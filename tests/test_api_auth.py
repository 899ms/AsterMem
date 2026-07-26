"""
Authentication Boundary Tests

Background: AsterMem is a self-hosted service; the Web management channel (cookie session)
and the AI channel (Bearer API Token) must be isolated and independently revocable.
There is only one admin account with default credentials admin / admin; users can change
credentials or disable login protection entirely.
Design intent: Verify 401 when unauthenticated, access after login, agent channel rejects
session cookies, token revocation takes effect immediately, and credential change / login
protection toggle safeguards.
Key constraint: api.py service instances are module-level singletons; tests that change
credentials or toggle protection must restore original state in finally blocks (and re-login
the client), otherwise subsequent tests in the same session will be polluted.
"""

import io
import zipfile

from conftest import TEST_PASSWORD


def test_unauthenticated_api_returns_401(anon_client):
    for path in ("/api/config", "/api/memories", "/api/stats", "/api/tokens"):
        resp = anon_client.get(path)
        assert resp.status_code == 401, f"{path} -> {resp.status_code}"


def test_login_wrong_password_rejected(anon_client):
    resp = anon_client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        assert not resp.json().get("success")


def test_login_username_defaults_to_admin(app_bundle):
    # Single-user service: when username is omitted, defaults to admin
    # Use a separate TestClient to avoid leaving cookies on the shared anon_client
    from starlette.testclient import TestClient

    resp = TestClient(app_bundle[0]).post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert resp.status_code == 200
    assert resp.json().get("success") is True


def test_auth_check_after_login(client):
    resp = client.get("/api/auth/check")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("authenticated") is True
    assert body.get("login_required") is True
    assert body.get("username") == "admin"


def test_auth_check_hides_identity_when_anonymous(anon_client):
    body = anon_client.get("/api/auth/check").json()
    assert body.get("authenticated") is False
    assert "username" not in body
    assert "must_change_credentials" not in body


def test_update_credentials_requires_current_password(client):
    resp = client.post("/api/auth/credentials",
                       json={"current_password": "wrong", "new_password": "another-password"})
    assert resp.status_code == 401


def test_update_credentials_rejects_weak_input(client):
    short_pw = client.post("/api/auth/credentials",
                           json={"current_password": TEST_PASSWORD, "new_password": "abc"})
    assert short_pw.status_code == 400

    bad_name = client.post("/api/auth/credentials",
                           json={"current_password": TEST_PASSWORD, "username": "a"})
    assert bad_name.status_code == 400


def test_credentials_roundtrip(client, app_bundle):
    """After changing username + password, old credentials fail, new ones work, then restore"""
    from starlette.testclient import TestClient

    app = app_bundle[0]
    new_username, new_password = "owner", "brand-new-pass"

    resp = client.post("/api/auth/credentials", json={
        "current_password": TEST_PASSWORD,
        "username": new_username,
        "new_password": new_password,
    })
    assert resp.status_code == 200, resp.text

    try:
        probe = TestClient(app)
        assert probe.post("/api/auth/login",
                          json={"username": "admin", "password": TEST_PASSWORD}).status_code == 401
        assert probe.post("/api/auth/login",
                          json={"username": new_username, "password": new_password}).status_code == 200
    finally:
        restore = client.post("/api/auth/credentials", json={
            "current_password": new_password,
            "username": "admin",
            "new_password": TEST_PASSWORD,
        })
        assert restore.status_code == 200, restore.text


def test_login_protection_can_be_disabled_and_restored(client, app_bundle):
    """After disabling login protection anonymous access works, re-enabling restores 401"""
    from starlette.testclient import TestClient

    app, config = app_bundle[0], app_bundle[1]
    anon = TestClient(app)

    off = client.post("/api/auth/login-protection",
                      json={"enabled": False, "current_password": TEST_PASSWORD})
    assert off.status_code == 200, off.text

    try:
        assert config["auth"]["login_required"] is False
        assert anon.get("/api/stats").status_code == 200
        assert anon.get("/api/auth/check").json().get("login_required") is False
    finally:
        on = client.post("/api/auth/login-protection",
                         json={"enabled": True, "current_password": TEST_PASSWORD})
        assert on.status_code == 200, on.text
        # Disabling clears sessions; after re-enabling, re-login the shared client
        client.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})

    assert TestClient(app).get("/api/stats").status_code == 401
    assert client.get("/api/stats").status_code == 200


