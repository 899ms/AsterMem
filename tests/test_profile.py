"""
Profile Layer Fast-Cycle Tests (PRD_UserProfile v0.3)

Coverage: L1/L2 field layer read/write and isolation, manual.md, daily distillation
(including rejection of unsourced claims and source-text anchoring), semantic review
fail-closed behavior, traceback (stale / user resolution), get_profile rendering and
Agent tool channel, enabled toggle.
All LLM calls use injected FakeChat; no real network requests.
"""

import json
import re

import pytest


class FakeChat:
    """Dispatch fixed JSON responses based on prompt markers; record all prompts for assertions"""

    def __init__(self):
        self.prompts = []
        self.distill_response = {"claims": []}
        self.suggest_response = {"suggestions": {}}

    def chat(self, messages, temperature=0.3, max_retries=3, **kwargs):
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        if "profile distiller" in prompt:
            return json.dumps(self.distill_response, ensure_ascii=False)
        if "Profile field suggestions" in prompt:
            return json.dumps(self.suggest_response, ensure_ascii=False)
        if "reviewer" in prompt:
            indexes = sorted({int(m) for m in re.findall(r"Claim (\d+):", prompt)})
            return json.dumps({"verdicts": [
                {"index": i, "supported": True, "reason": "supported by source"} for i in indexes
            ]}, ensure_ascii=False)
        return "{}"

    def is_available(self):
        return True


@pytest.fixture()
def profile_env(app_bundle):
    """Enable profile + inject FakeChat + stop background scheduler to avoid interference"""
    _app, config, _path, services = app_bundle
    scheduler = services.get("profile_scheduler")
    if scheduler:
        scheduler.stop()
    fake = FakeChat()
    svc = services["profile_service"]
    dream = services["dream_manager"]
    old_cfg = config.get("profile")
    config["profile"] = {
        "enabled": True,
        "daily_hour": 3,
        "distill": {"max_memories": 20, "per_source_chars": 2000},
        "audit": {"batch_size": 50, "aging_days": 30},
        "dream": {"min_interval_days": 7, "trigger": {"new_claims": 9999, "pending_issues": 9999}},
    }
    old_factories = (svc._chat_factory, svc.auditor._chat_factory,
                     dream._chat_factory, dream.auditor._chat_factory)
    svc._chat_factory = lambda: fake
    svc.auditor._chat_factory = lambda: fake
    dream._chat_factory = lambda: fake
    dream.auditor._chat_factory = lambda: fake
    yield services, fake, config
    (svc._chat_factory, svc.auditor._chat_factory,
     dream._chat_factory, dream.auditor._chat_factory) = old_factories
    if old_cfg is None:
        config.pop("profile", None)
    else:
        config["profile"] = old_cfg


