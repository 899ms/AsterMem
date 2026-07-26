"""
Provider Registry Unit Tests

Background: providers.py is the refactoring core (four upstream types converge into two
protocol adapters + a config registry); schema migration and key resolution errors cause
semantic search to silently fail or leak secrets.
Design intent: No real network requests — protocol adapters use a fake httpx.Client to
verify request assembly; registry logic is validated purely in-memory.
"""

import os

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
    assert set(config["providers"].keys()) == {"lmstudio"}
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