def test_agent_call_requires_bearer_token(client, api_token):
    # Session cookie alone is insufficient for agent channel access
    resp = client.post("/api/agent/call", json={"tool": "get_memory_stats", "arguments": {}},
                       headers={"Authorization": ""})
    assert resp.status_code == 401

    resp = client.post("/api/agent/call", json={"tool": "get_memory_stats", "arguments": {}},
                       headers={"Authorization": f"Bearer {api_token}"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_agent_call_unknown_tool_404(client, api_token):
    resp = client.post("/api/agent/call", json={"tool": "no_such_tool", "arguments": {}},
                       headers={"Authorization": f"Bearer {api_token}"})
    assert resp.status_code == 404


def test_agent_can_read_and_configure_provider(client, api_token, app_bundle):
    """Agent can manage provider settings; read results keep API keys redacted."""
    headers = {"Authorization": f"Bearer {api_token}"}
    config = app_bundle[1]
    original_chat_model = config["providers"]["lmstudio"].get("chat_model")
    original_chat_provider = config["active"].get("chat_provider")

    try:
        changed = client.post("/api/agent/call", headers=headers, json={
            "tool": "configure_provider",
            "arguments": {
                "provider_id": "lmstudio",
                "chat_model": "pytest-chat-model",
                "use_for_chat": True,
            },
        })
        assert changed.status_code == 200, changed.text
        result = changed.json()["result"]
        assert result["requires_vector_rebuild"] is False
        assert result["config"]["providers"]["lmstudio"]["chat_model"] == "pytest-chat-model"
        assert result["config"]["active"]["chat_provider"] == "lmstudio"
        assert "api_key" not in result["config"]["providers"]["lmstudio"]
    finally:
        restore = {
            "provider_id": "lmstudio",
            "chat_model": original_chat_model,
            "use_for_chat": original_chat_provider == "lmstudio",
        }
        client.post("/api/agent/call", headers=headers,
                    json={"tool": "configure_provider", "arguments": restore})
        if original_chat_provider != "lmstudio":
            config["active"]["chat_provider"] = original_chat_provider


def test_agent_vector_rebuild_requires_confirmation(client, api_token):
    resp = client.post("/api/agent/call",
                       headers={"Authorization": f"Bearer {api_token}"},
                       json={"tool": "rebuild_vector_index", "arguments": {}})
    assert resp.status_code == 400
    assert "confirm=true" in resp.text


def test_api_token_scopes_gate_agent_and_rest(client):
    created = client.post("/api/tokens", json={"name": "read-only", "scopes": ["read"]})
    token = created.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    tokens = client.get("/api/tokens").json()["tokens"]
    token_id = next(item["id"] for item in tokens if item["name"] == "read-only")

    try:
        assert client.get("/api/stats", headers=headers).status_code == 200
        assert client.post("/api/agent/call", headers=headers, json={
            "tool": "get_memory_stats", "arguments": {},
        }).status_code == 200
        assert client.post("/api/agent/call", headers=headers, json={
            "tool": "add_memory",
            "arguments": {"title": "denied", "content": "denied"},
        }).status_code == 403
        assert client.put("/api/config", headers=headers, json={}).status_code == 403
        assert client.get("/api/tokens", headers=headers).status_code == 403
    finally:
        client.delete(f"/api/tokens/{token_id}")


def test_admin_token_cannot_escalate_its_own_scopes(client):
    created = client.post("/api/tokens", json={
        "name": "admin-no-destructive",
        "scopes": ["read", "write", "config", "admin"],
    })
    token = created.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    tokens = client.get("/api/tokens").json()["tokens"]
    token_id = next(item["id"] for item in tokens if item["name"] == "admin-no-destructive")

    try:
        assert client.get("/api/tokens", headers=headers).status_code == 200
        escalated = client.post("/api/tokens", headers=headers, json={
            "name": "must-not-exist",
            "scopes": ["read", "destructive"],
        })
        assert escalated.status_code == 403
    finally:
        client.delete(f"/api/tokens/{token_id}")


def test_destructive_token_requires_exact_confirmation_header(client):
    created = client.post("/api/tokens", json={
        "name": "destructive-guard",
        "scopes": ["destructive"],
    })
    token = created.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    tokens = client.get("/api/tokens").json()["tokens"]
    token_id = next(item["id"] for item in tokens if item["name"] == "destructive-guard")

    try:
        resp = client.delete("/api/logs", headers=headers)
        assert resp.status_code == 428
        assert "X-AsterMem-Confirm" in resp.text
    finally:
        client.delete(f"/api/tokens/{token_id}")


def test_skill_package_download(client):
    resp = client.get("/api/skill/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        names = set(archive.namelist())
    assert "astermem/SKILL.md" in names
    assert "astermem/scripts/astermem.sh" in names
    assert "astermem/scripts/astermem.ps1" in names


def test_token_revocation(client):
    resp = client.post("/api/tokens", json={"name": "revoke-me"})
    token = resp.json()["token"]
    # Create response returns the full token only once; id must be fetched from the list
    tokens = client.get("/api/tokens").json().get("tokens", [])
    token_id = next(t["id"] for t in tokens if t["name"] == "revoke-me")

    ok = client.post("/api/agent/call", json={"tool": "get_memory_stats", "arguments": {}},
                     headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200

    revoke = client.post(f"/api/tokens/{token_id}/revoke")
    assert revoke.status_code == 200

    denied = client.post("/api/agent/call", json={"tool": "get_memory_stats", "arguments": {}},
                         headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 401