def _add_memory(client, title, content, tags=None):
    resp = client.post("/api/memories", json={
        "title": title, "content": content, "tags": tags or ["test"], "priority": 5,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["memory"]["id"]


# ---------- L1/L2 Field Layer ----------

def test_fields_schema_and_update(client, profile_env):
    resp = client.get("/api/profile/fields")
    assert resp.status_code == 200
    data = resp.json()
    keys = {f["key"] for f in data["schema"]}
    assert {"nickname", "gender", "language", "timezone"} <= keys
    assert "nickname" in data["missing_required"]

    resp = client.put("/api/profile/fields", json={"values": {
        "nickname": "Alex", "gender": "male", "language": "English", "timezone": "Asia/Shanghai",
    }})
    assert resp.status_code == 200
    assert resp.json()["missing_required"] == []
    assert resp.json()["values"]["nickname"] == "Alex"

    # Unknown fields rejected: AI or misuse cannot inject arbitrary data via this endpoint
    resp = client.put("/api/profile/fields", json={"values": {"hacked": "x"}})
    assert resp.status_code == 400


def test_fields_ai_autofill(client, profile_env):
    """AI auto-fills fields and persists directly; user-edited fields are not overwritten; unknown fields are filtered"""
    services, fake, _config = profile_env
    _add_memory(client, "\u81ea\u6211\u4ecb\u7ecd", "\u6211\u662f\u4ea7\u54c1\u7ecf\u7406\uff0c\u5e38\u9a7b\u4e0a\u6d77\uff0c\u6700\u8fd1\u5728\u505a\u8bb0\u5fc6\u7cfb\u7edf\u3002")
    # User manually fills location first; once locked, AI cannot overwrite it
    client.put("/api/profile/fields", json={"values": {"location": "\u5317\u4eac"}})
    fake.suggest_response = {"suggestions": {
        "occupation": "\u4ea7\u54c1\u7ecf\u7406", "location": "\u4e0a\u6d77", "hacked": "\u4e0d\u8be5\u51fa\u73b0",
    }}
    resp = client.post("/api/profile/fields/autofill")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["applied"] == {"occupation": "\u4ea7\u54c1\u7ecf\u7406"}
    assert "location" in data["skipped_locked"]
    # Suggest prompt must include memory source text
    suggest_prompts = [p for p in fake.prompts if "Profile field suggestions" in p]
    assert suggest_prompts and "\u4ea7\u54c1\u7ecf\u7406" in suggest_prompts[-1]
    # AI-filled values are persisted directly with source=distilled; user-filled remain manual
    fields = client.get("/api/profile/fields").json()
    assert fields["values"]["occupation"] == "\u4ea7\u54c1\u7ecf\u7406"
    assert fields["values"]["location"] == "\u5317\u4eac"
    assert fields["sources"]["occupation"]["source"] == "distilled"
    assert fields["sources"]["location"]["source"] == "manual"


def test_fields_version_history(client, profile_env):
    """When field value changes, the old value is archived; user editing an AI-filled value locks it"""
    services, fake, _config = profile_env
    _add_memory(client, "\u81ea\u6211\u4ecb\u7ecd", "\u6211\u662f\u4ea7\u54c1\u7ecf\u7406\u3002")
    fake.suggest_response = {"suggestions": {"occupation": "\u4ea7\u54c1\u7ecf\u7406"}}
    client.post("/api/profile/fields/autofill")

    # User modifies AI-filled value: old value goes to history, source becomes manual
    client.put("/api/profile/fields", json={"values": {"occupation": "\u8d44\u6df1\u4ea7\u54c1\u7ecf\u7406"}})
    history = client.get("/api/profile/fields/history?key=occupation").json()["history"]
    assert len(history) == 1
    assert history[0]["value"] == "\u4ea7\u54c1\u7ecf\u7406"
    assert history[0]["source"] == "distilled"

    # After locking, AI re-fill does not take effect and produces no new history
    fake.suggest_response = {"suggestions": {"occupation": "\u5de5\u7a0b\u5e08"}}
    resp = client.post("/api/profile/fields/autofill")
    assert resp.json()["applied"] == {}
    fields = client.get("/api/profile/fields").json()
    assert fields["values"]["occupation"] == "\u8d44\u6df1\u4ea7\u54c1\u7ecf\u7406"
    history = client.get("/api/profile/fields/history?key=occupation").json()["history"]
    assert len(history) == 1

    # User clears field: old value archived, field deleted (unlocked)
    client.put("/api/profile/fields", json={"values": {"occupation": ""}})
    fields = client.get("/api/profile/fields").json()
    assert "occupation" not in fields["values"]
    history = client.get("/api/profile/fields/history?key=occupation").json()["history"]
    assert len(history) == 2
    assert history[0]["value"] == "\u8d44\u6df1\u4ea7\u54c1\u7ecf\u7406"


def test_manual_roundtrip(client, profile_env):
    resp = client.put("/api/profile/manual", json={"content": "# \u5173\u4e8e\u6211\n\u6211\u53ea\u559d\u624b\u51b2\u5496\u5561"})
    assert resp.status_code == 200
    resp = client.get("/api/profile/manual")
    assert "\u624b\u51b2\u5496\u5561" in resp.json()["content"]


def test_fields_appear_in_profile_even_when_disabled(client, profile_env):
    services, _fake, config = profile_env
    config["profile"]["enabled"] = False
    try:
        resp = client.get("/api/profile")
        assert resp.status_code == 200
        text = resp.json()["profile"]
        assert "Alex" in text or "Preferred name" in text  # Field layer does not depend on enabled
        assert resp.json()["enabled"] is False
    finally:
        config["profile"]["enabled"] = True


# ---------- Daily Distillation ----------

def test_distill_accepts_sourced_and_rejects_unsourced(client, profile_env):
    services, fake, _config = profile_env
    svc = services["profile_service"]
    mem_id = _add_memory(client, "\u753b\u50cf\u84b8\u998f\u6e90\u6587\u6863",
                         "\u672c\u5468\u5f00\u59cb\u5b66\u4e60\u5c24\u514b\u91cc\u91cc\uff0c\u6bcf\u5929\u7ec3\u4e60\u534a\u5c0f\u65f6\uff0c\u76ee\u6807\u662f\u4e09\u4e2a\u6708\u5f39\u5531\u4e00\u9996\u6b4c\u3002")

    fake.distill_response = {"claims": [
        {"text": "\u7528\u6237\u6b63\u5728\u5b66\u4e60\u5c24\u514b\u91cc\u91cc\uff0c\u6bcf\u5929\u7ec3\u4e60\u534a\u5c0f\u65f6", "sources": [mem_id]},
        {"text": "\u7528\u6237\u6ca1\u6709\u6765\u6e90\u7684\u5e7b\u89c9\u65ad\u8a00", "sources": []},
        {"text": "\u5f15\u7528\u4e86\u4e0d\u5b58\u5728\u8bb0\u5fc6\u7684\u65ad\u8a00", "sources": ["mem_none_xxx"]},
    ]}
    resp = client.post("/api/profile/distill", json={})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["added"] == 1
    assert result["rejected"] == 2

    # Source anchoring: distill prompt must contain memory source text; review prompt too
    distill_prompts = [p for p in fake.prompts if "profile distiller" in p]
    assert distill_prompts and "\u5c24\u514b\u91cc\u91cc" in distill_prompts[-1]
    review_prompts = [p for p in fake.prompts if "reviewer" in p]
    assert review_prompts and "\u5c24\u514b\u91cc\u91cc" in review_prompts[-1]

    claims = svc.list_claims(status="active", tier="recent")
    texts = [c["text"] for c in claims]
    assert "\u7528\u6237\u6b63\u5728\u5b66\u4e60\u5c24\u514b\u91cc\u91cc\uff0c\u6bcf\u5929\u7ec3\u4e60\u534a\u5c0f\u65f6" in texts
    assert all(c["sources"] for c in claims)

    # Rendering: active claims appear in profile output, with sources
    resp = client.get("/api/profile", params={"level": "standard", "with_sources": "true"})
    body = resp.json()["profile"]
    assert "\u5c24\u514b\u91cc\u91cc" in body
    assert mem_id in body
    assert body.startswith("<astermem_profile")


def test_distill_requires_enabled(client, profile_env):
    _services, _fake, config = profile_env
    config["profile"]["enabled"] = False
    try:
        resp = client.post("/api/profile/distill", json={})
        assert resp.status_code == 400
    finally:
        config["profile"]["enabled"] = True


# ---------- Traceback and User Resolution ----------

def test_audit_marks_stale_and_user_resolution(client, profile_env):
    services, fake, _config = profile_env
    svc = services["profile_service"]
    mem_id = _add_memory(client, "\u56de\u6eaf\u6d4b\u8bd5\u6587\u6863", "\u5341\u6708\u8981\u53bb\u4eac\u90fd\u65c5\u884c\uff0c\u5df2\u7ecf\u8ba2\u597d\u673a\u7968\u3002")
    version_id = svc.get_active_version_id()
    claim_id = svc.insert_claim(version_id, "recent", "\u7528\u6237\u5341\u6708\u53bb\u4eac\u90fd\u65c5\u884c", [mem_id])

    # Source archived → traceback should mark as stale
    resp = client.put(f"/api/memories/{mem_id}", json={"status": "archived"})
    assert resp.status_code == 200
    resp = client.post("/api/profile/audit")
    assert resp.status_code == 200
    assert resp.json()["stale"] >= 1

    pending = client.get("/api/profile/claims", params={"status": "pending"}).json()["claims"]
    assert any(c["id"] == claim_id and c["status"] == "stale" for c in pending)

    # Stale claims do not appear in profile output
    body = client.get("/api/profile").json()["profile"]
    assert "\u4eac\u90fd" not in body

    # User confirms still valid → transitions back to active
    resp = client.post(f"/api/profile/claims/{claim_id}/resolve", json={"action": "keep"})
    assert resp.status_code == 200
    body = client.get("/api/profile").json()["profile"]
    assert "\u4eac\u90fd" in body

    # User deletes
    resp = client.post(f"/api/profile/claims/{claim_id}/resolve", json={"action": "delete"})
    assert resp.status_code == 200
    body = client.get("/api/profile").json()["profile"]
    assert "\u4eac\u90fd" not in body

    # Audit log is viewable
    logs = client.get("/api/profile/audit-log").json()["logs"]
    assert any(log["kind"] == "audit_stale" for log in logs)


def test_semantic_review_fail_closed(client, profile_env):
    """When semantic review LLM is down, new claims are not injected (unsupported); fail-closed"""
    services, fake, _config = profile_env
    svc = services["profile_service"]
    mem_id = _add_memory(client, "fail-closed \u6d4b\u8bd5", "\u4eca\u5929\u7ed9\u9633\u53f0\u7684\u591a\u8089\u6362\u4e86\u76c6\u3002")

    class BrokenReviewChat(FakeChat):
        def chat(self, messages, temperature=0.3, max_retries=3, **kwargs):
            prompt = messages[0]["content"]
            self.prompts.append(prompt)
            if "profile distiller" in prompt:
                return json.dumps(self.distill_response, ensure_ascii=False)
            raise ConnectionError("review provider down")

    broken = BrokenReviewChat()
    broken.distill_response = {"claims": [
        {"text": "\u7528\u6237\u5728\u517b\u591a\u8089\u690d\u7269", "sources": [mem_id]},
    ]}
    svc._chat_factory = lambda: broken
    svc.auditor._chat_factory = lambda: broken

    result = client.post("/api/profile/distill", json={}).json()
    assert result["added"] == 0
    assert result["unsupported"] == 1
    body = client.get("/api/profile").json()["profile"]
    assert "\u591a\u8089" not in body


# ---------- Status and Agent Channel ----------

def test_status_endpoint(client, profile_env):
    resp = client.get("/api/profile/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert "claim_counts" in data
    assert "pending_issues" in data


def test_agent_get_profile_tool(client, api_token, profile_env):
    resp = client.post("/api/agent/call",
                       json={"tool": "get_profile", "arguments": {"level": "full"}},
                       headers={"Authorization": f"Bearer {api_token}"})
    assert resp.status_code == 200, resp.text
    result = resp.json().get("result") or ""
    assert "<astermem_profile" in str(result)


def test_settings_toggle(client, profile_env):
    _services, _fake, config = profile_env
    resp = client.put("/api/profile/settings", json={"enabled": False, "daily_hour": 4})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert config["profile"]["daily_hour"] == 4
    resp = client.put("/api/profile/settings", json={"enabled": True})
    assert resp.json()["enabled"] is True
    # Invalid daily_hour
    resp = client.put("/api/profile/settings", json={"daily_hour": 25})
    assert resp.status_code == 400
