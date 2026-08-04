"""
Profile Slow-Cycle Dream Tests (PRD_UserProfile v0.3 · P1-0b)

Coverage: Event-triggered suggestions, four-stage pipeline (dedup/merge based on source text,
core summarization, topic map rebuild), candidate version does not take effect directly,
diff, activation switch, discard.
LLM uses FakeChat dispatched by TASK markers; no real network requests.
"""

import json
import re

import pytest


class DreamFakeChat:
    """Dispatch by Dream stage markers; parse real claim ids from prompt, simulate merge/summarization"""

    def __init__(self, merge_first_two=True):
        self.prompts = []
        self.merge_first_two = merge_first_two

    def chat(self, messages, temperature=0.3, max_retries=3, **kwargs):
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        ids = [int(m) for m in re.findall(r"#(\d+) \[", prompt)]
        if "TASK: DEDUP" in prompt:
            if self.merge_first_two and len(ids) >= 2:
                return json.dumps({"merges": [{
                    "ids": ids[:2], "text": "\u7528\u6237\u70ed\u8877\u4e8e\u5f39\u594f\u5c24\u514b\u91cc\u91cc", "tier": "recent",
                }]}, ensure_ascii=False)
            return '{"merges": []}'
        if "TASK: CONFLICT" in prompt:
            return '{"conflicts": []}'
        if "TASK: CORE" in prompt:
            if self.merge_first_two and len(ids) >= 2:
                return json.dumps({"core_claims": [{
                    "text": "\u7528\u6237\u957f\u671f\u6295\u5165\u97f3\u4e50\u7c7b\u7231\u597d", "from": ids[:2],
                }]}, ensure_ascii=False)
            return '{"core_claims": []}'
        if "TASK: MAP" in prompt:
            if self.merge_first_two and ids:
                return json.dumps({"map_claims": [{
                    "topic": "\u7231\u597d", "text": "\u97f3\u4e50\u76f8\u5173\u7684\u5b66\u4e60\u5728\u6301\u7eed\u63a8\u8fdb", "from": [ids[0]],
                }]}, ensure_ascii=False)
            return '{"map_claims": []}'
        if "reviewer" in prompt:
            indexes = sorted({int(m) for m in re.findall(r"Claim (\d+):", prompt)})
            return json.dumps({"verdicts": [
                {"index": i, "supported": True, "reason": "supported by source"} for i in indexes
            ]}, ensure_ascii=False)
        return "{}"

    def is_available(self):
        return True


@pytest.fixture()
def dream_env(app_bundle, client):
    """Enable profile, inject FakeChat, stop scheduler, and seed two memories + three claims"""
    _app, config, _path, services = app_bundle
    scheduler = services.get("profile_scheduler")
    if scheduler:
        scheduler.stop()
    svc = services["profile_service"]
    dream = services["dream_manager"]
    fake = DreamFakeChat()
    old_cfg = config.get("profile")
    config["profile"] = {
        "enabled": True,
        "daily_hour": 3,
        "distill": {"max_memories": 20, "per_source_chars": 2000},
        "audit": {"batch_size": 50, "aging_days": 30},
        "dream": {"auto_run_on_trigger": False, "min_interval_days": 7,
                  "trigger": {"new_claims": 9999, "pending_issues": 9999}},
    }
    old_factories = (svc._chat_factory, svc.auditor._chat_factory,
                     dream._chat_factory, dream.auditor._chat_factory)
    svc._chat_factory = lambda: fake
    svc.auditor._chat_factory = lambda: fake
    dream._chat_factory = lambda: fake
    dream.auditor._chat_factory = lambda: fake

    def add_memory(title, content):
        resp = client.post("/api/memories", json={
            "title": title, "content": content, "tags": ["dream-test"], "priority": 5})
        assert resp.status_code == 200, resp.text
        return resp.json()["memory"]["id"]

    m1 = add_memory("\u5c24\u514b\u91cc\u91cc\u7ec3\u4e60\u65e5\u5fd7", "\u4eca\u5929\u7ec3\u4e86\u5c24\u514b\u91cc\u91cc 40 \u5206\u949f\uff0cC \u548c\u5f26\u8f6c\u6362\u987a\u4e86\u5f88\u591a\u3002")
    m2 = add_memory("\u5468\u672b\u97f3\u4e50\u8ba1\u5212", "\u5468\u672b\u6253\u7b97\u7ee7\u7eed\u7ec3\u5c24\u514b\u91cc\u91cc\uff0c\u987a\u4fbf\u770b\u4e00\u573a\u6f14\u51fa\u3002")
    version_id = svc.get_active_version_id()
    # Clear: other tests in the session-level app may have left claims; supersede all to ensure controlled Dream input
    with services["database"].get_connection() as conn:
        conn.execute(
            "UPDATE profile_claims SET status = 'superseded' WHERE version_id = ?",
            (version_id,))
    seeds = {
        "c1": svc.insert_claim(version_id, "recent", "\u7528\u6237\u5728\u7ec3\u4e60\u5c24\u514b\u91cc\u91cc", [m1]),
        "c2": svc.insert_claim(version_id, "recent", "\u7528\u6237\u5468\u672b\u7ec3\u5c24\u514b\u91cc\u91cc", [m2]),
        "c3": svc.insert_claim(version_id, "recent", "\u7528\u6237\u4f1a\u53bb\u770b\u73b0\u573a\u6f14\u51fa", [m2]),
    }
    yield services, fake, config, seeds, version_id

    (svc._chat_factory, svc.auditor._chat_factory,
     dream._chat_factory, dream.auditor._chat_factory) = old_factories
    if old_cfg is None:
        config.pop("profile", None)
    else:
        config["profile"] = old_cfg


