"""
Conversation Capture Tests (memory/capture.py)

Background: capture_conversation stores the raw conversation verbatim immediately
(tag capture/raw = evidence layer) and distills long-term facts in the background,
each fact carrying a hard source link back to the raw log. Bottom lines under test:
  - Raw log is persisted even when distillation is impossible (fail-open).
  - Every distilled fact traces back to its raw log ("原文是唯一真相" preserved).
  - capture.enabled defaults to off; disabled call stores nothing.
Distillation LLM is a FakeChat; synchronous mode is used to avoid timing flakiness.
"""

import json

import pytest

from memory.capture import DISTILLED_TAG, RAW_TAG, CaptureService


class CaptureFakeChat:
    def __init__(self, facts):
        self.facts = facts
        self.prompts = []

    def chat(self, messages, temperature=0.3, max_retries=3, **kwargs):
        self.prompts.append(messages[0]["content"])
        return json.dumps({"facts": self.facts}, ensure_ascii=False)

    def is_available(self):
        return True


CONVERSATION = (
    "user: from now on always write commit messages in English, our team went international\n"
    "assistant: got it, English commit messages from now on.\n"
    "user: also we decided to move the staging environment to Hetzner to cut costs\n"
    "assistant: noted, staging moves to Hetzner.\n"
)


@pytest.fixture()
def capture_env(app_bundle):
    _app, _config, _path, services = app_bundle
    tools = services["memory_tools"]
    db = services["database"]

    def make_service(facts, enabled=True, chat=None, **cfg_extra):
        fake = chat if chat is not None else CaptureFakeChat(facts)
        cfg = {"capture": {"enabled": enabled, "min_chars": 40, "max_facts": 10, **cfg_extra}}
        return CaptureService(tools, cfg, chat_factory=(lambda: fake)), fake

    return services, tools, db, make_service


def _memories_with_tag(db, tag):
    return db.list_memories(status="active", tags=[tag], limit=100)


def test_capture_stores_raw_and_distills_with_source_link(capture_env):
    services, _tools, db, make_service = capture_env
    facts = [
        {"title": "Commit messages must be English",
         "content": "The team went international; commit messages are always written in English.",
         "tags": ["conventions/git"], "priority": 8},
        {"title": "Staging moved to Hetzner",
         "content": "Staging environment was moved to Hetzner to cut costs.",
         "tags": ["infra"], "priority": 6},
    ]
    service, fake = make_service(facts)

    message = service.capture(CONVERSATION, session="s1", synchronous=True)
    assert "Captured: mem_" in message and "2 fact(s)" in message

    # Raw log stored verbatim with session tag
    raw_logs = [m for m in _memories_with_tag(db, RAW_TAG) if "session/s1" in m.tags]
    assert raw_logs, "raw conversation log must be persisted"
    raw = raw_logs[0]
    assert "Hetzner" in raw.content and raw.priority == 2

    # Each distilled fact links back to the raw log (hard traceability)
    distilled = [m for m in _memories_with_tag(db, DISTILLED_TAG)
                 if f"> Source conversation: {raw.id}" in m.content]
    assert len(distilled) == 2
    titles = {m.title for m in distilled}
    assert "Commit messages must be English" in titles
    by_title = {m.title: m for m in distilled}
    assert by_title["Commit messages must be English"].priority == 8
    assert "conventions/git" in by_title["Commit messages must be English"].tags

    # The conversation text reached the distillation prompt
    assert "went international" in fake.prompts[0]


def test_capture_disabled_stores_nothing(capture_env):
    _services, _tools, db, make_service = capture_env
    service, fake = make_service([], enabled=False)
    before = len(_memories_with_tag(db, RAW_TAG))
    message = service.capture(CONVERSATION, synchronous=True)
    assert "disabled" in message.lower()
    assert len(_memories_with_tag(db, RAW_TAG)) == before
    assert fake.prompts == []


def test_capture_too_short_skipped(capture_env):
    _services, _tools, db, make_service = capture_env
    service, _fake = make_service([])
    before = len(_memories_with_tag(db, RAW_TAG))
    message = service.capture("user: hi", synchronous=True)
    assert "Skipped" in message
    assert len(_memories_with_tag(db, RAW_TAG)) == before


def test_raw_log_survives_llm_failure(capture_env):
    """Fail-open: distillation failing must not lose the raw log"""
    _services, _tools, db, make_service = capture_env

    class ExplodingChat:
        def chat(self, *args, **kwargs):
            raise RuntimeError("provider down")

    service, _fake = make_service([], chat=ExplodingChat())
    before = len(_memories_with_tag(db, RAW_TAG))
    message = service.capture(CONVERSATION, synchronous=True)
    assert "Captured: mem_" in message
    assert len(_memories_with_tag(db, RAW_TAG)) == before + 1


def test_agent_tool_capture_conversation_registered(client, api_token):
    """Agent channel: tool dispatch works; shared app has capture disabled => hint returned"""
    resp = client.post("/api/agent/call", json={
        "tool": "capture_conversation",
        "arguments": {"content": CONVERSATION, "session": "agent-s1"},
    }, headers={"Authorization": f"Bearer {api_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert "disabled" in body["result"].lower()
