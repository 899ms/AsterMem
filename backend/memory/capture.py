"""
Conversation capture + background distillation

Background: AsterMem relies on the Agent proactively calling add_memory per the SKILL
rules — and Agents forget. Facts mentioned in conversation but never explicitly saved
are lost forever. Capture closes that gap: the Agent hands over the whole conversation,
and the system decides what is worth keeping — without ever discarding the original.

Design intent:
  - Two layers, both of which are ordinary AsterMem memories:
    1. Raw log (tag capture/raw, priority 2): the conversation text stored verbatim,
       immediately, with zero processing. This is the evidence layer.
    2. Distilled facts (tag capture/distilled): background LLM pass extracts facts
       worth remembering long-term; each fact becomes its own small memory whose
       content ends with "> Source conversation: mem_xxx" — the hard traceability
       link back to the verbatim log ("原文是唯一真相" is preserved: the distilled
       card is a derivative, the raw log is the truth).
  - Distilled facts go through the normal add_memory path, so chunking, indexing AND
    write-time arbitration (dedup against existing memories) all apply automatically.
  - Extraction failure loses nothing: the raw log is already on disk.

Key constraints:
  - capture.enabled defaults to False: every capture costs one LLM call, the owner
    must opt in.
  - Raw logs are exempt from arbitration (see arbitration.EXEMPT_TAGS).

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from datetime import datetime
from typing import Callable, Optional

from . import output_language
from .profile_audit import parse_json_block
from .sync import _run_background

RAW_TAG = "capture/raw"
DISTILLED_TAG = "capture/distilled"


class CaptureService:
    """Conversation capture: immediate raw persistence + async fact distillation with source links"""

    def __init__(self, memory_tools, config: dict,
                 chat_factory: Optional[Callable] = None):
        self.tools = memory_tools
        self.config = config
        if chat_factory is None:
            from .providers import get_chat_model
            chat_factory = lambda: get_chat_model(self.config, caller="capture")  # noqa: E731
        self._chat_factory = chat_factory

    def _cfg(self) -> dict:
        return self.config.get("capture") or {}

    def is_enabled(self) -> bool:
        return bool(self._cfg().get("enabled", False))

    # ---------- Main entry ----------

    def capture(self, content: str, session: Optional[str] = None,
                synchronous: bool = False) -> str:
        """
        Capture one conversation round (or a whole session transcript).

        Args:
            content: Conversation text (any format the Agent chooses; stored verbatim)
            session: Optional session label, stored as a tag for grouping
            synchronous: Run distillation inline (tests); default is background

        Returns:
            Human/Agent-readable result message
        """
        if not self.is_enabled():
            return ("Capture is disabled. Enable it with capture.enabled=true in config.yaml "
                    "(each capture costs one LLM call for fact distillation).")

        text = (content or "").strip()
        min_chars = int(self._cfg().get("min_chars", 80))
        if len(text) < min_chars:
            return f"Skipped: content shorter than {min_chars} chars — nothing worth distilling."

        # Layer 1: verbatim raw log, persisted immediately (never lost)
        now = datetime.now()
        tags = [RAW_TAG]
        if session:
            tags.append(f"session/{session}")
        raw_memory = self.tools.sync.add_memory(
            title=f"Conversation log {now.strftime('%Y-%m-%d %H:%M')}",
            content=text,
            tags=tags,
            priority=2,
            source="api",
        )
        if self.tools._on_document_changed:
            try:
                self.tools._on_document_changed(raw_memory.id)
            except Exception as e:
                print(f"[WARN] Capture chunking trigger failed: {e}")

        # Layer 2: background distillation (fails open — raw log already saved)
        if synchronous:
            distill_result = self._distill(raw_memory.id, text)
            return (f"Captured: {raw_memory.id} | distilled {distill_result.get('added', 0)} fact(s)"
                    + (f" | {distill_result['skipped']}" if distill_result.get("skipped") else ""))

        _run_background(self._distill, raw_memory.id, text)
        return (f"Captured: {raw_memory.id} (raw log saved). "
                f"Fact distillation is running in the background; distilled facts will "
                f"appear as memories tagged {DISTILLED_TAG} with a source link back to {raw_memory.id}.")

    # ---------- Distillation ----------

    def _distill(self, raw_memory_id: str, text: str) -> dict:
        chat = self._chat_factory()
        if chat is None:
            return {"added": 0, "skipped": "llm_unavailable"}

        max_facts = int(self._cfg().get("max_facts", 10))
        max_chars = int(self._cfg().get("max_input_chars", 8000))
        if len(text) > max_chars:
            text = text[:max_chars] + "...(truncated)"

        prompt = (
            "TASK: CONVERSATION DISTILLATION\n"
            "Below is a conversation between a user and an AI assistant. Extract facts about "
            "the USER that are worth remembering long-term across future sessions.\n"
            "Worth extracting: stable preferences, decisions and their reasons, rules/conventions "
            "the user set, important events or plans, corrections the user made.\n"
            "NOT worth extracting: one-off task details, the assistant's own output, anything "
            "already obvious from context, small talk.\n"
            "Rules:\n"
            f"1. At most {max_facts} facts; fewer is better. If nothing qualifies, return an empty array.\n"
            "2. Each fact must be self-contained (understandable without the conversation).\n"
            "3. title: one short line; content: 1-3 sentences; tags: 1-3 topic tags "
            "(lowercase, hierarchical like 'preferences/coding' allowed); "
            "priority: 1-10 (10 = critical long-term rule).\n\n"
            f"Conversation:\n{text}\n\n"
            'Output only JSON: {"facts": [{"title": "...", "content": "...", '
            '"tags": ["..."], "priority": 5}]}'
            + output_language.current_directive(json_mode=True)
        )

        try:
            raw = chat.chat([{"role": "user", "content": prompt}], temperature=0.2)
        except Exception as e:
            print(f"[WARN] Capture distillation LLM call failed for {raw_memory_id}: {e}")
            return {"added": 0, "skipped": f"llm_error: {e}"}

        data = parse_json_block(raw) or {}
        added = 0
        for fact in (data.get("facts") or [])[:max_facts]:
            if not isinstance(fact, dict):
                continue
            title = str(fact.get("title") or "").strip()
            content = str(fact.get("content") or "").strip()
            if not title or not content:
                continue
            fact_tags = [str(t).strip() for t in (fact.get("tags") or []) if str(t).strip()]
            priority = fact.get("priority")
            if not isinstance(priority, int) or not (1 <= priority <= 10):
                priority = 5
            # Hard traceability link: derivative → verbatim source
            body = f"{content}\n\n> Source conversation: {raw_memory_id}"
            # Normal add path => chunking + indexing + arbitration (dedup) all apply
            result = self.tools.add_memory(
                title=title,
                content=body,
                tags=[DISTILLED_TAG] + fact_tags[:3],
                priority=priority,
            )
            if result.startswith("Added:"):
                added += 1

        if added:
            print(f"[capture] Distilled {added} fact(s) from {raw_memory_id}")
        return {"added": added}
