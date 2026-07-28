"""
Config / Provider Registry API Tests

Background: The settings page depends on the new GET/PUT /api/config schema; key handling
is a security red line (plaintext keys only go into .env, never echoed back).
Design intent: Verify registry view completeness, zero key leakage, partial merge whitelist,
api_keys written to .env, and invalid provider returns 400.
"""

import os

from memory.recall import MAX_NOISE_FLOOR


def test_get_config_registry_shape(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert set(cfg["providers"].keys()) >= {"lmstudio"}
    assert {"anthropic", "xai", "openai", "dashscope"} <= set(cfg["provider_catalog"])
    for entry in cfg["providers"].values():
        assert {"name", "api_type", "base_url", "embedding_model", "chat_model", "has_api_key"} <= set(entry)
    assert "embedding_provider" in cfg["active"]
    assert "semantic" in cfg["search"]


def test_get_config_never_leaks_keys(client, monkeypatch):
    assert client.put("/api/config", json={"add_providers": ["dashscope"]}).status_code == 200
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-super-secret-value")
    resp = client.get("/api/config")
    assert "sk-super-secret-value" not in resp.text
    assert resp.json()["providers"]["dashscope"]["has_api_key"] is True


def test_put_config_partial_provider_merge(client):
    resp = client.put("/api/config", json={
        "providers": {"lmstudio": {"base_url": "http://127.0.0.1:9999/v1", "evil_field": "x"}},
    })
    assert resp.status_code == 200

    cfg = client.get("/api/config").json()
    lp = cfg["providers"]["lmstudio"]
    assert lp["base_url"] == "http://127.0.0.1:9999/v1"
    assert "evil_field" not in lp  # Fields outside whitelist are discarded
    assert lp["embedding_model"]   # Unsubmitted fields retain original values


def test_put_config_active_switch_and_validation(client):
    assert client.put("/api/config", json={"add_providers": ["asterove"]}).status_code == 200
    resp = client.put("/api/config", json={"active": {"chat_provider": "asterove"}})
    assert resp.status_code == 200
    assert client.get("/api/config").json()["active"]["chat_provider"] == "asterove"

    resp = client.put("/api/config", json={"active": {"chat_provider": "ghost"}})
    assert resp.status_code == 400


def test_put_config_api_key_writes_env(client, app_bundle):
    _app, _config, config_path, _services = app_bundle
    assert client.put("/api/config", json={"add_providers": ["openrouter"]}).status_code == 200
    resp = client.put("/api/config", json={"api_keys": {"openrouter": "sk-or-test-123"}})
    assert resp.status_code == 200

    env_path = os.path.join(os.path.dirname(config_path), ".env")
    with open(env_path) as f:
        content = f.read()
    assert "OPENROUTER_API_KEY=sk-or-test-123" in content
    assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-test-123"


def test_put_config_api_key_unknown_provider(client):
    resp = client.put("/api/config", json={"api_keys": {"ghost": "sk-x"}})
    assert resp.status_code == 400


def test_put_config_keyless_provider_rejects_key(client):
    resp = client.put("/api/config", json={"api_keys": {"lmstudio": "sk-x"}})
    assert resp.status_code == 400


def test_add_and_remove_provider(client):
    resp = client.put("/api/config", json={"add_providers": ["anthropic"]})
    assert resp.status_code == 200
    assert "anthropic" in client.get("/api/config").json()["providers"]

    resp = client.put("/api/config", json={"remove_providers": ["anthropic"]})
    assert resp.status_code == 200
    assert "anthropic" not in client.get("/api/config").json()["providers"]


def test_cannot_remove_active_provider(client):
    # Reads the active provider rather than naming one: the client fixture is session-scoped, so
    # hardcoding an id couples this to the default registry and to whatever earlier tests left set.
    active = client.get("/api/config").json()["active"]
    resp = client.put("/api/config", json={"remove_providers": [active["embedding_provider"]]})
    assert resp.status_code == 400

    resp = client.put("/api/config", json={"remove_providers": [active["chat_provider"]]})
    assert resp.status_code == 400


def test_remove_active_provider_after_switching_and_keep_key(client, monkeypatch):
    assert client.put("/api/config", json={"add_providers": ["asterove"]}).status_code == 200
    monkeypatch.setenv("ASTEROVE_API_KEY", "sk-keep-after-remove")
    assert client.put("/api/config", json={
        "active": {
            "embedding_provider": "asterove",
            "chat_provider": "asterove",
        },
    }).status_code == 200
    resp = client.put("/api/config", json={
        "active": {
            "embedding_provider": "lmstudio",
            "chat_provider": "lmstudio",
        },
        "remove_providers": ["asterove"],
    })
    assert resp.status_code == 200
    cfg = client.get("/api/config").json()
    assert "asterove" not in cfg["providers"]
    assert cfg["active"] == {
        "embedding_provider": "lmstudio",
        "chat_provider": "lmstudio",
    }
    assert os.environ["ASTEROVE_API_KEY"] == "sk-keep-after-remove"


def test_semantic_settings_roundtrip(client):
    resp = client.put("/api/config", json={"min_similarity": 0.25})
    assert resp.status_code == 200
    search_cfg = client.get("/api/config").json()["search"]["semantic"]
    assert search_cfg["min_similarity"] == 0.25
    assert search_cfg["min_similarity_max"] == MAX_NOISE_FLOOR


def test_semantic_threshold_above_safe_range_rejected(client):
    """A noise floor that is too high zeroes out all semantic recall; the API must reject it outright."""
    resp = client.put("/api/config", json={"min_similarity": MAX_NOISE_FLOOR + 0.1})
    assert resp.status_code == 400


def test_provider_test_endpoint_unknown(client):
    resp = client.post("/api/providers/ghost/test")
    assert resp.status_code == 200
    assert resp.json()["success"] is False