def test_trigger_suggestion(client, dream_env):
    """Event trigger: new claims exceed threshold → Dream suggestion is produced (not auto-executed)"""
    services, _fake, config, _seeds, _vid = dream_env
    dream = services["dream_manager"]
    config["profile"]["dream"]["trigger"]["new_claims"] = 1

    suggestion = dream.check_triggers()
    assert suggestion is not None
    assert any("new claims" in r for r in suggestion["reasons"])
    # Suggestion appears in status, for frontend display
    status = client.get("/api/profile/status").json()
    assert status["dream_suggestion"] is not None
    # Not auto-executed
    assert client.get("/api/profile/dreams").json()["dreams"] == []

    config["profile"]["dream"]["trigger"]["new_claims"] = 9999


def test_dream_pipeline_review_and_activate(client, dream_env):
    services, fake, _config, seeds, old_vid = dream_env
    svc = services["profile_service"]
    dream_mgr = services["dream_manager"]

    dream = dream_mgr.start_dream(trigger_reason="manual", synchronous=True)
    dream = dream_mgr.get_dream(dream["id"])
    assert dream["status"] == "review", dream.get("error")
    candidate_vid = dream["output_version_id"]
    assert candidate_vid and candidate_vid != old_vid

    # Source anchoring: Dream stage prompts must include source text, not just claim text
    dedup_prompts = [p for p in fake.prompts if "TASK: DEDUP" in p]
    assert dedup_prompts and "C \u548c\u5f26\u8f6c\u6362" in dedup_prompts[-1]

    # Candidate does not take effect directly: active version is still old, profile output uses old claims
    assert svc.get_active_version_id() == old_vid
    body = client.get("/api/profile").json()["profile"]
    assert "\u7528\u6237\u5728\u7ec3\u4e60\u5c24\u514b\u91cc\u91cc" in body
    assert "\u7528\u6237\u70ed\u8877\u4e8e\u5f39\u594f\u5c24\u514b\u91cc\u91cc" not in body

    # diff: merge produces 1 new recent + 1 core + 1 map; c1/c2 are removed
    diff = client.get(f"/api/profile/versions/{candidate_vid}/diff").json()
    added_texts = [c["text"] for c in diff["added"]]
    assert "\u7528\u6237\u70ed\u8877\u4e8e\u5f39\u594f\u5c24\u514b\u91cc\u91cc" in added_texts
    assert "\u7528\u6237\u957f\u671f\u6295\u5165\u97f3\u4e50\u7c7b\u7231\u597d" in added_texts
    assert any("\u97f3\u4e50\u76f8\u5173\u7684\u5b66\u4e60" in t for t in added_texts)
    removed_ids = {c["id"] for c in diff["removed"]}
    assert {seeds["c1"], seeds["c2"]} <= removed_ids
    assert diff["unchanged_count"] >= 1  # c3 kept as-is

    # Activation: active version switches, merged claims and long-term mainline enter output
    resp = client.post(f"/api/profile/versions/{candidate_vid}/activate")
    assert resp.status_code == 200
    assert svc.get_active_version_id() == candidate_vid
    body = client.get("/api/profile", params={"level": "full"}).json()["profile"]
    assert "\u7528\u6237\u70ed\u8877\u4e8e\u5f39\u594f\u5c24\u514b\u91cc\u91cc" in body
    assert "\u7528\u6237\u957f\u671f\u6295\u5165\u97f3\u4e50\u7c7b\u7231\u597d" in body
    assert "[Topic Map]" in body
    # Duplicate activation is rejected
    resp = client.post(f"/api/profile/versions/{candidate_vid}/activate")
    assert resp.status_code == 400

    # Merged claim sources are the union of both original sources (traceable)
    merged = [c for c in svc.list_claims(candidate_vid, status="active", tier="recent")
              if c["text"] == "\u7528\u6237\u70ed\u8877\u4e8e\u5f39\u594f\u5c24\u514b\u91cc\u91cc"]
    assert merged and len(merged[0]["sources"]) == 2


