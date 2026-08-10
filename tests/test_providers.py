"""
Provider Registry Unit Tests

Background: providers.py is the refactoring core (four upstream types converge into two
protocol adapters + a config registry); schema migration and key resolution errors cause
semantic search to silently fail or leak secrets.
Design intent: No real network requests — protocol adapters use a fake httpx.Client to
verify request assembly; registry logic is validated purely in-memory.
"""

import os

import httpx
import pytest

from memory import providers
from memory.providers import (
    PROVIDER_CATALOG,
    AnthropicChat,
    GeminiEmbedding,
    OpenAICompatibleChat,
    OpenAICompatibleEmbedding,
    _classify_error,
    _generate_tags_via_chat,
    get_chat_model,
    get_embedding_model,
    normalize_config,
    resolve_api_key,
)


# ---------- Schema Migration ----------

def test_normalize_config_creates_registry_and_active():
    config = {}
    changed = normalize_config(config)
    assert changed is True
    assert set(config["providers"].keys()) == {"builtin", "lmstudio"}
    # A fresh install selects nothing. Defaulting to lmstudio aimed every model call at
    # localhost:1234, which only answers on a machine already running LM Studio, so a server
    # install failed chat and indexing without naming the provider at fault.
    assert config["active"] == {"embedding_provider": "", "chat_provider": ""}


def test_normalize_config_migrates_old_schema():
    """Legacy config (model.mode=bailian + custom local section) should migrate while preserving user changes"""
    config = {
        "model": {
            "mode": "bailian",
            "local": {"base_url": "http://192.168.1.9:1234/v1", "embedding_model": "custom-embed",
                      "chat_model": "custom-chat", "vlm_model": "custom-vlm"},
        }
    }
    normalize_config(config)
    assert config["active"]["embedding_provider"] == "dashscope"
    assert "dashscope" in config["providers"]
    lp = config["providers"]["lmstudio"]
    assert lp["base_url"] == "http://192.168.1.9:1234/v1"
    assert lp["embedding_model"] == "custom-embed"
    assert lp["vlm_model"] == "custom-vlm"


def test_normalize_config_idempotent():
    config = {}
    normalize_config(config)
    snapshot = repr(config)
    assert normalize_config(config) is False
    assert repr(config) == snapshot


def test_default_asterove_provider_present():
    entry = PROVIDER_CATALOG["asterove"]
    assert entry["api_type"] == "openai_compatible"
    assert entry["base_url"] == "https://asterove.com/api/v1"
    assert entry["api_key_env"] == "ASTEROVE_API_KEY"
    # The chat alias is refused on /embeddings, so reusing it here left semantic search dead for
    # everyone who added this provider.
    assert entry["embedding_model"] != entry["chat_model"]
    assert entry["embedding_model"] == "dashscope/text-embedding-v4"


def test_broken_asterove_embedding_model_is_repaired_on_upgrade():
    """
    asterove/standard shipped as the embedding model but is a chat alias the gateway rejects, so it
    never produced a vector. Rewriting it therefore discards nothing an upgrading user still needs.
    """
    config = {
        "providers": {"asterove": dict(PROVIDER_CATALOG["asterove"],
                                       embedding_model="asterove/standard")},
        "active": {"embedding_provider": "asterove", "chat_provider": "asterove"},
        "provider_catalog_version": 3,
    }

    assert normalize_config(config) is True
    assert config["providers"]["asterove"]["embedding_model"] == "dashscope/text-embedding-v4"
    assert normalize_config(config) is False


def test_self_chosen_asterove_embedding_model_survives_the_repair():
    config = {
        "providers": {"asterove": dict(PROVIDER_CATALOG["asterove"],
                                       embedding_model="my/own-embedding")},
        "active": {"embedding_provider": "asterove", "chat_provider": "asterove"},
        "provider_catalog_version": 3,
    }
    normalize_config(config)

    assert config["providers"]["asterove"]["embedding_model"] == "my/own-embedding"


