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
    assert config["active"]["embedding_provider"] == "lmstudio"


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

    @classmethod
    def reset(cls, failures, status=429, headers=None):
        cls.calls = 0
        cls.failures = failures
        cls.status = status
        cls.headers = headers or {}

    def post(self, url, headers=None, json=None):
        _FailingClient.calls += 1
        if _FailingClient.calls <= _FailingClient.failures:
            request = httpx.Request("POST", url)
            response = httpx.Response(_FailingClient.status, headers=_FailingClient.headers,
                                      request=request)
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