def test_dream_auto_activate_when_clean(client, dream_env):
    """auto_activate=true: candidate with zero pending claims goes live without human review"""
    services, _fake, config, _seeds, old_vid = dream_env
    svc = services["profile_service"]
    dream_mgr = services["dream_manager"]
    config["profile"]["dream"]["auto_activate"] = True

    dream = dream_mgr.start_dream(trigger_reason="manual", synchronous=True)
    dream = dream_mgr.get_dream(dream["id"])
    # Auditor gate passed (FakeChat reviewer approves everything) → applied, not review
    assert dream["status"] == "applied", dream.get("error")
    assert svc.get_active_version_id() == dream["output_version_id"]
    assert svc.get_active_version_id() != old_vid


def test_auto_activate_gate_blocks_pending_claims(dream_env):
    """A candidate holding any pending-status claim must stay in review (auditor gate)"""
    services, _fake, _config, _seeds, _vid = dream_env
    svc = services["profile_service"]
    dream_mgr = services["dream_manager"]
    old_active = svc.get_active_version_id()

    # Build a synthetic candidate with one conflicted claim
    with services["database"].get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO profile_versions (status, origin, created_at) "
            "VALUES ('candidate', 'dream', datetime('now'))")
        candidate_vid = cursor.lastrowid
    svc.insert_claim(candidate_vid, "recent", "conflicted fact", ["mem_x"],
                     status="conflict")

    assert dream_mgr._try_auto_activate(candidate_vid) is False
    # Still a candidate; active version untouched
    with services["database"].get_connection() as conn:
        row = conn.execute("SELECT status FROM profile_versions WHERE id = ?",
                           (candidate_vid,)).fetchone()
    assert row["status"] == "candidate"
    assert svc.get_active_version_id() == old_active


def test_dream_discard(client, dream_env):
    services, fake, _config, _seeds, _vid = dream_env
    dream_mgr = services["dream_manager"]
    fake.merge_first_two = False  # No-op Dream: produces no changes

    dream = dream_mgr.start_dream(trigger_reason="manual", synchronous=True)
    dream = dream_mgr.get_dream(dream["id"])
    assert dream["status"] == "review", dream.get("error")
    candidate_vid = dream["output_version_id"]
    old_active = services["profile_service"].get_active_version_id()

    resp = client.post(f"/api/profile/versions/{candidate_vid}/discard")
    assert resp.status_code == 200
    # After discard, active version unchanged, dream marked discarded
    assert services["profile_service"].get_active_version_id() == old_active
    assert dream_mgr.get_dream(dream["id"])["status"] == "discarded"