def test_catalog_includes_anthropic_and_grok():
    assert PROVIDER_CATALOG["anthropic"]["api_type"] == "anthropic"
    assert PROVIDER_CATALOG["xai"]["chat_model"] == "grok-4"


# ---------- Key Resolution ----------

def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("TEST_ASTERMEM_KEY", "sk-abc")
    assert resolve_api_key({"api_key_env": "TEST_ASTERMEM_KEY"}) == "sk-abc"


def test_resolve_api_key_empty_env_name():
    assert resolve_api_key({"api_key_env": ""}) == ""
    assert resolve_api_key({}) == ""


# ---------- Factory ----------

def test_get_embedding_model_returns_correct_adapter():
    config = {}
    normalize_config(config)
    config["active"]["embedding_provider"] = "lmstudio"
    model = get_embedding_model(config)
    assert isinstance(model, OpenAICompatibleEmbedding)

    config["active"]["embedding_provider"] = "builtin"
    model = get_embedding_model(config)
    assert isinstance(model, providers.LocalONNXEmbedding)

    config["providers"]["google"] = dict(PROVIDER_CATALOG["google"])
    config["active"]["embedding_provider"] = "google"
    model = get_embedding_model(config)
    assert isinstance(model, GeminiEmbedding)


def test_get_embedding_model_unknown_provider_returns_none():
    config = {}
    normalize_config(config)
    config["active"]["embedding_provider"] = "nonexistent"
    assert get_embedding_model(config) is None


def test_get_chat_model_none_when_no_chat_model():
    config = {}
    normalize_config(config)
    config["active"]["chat_provider"] = "lmstudio"
    config["providers"]["lmstudio"]["chat_model"] = ""
    assert get_chat_model(config) is None


# ---------- Protocol Adapters (Fake HTTP) ----------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Fake httpx.Client that records requests and returns preset responses"""

    last_request = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        _FakeClient.last_request = {"url": url, "headers": headers or {}, "json": json or {}}
        if "/embeddings" in url:
            inputs = json.get("input")
            if isinstance(inputs, list):
                data = [{"index": i, "embedding": [0.1, 0.2]} for i in range(len(inputs))]
            else:
                data = [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]
            return _FakeResponse({"data": data})
        if ":embedContent" in url:
            return _FakeResponse({"embedding": {"values": [0.5] * 8}})
        if "/chat/completions" in url:
            return _FakeResponse({"choices": [{"message": {"content": "tech/testing, life/example"}}]})
        if "/messages" in url:
            return _FakeResponse({"content": [{"type": "text", "text": "anthropic ok"}]})
        if ":generateContent" in url:
            return _FakeResponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
        return _FakeResponse({}, 404)

    def get(self, url, **kwargs):
        return _FakeResponse({"data": []})


@pytest.fixture()
def fake_http(monkeypatch):
    monkeypatch.setattr(providers.httpx, "Client", _FakeClient)
    _FakeClient.last_request = None
    return _FakeClient


def test_openai_embedding_request_shape(fake_http):
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1/", "google/gemini-embedding-001", "sk-test", "Asterove")
    vec = emb.embed("hello")
    assert vec == [0.1, 0.2, 0.3]
    req = fake_http.last_request
    assert req["url"] == "https://asterove.com/api/v1/embeddings"
    assert req["headers"]["Authorization"] == "Bearer sk-test"
    assert req["json"] == {"model": "google/gemini-embedding-001", "input": "hello"}


def test_openai_embedding_no_key_omits_auth_header(fake_http):
    emb = OpenAICompatibleEmbedding("http://localhost:1234/v1", "local-model", "", "LM Studio")
    emb.embed("hello")
    assert "Authorization" not in fake_http.last_request["headers"]


def test_openai_embedding_batch_chunks(fake_http):
    emb = OpenAICompatibleEmbedding("http://x/v1", "m", "")
    texts = [f"t{i}" for i in range(45)]  # BATCH_SIZE=20 → 3 batches
    vectors = emb.embed_batch(texts)
    assert len(vectors) == 45


