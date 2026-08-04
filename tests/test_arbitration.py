"""
Write-time Conflict Arbitration Tests (memory/arbitration.py)

Background: After each add_memory, similar old memories are recalled and one LLM call
decides keep_both / supersede / duplicate. Bottom lines under test:
  - Only soft-archive, never hard delete; archived memories restorable via status change.
  - Every decision logged to arbitration_log with reasoning (white-box).
  - Fail-open: no chat provider / LLM error => everything is kept.
  - Raw conversation logs (capture/raw) are exempt.
LLM is a FakeChat with a canned decision; candidate recall runs against the real
keyword index of the shared test app.
"""

import json

import pytest

from memory.arbitration import WriteArbitrator


class ArbFakeChat:
    """Returns a canned arbitration decision; records prompts for assertions"""

    def __init__(self, response: dict):
        self.response = response
        self.prompts = []

    def chat(self, messages, temperature=0.3, max_retries=3, **kwargs):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.response, ensure_ascii=False)

    def is_available(self):
        return True


@pytest.fixture()
def arb_env(app_bundle, client):
    _app, config, _path, services = app_bundle

    def make_arbitrator(fake_chat):
        return WriteArbitrator(
            services["database"], services["sync_manager"], services["search_engine"],
            {"arbitration": {"enabled": True, "max_candidates": 5}},
            chat_factory=(lambda: fake_chat),
        )

    def add(title, content, tags=None):
        resp = client.post("/api/memories", json={
            "title": title, "content": content,
            "tags": tags or ["arb-test"], "priority": 5})
        assert resp.status_code == 200, resp.text
        return resp.json()["memory"]["id"]

    return services, make_arbitrator, add


def test_supersede_archives_old_and_logs(arb_env, client):
    services, make_arbitrator, add = arb_env
    db = services["database"]

    old_id = add("Project Zeta database decision",
                 "Project Zeta uses PostgreSQL as its primary datastore for everything.")
    new_id = add("Project Zeta database decision updated",
                 "Project Zeta no longer uses PostgreSQL; it migrated to MySQL as the primary datastore.")

    fake = ArbFakeChat({"action": "supersede", "targets": [old_id],
                        "reason": "decision explicitly replaced"})
    result = make_arbitrator(fake).arbitrate(new_id)

    assert result["action"] == "supersede"
    assert result["archived"] == [old_id]
    # Prompt carried both the new memory and the candidate's original text (source-grounded judgment)
    assert old_id in fake.prompts[0] and "MySQL" in fake.prompts[0]

    # Soft archive only: old memory still exists, content intact, restorable
    old = db.get_memory(old_id)
    assert old is not None and old.status == "archived"
    assert "PostgreSQL" in old.content
    assert db.get_memory(new_id).status == "active"

    # White-box: decision + reasoning in arbitration_log, visible via REST
    logs = db.list_arbitration_logs(limit=10)
    entry = next(l for l in logs if l["new_memory_id"] == new_id)
    assert entry["action"] == "supersede"
    assert entry["archived_ids"] == [old_id]
    assert "replaced" in entry["reason"]

    resp = client.get("/api/arbitration/logs")
    assert resp.status_code == 200
    api_logs = resp.json()["logs"]
    assert any(l["new_memory_id"] == new_id for l in api_logs)

    # Rollback: restoring the archived memory is one status update away
    resp = client.put(f"/api/memories/{old_id}", json={"status": "active"})
    assert resp.status_code == 200
    assert db.get_memory(old_id).status == "active"


def test_duplicate_archives_new_memory(arb_env):
    services, make_arbitrator, add = arb_env
    db = services["database"]

    old_id = add("Coffee preference", "The user drinks oat milk lattes every morning routine.")
    new_id = add("Coffee preference again", "The user drinks oat milk lattes every morning routine.")

    fake = ArbFakeChat({"action": "duplicate", "targets": [old_id],
                        "reason": "identical fact already stored"})
    result = make_arbitrator(fake).arbitrate(new_id)

    assert result["action"] == "duplicate"
    assert result["archived"] == [new_id]
    assert db.get_memory(new_id).status == "archived"
    assert db.get_memory(old_id).status == "active"


def test_keep_both_archives_nothing_but_logs(arb_env):
    services, make_arbitrator, add = arb_env
    db = services["database"]

    a_id = add("Gym schedule alpha", "Weightlifting sessions happen on Monday and Thursday evenings.")
    b_id = add("Gym schedule beta", "Weightlifting warmup routine includes rowing and stretching drills.")

    fake = ArbFakeChat({"action": "keep_both", "targets": [], "reason": "complementary facts"})
    result = make_arbitrator(fake).arbitrate(b_id)

    assert result["action"] == "keep_both"
    assert result["archived"] == []
    assert db.get_memory(a_id).status == "active"
    assert db.get_memory(b_id).status == "active"
    entry = next(l for l in db.list_arbitration_logs(limit=10)
                 if l["new_memory_id"] == b_id)
    assert entry["action"] == "keep_both"


def test_invalid_targets_degrade_to_keep_both(arb_env):
    """Supersede pointing at ids not among the candidates must not archive anything"""
    services, make_arbitrator, add = arb_env
    db = services["database"]

    add("Reading list source", "Currently reading a systems design compendium chapter weekly.")
    new_id = add("Reading list note", "Weekly systems design compendium reading continues with chapter twelve.")

    fake = ArbFakeChat({"action": "supersede", "targets": ["mem_notacand"],
                        "reason": "hallucinated target"})
    result = make_arbitrator(fake).arbitrate(new_id)
    assert result["action"] == "keep_both"
    assert result["archived"] == []
    assert db.get_memory(new_id).status == "active"


def test_fail_open_when_llm_unavailable(arb_env):
    services, _make, add = arb_env
    db = services["database"]

    add("Fail open anchor", "Timezone conversions for the quarterly planning meetings matter.")
    new_id = add("Fail open probe", "Quarterly planning meetings need timezone conversion reminders.")

    arbitrator = WriteArbitrator(
        services["database"], services["sync_manager"], services["search_engine"],
        {"arbitration": {"enabled": True}},
        chat_factory=(lambda: None),
    )
    result = arbitrator.arbitrate(new_id)
    assert result.get("skipped") == "llm_unavailable"
    assert db.get_memory(new_id).status == "active"


def test_capture_raw_is_exempt(arb_env):
    services, make_arbitrator, add = arb_env
    raw_id = add("Conversation log 2026-08-04",
                 "user: hello there assistant: hi, how can I help today",
                 tags=["capture/raw"])
    fake = ArbFakeChat({"action": "duplicate", "targets": [], "reason": "should never run"})
    result = make_arbitrator(fake).arbitrate(raw_id)
    assert result.get("skipped") == "exempt_tag"
    assert fake.prompts == []


def test_disabled_config_skips(arb_env):
    services, _make, add = arb_env
    new_id = add("Disabled probe", "Arbitration disabled configuration should skip entirely.")
    arbitrator = WriteArbitrator(
        services["database"], services["sync_manager"], services["search_engine"],
        {"arbitration": {"enabled": False}},
        chat_factory=(lambda: ArbFakeChat({"action": "duplicate", "targets": []})),
    )
    assert arbitrator.is_enabled() is False
    assert arbitrator.arbitrate(new_id) == {"skipped": "disabled"}