def test_gemini_embedding_request_shape(fake_http):
    emb = GeminiEmbedding("https://generativelanguage.googleapis.com/v1beta",
                          "models/gemini-embedding-001", "gk-1")
    vec = emb.embed("hi")
    assert len(vec) == 8
    req = fake_http.last_request
    assert ":embedContent" in req["url"]
    assert req["headers"]["x-goog-api-key"] == "gk-1"


def test_gemini_embedding_requires_key():
    emb = GeminiEmbedding("https://x/v1beta", "models/m", "")
    with pytest.raises(ValueError):
        emb.embed("hi")


class _FailingClient(_FakeClient):
    """Rejects the first N calls the way httpx does, then behaves like _FakeClient."""

    calls = 0
    failures = 0
    status = 429
    headers: dict = {}
    body = ""

    @classmethod
    def reset(cls, failures, status=429, headers=None, body=""):
        cls.calls = 0
        cls.failures = failures
        cls.status = status
        cls.headers = headers or {}
        cls.body = body

    def post(self, url, headers=None, json=None):
        _FailingClient.calls += 1
        if _FailingClient.calls <= _FailingClient.failures:
            request = httpx.Request("POST", url)
            response = httpx.Response(_FailingClient.status, headers=_FailingClient.headers,
                                      request=request, text=_FailingClient.body)
            raise httpx.HTTPStatusError("upstream refused", request=request, response=response)
        return super().post(url, headers=headers, json=json)


@pytest.fixture()
def failing_http(monkeypatch):
    """Fake transport plus captured sleeps, so backoff is asserted without real waiting."""
    waits: list[float] = []
    monkeypatch.setattr(providers.httpx, "Client", _FailingClient)
    monkeypatch.setattr(providers.time, "sleep", waits.append)
    return _FailingClient, waits


def test_embedding_retries_through_rate_limit(failing_http):
    client, waits = failing_http
    client.reset(failures=2)
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    assert emb.embed("hello") == [0.1, 0.2, 0.3]
    assert client.calls == 3
    assert len(waits) == 2


def test_embedding_honors_retry_after_hint(failing_http):
    client, waits = failing_http
    client.reset(failures=1, headers={"Retry-After": "7"})
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    emb.embed("hello")
    assert waits == [7.0]


def test_embedding_gives_up_after_max_retries(failing_http):
    client, waits = failing_http
    client.reset(failures=99)
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    with pytest.raises(ConnectionError):
        emb.embed("hello")
    assert client.calls == 3


def test_embedding_does_not_retry_rejected_key(failing_http):
    client, waits = failing_http
    client.reset(failures=99, status=401)
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    with pytest.raises(ConnectionError):
        emb.embed("hello")
    assert client.calls == 1
    assert waits == []


def test_embedding_batch_retries_per_batch(failing_http):
    client, waits = failing_http
    client.reset(failures=1)
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    assert len(emb.embed_batch([f"t{i}" for i in range(25)])) == 25
    assert client.calls == 3  # first batch rejected once, then both batches succeed


def test_gemini_embedding_retries_through_rate_limit(failing_http):
    client, waits = failing_http
    client.reset(failures=1)
    emb = GeminiEmbedding("https://generativelanguage.googleapis.com/v1beta", "models/m", "gk-1")
    assert len(emb.embed("hi")) == 8
    assert client.calls == 2


# ---------- Inputs the endpoint would refuse ----------
#
# An embedding call that fails leaves the memory stored and searchable by keyword, so nothing looks
# broken until someone notices it never comes back from semantic search. These two inputs fail that
# way permanently, unlike an outage, which the retries above already cover.

def test_an_input_past_the_window_is_brought_inside_it(fake_http):
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    emb.embed("长" * 40000)
    assert len(fake_http.last_request["json"]["input"]) == providers._EMBEDDING_MAX_CHARS


def test_input_within_the_window_is_sent_untouched(fake_http):
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    text = "字" * (providers._EMBEDDING_MAX_CHARS - 1)
    emb.embed(text)
    assert fake_http.last_request["json"]["input"] == text


@pytest.mark.parametrize("blank", ["", "   ", "\n\n"])
def test_a_blank_input_is_sent_as_something_acceptable(fake_http, blank):
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    assert emb.embed(blank) == [0.1, 0.2, 0.3]
    assert fake_http.last_request["json"]["input"].strip() == ""
    assert fake_http.last_request["json"]["input"] != ""


def test_one_oversized_member_does_not_take_its_batch_down(fake_http):
    """A rejected request loses every vector in it, not just the offending one."""
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    texts = ["fine"] * 9 + ["长" * 40000]
    assert len(emb.embed_batch(texts)) == 10
    assert all(len(sent) <= providers._EMBEDDING_MAX_CHARS
               for sent in fake_http.last_request["json"]["input"])


def test_gemini_is_held_to_its_own_smaller_window(fake_http):
    """Google's embedding models take 2048 tokens, a quarter of what the others accept."""
    emb = GeminiEmbedding("https://generativelanguage.googleapis.com/v1beta", "models/m", "gk-1")
    emb.embed("长" * 40000)
    sent = fake_http.last_request["json"]["content"]["parts"][0]["text"]
    assert len(sent) == providers._GEMINI_MAX_CHARS
    assert providers._GEMINI_MAX_CHARS < providers._EMBEDDING_MAX_CHARS


def test_a_refusal_is_reported_with_what_the_upstream_said(failing_http):
    """
    httpx summarises a 400 as the status line and the url, which is the same text whatever the
    upstream objected to. The reply body is the only copy of the reason, and dropping it is what
    made a reproducible rejection look like an unexplained gap in the index.
    """
    client, _ = failing_http
    client.reset(failures=99, status=400,
                 body='{"error":{"message":"Range of input length should be [1, 8192]"}}')
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    with pytest.raises(ConnectionError, match=r"Range of input length"):
        emb.embed("hello")


def test_a_refusal_without_a_body_keeps_the_original_message(failing_http):
    """Not every upstream explains itself; the transport's own description is what is left."""
    client, _ = failing_http
    client.reset(failures=99, status=400)
    emb = OpenAICompatibleEmbedding("https://asterove.com/api/v1", "m", "sk", "Asterove")
    with pytest.raises(ConnectionError, match=r"upstream refused"):
        emb.embed("hello")


def test_chat_generate_tags_parses_result(fake_http):
    chat = OpenAICompatibleChat("http://x/v1", "m", "")
    tags = _generate_tags_via_chat(chat, "title", "content")
    assert tags == ["tech/testing", "life/example"]


def test_anthropic_chat_request_shape(fake_http):
    chat = AnthropicChat("https://api.anthropic.com/v1", "claude-sonnet-4-6", "sk-ant-test")
    result = chat.chat([
        {"role": "system", "content": "Be concise"},
        {"role": "user", "content": "Hi"},
    ], max_retries=1)
    assert result == "anthropic ok"
    req = fake_http.last_request
    assert req["url"] == "https://api.anthropic.com/v1/messages"
    assert req["headers"]["x-api-key"] == "sk-ant-test"
    assert req["json"]["system"] == "Be concise"


# ---------- Error Classification ----------

def test_classify_error():
    assert "invalid" in _classify_error("HTTP 401 Unauthorized")
    assert "balance" in _classify_error("status 402")
    assert "Rate limited" in _classify_error("429 Too Many Requests")
    assert "timeout" in _classify_error("connect timeout").lower()
    assert _classify_error("weird failure").startswith("Connection failed")


def test_fresh_install_does_not_activate_the_downloading_provider():
    """
    The built-in model fetches ~80MB on first use. Making it the default would mean an offline or
    air-gapped instance reaches for the network on its own and fails at indexing with no
    explanation, so it stays available but unselected.
    """
    config = {}
    providers.normalize_config(config)

    assert config["active"]["embedding_provider"] != "builtin"
    assert "builtin" in config["providers"], "still offered, just not chosen for the user"


def test_migrated_install_keeps_its_embedding_provider():
    """
    Switching a provider changes vector dimensions, so moving an upgrading user onto a different
    model would invalidate their vector store and break every search until a reindex.
    """
    config = {"model": {"mode": "bailian"}}
    providers.normalize_config(config)

    assert config["active"]["embedding_provider"] == "dashscope"


def test_configured_install_is_left_alone():
    config = {
        "providers": {"openrouter": {"name": "OR", "base_url": "x", "embedding_model": "m"}},
        "active": {"embedding_provider": "openrouter", "chat_provider": "openrouter"},
    }
    providers.normalize_config(config)

    assert config["active"]["embedding_provider"] == "openrouter"


def test_fresh_install_selects_no_provider_at_all():
    """
    lmstudio used to be the fallback, which pointed chat and embedding at http://localhost:1234.
    That address only answers where LM Studio already runs, so every server install failed model
    calls with a bare connection error and never built a vector store.
    """
    config = {}
    providers.normalize_config(config)

    assert config["active"] == {"embedding_provider": "", "chat_provider": ""}
    assert providers.get_embedding_model(config) is None
    assert providers.get_chat_model(config) is None


def test_fresh_install_keeps_both_defaults_offered():
    """
    The v2 cleanup drops legacy auto-expanded cards that are unselected, unkeyed and unmodified —
    which now describes the freshly seeded defaults themselves. A new config is stamped at the
    current version so that cleanup cannot delete the providers the settings page offers.
    """
    config = {}
    providers.normalize_config(config)

    assert set(config["providers"]) == {"builtin", "lmstudio"}
    assert config["provider_catalog_version"] == providers.PROVIDER_CATALOG_VERSION


def test_active_selection_pointing_at_a_removed_provider_is_cleared():
    """
    The settings page resends the selection it loaded, so a dangling id makes every save fail as an
    unknown provider — the one screen that could repair the choice cannot save. Clearing it on load
    lets an instance already in that state recover by restarting.
    """
    config = {
        "providers": {"builtin": dict(PROVIDER_CATALOG["builtin"])},
        "active": {"embedding_provider": "lmstudio", "chat_provider": "lmstudio"},
        "provider_catalog_version": providers.PROVIDER_CATALOG_VERSION,
    }

    assert providers.normalize_config(config) is True
    assert config["active"] == {"embedding_provider": "", "chat_provider": ""}
    assert providers.normalize_config(config) is False


def test_legacy_upgrade_still_activates_its_migrated_provider():
    """The empty default applies only to fresh configs; an upgrade must keep working as before."""
    config = {"model": {"mode": "local"}}
    providers.normalize_config(config)

    assert config["active"] == {"embedding_provider": "lmstudio", "chat_provider": "lmstudio"}


def test_built_in_embedding_loads_the_model_lazily():
    """
    The factory builds an adapter every time config is read, so loading weights in the constructor
    would cost ~100MB of RSS on instances that never use this provider.
    """
    model = providers.LocalONNXEmbedding()

    assert model._encoder is None


def test_built_in_embedding_returns_plain_floats():
    """
    The ONNX encoder returns numpy arrays. list() on one yields float32 scalars, which the chroma
    client rejects on add, so every vector was silently dropped from the index.
    """
    numpy = pytest.importorskip("numpy")
    model = providers.LocalONNXEmbedding()
    model._encoder = lambda texts: numpy.zeros((len(texts), 4), dtype=numpy.float32)

    vectors = model.embed_batch(["text"])

    assert all(type(value) is float for value in vectors[0])


def test_built_in_embedding_survives_blank_input():
    """Callers do pass empty trunks through, and the ONNX runtime rejects empty strings."""
    model = providers.LocalONNXEmbedding()
    calls = []

    def fake_encoder(texts):
        calls.append(texts)
        return [[0.0] * 384 for _ in texts]

    model._encoder = fake_encoder
    vectors = model.embed_batch(["", "   ", "real text"])

    assert len(vectors) == 3
    assert all(text.strip() or text == " " for text in calls[0])
