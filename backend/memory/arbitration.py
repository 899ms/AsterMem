"""
Write-time conflict arbitration

Background: With AI as the only reader/writer, contradictory or duplicated memories
pile up between two Dream runs and pollute every retrieval in between ("must use
PostgreSQL" and "switched to MySQL" get recalled side by side, and the Agent has to
guess which one is current on every turn).

Design intent (an extension of the Dream philosophy down to the write path — the same
consolidation judgment, applied incrementally at the moment new information arrives):
  - Post-write, asynchronous: the new memory is persisted BEFORE arbitration runs, so
    a slow or failing LLM can never lose data (fail-open: when in doubt, keep everything).
  - Candidate recall reuses SearchEngine (keyword and/or semantic, whatever is
    available) — inherits the existing degradation ladder for free.
  - One LLM call decides: keep_both / supersede (archive old) / duplicate (archive new).
  - White-box, reversible: only soft-archive (status='archived'), never hard delete;
    every decision — including keep_both — is written to arbitration_log with the
    LLM's reasoning, and archived memories can be restored via update_memory.

Key constraints:
  - Chat provider unavailable / LLM error / unparseable output => keep everything, log nothing destructive.
  - Conversation raw logs (tag capture/raw) are exempt: they are evidence, not claims.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import json
from datetime import datetime
from typing import Callable, List, Optional

from . import output_language
from .profile_audit import parse_json_block

# Tags whose memories must never be arbitrated: raw conversation logs are the
# evidence layer — archiving them would break the traceability chain of distilled facts.
EXEMPT_TAGS = {"capture/raw"}

VALID_ACTIONS = ("keep_both", "supersede", "duplicate")


def _now() -> str:
    return datetime.now().isoformat()


class WriteArbitrator:
    """Post-write conflict arbitration: candidate recall + single LLM judgment + soft archive + audit log"""

    def __init__(self, database, sync_manager, search_engine, config: dict,
                 chat_factory: Optional[Callable] = None):
        self.db = database
        self.sync = sync_manager
        self.search = search_engine
        self.config = config
        if chat_factory is None:
            from .providers import get_chat_model
            chat_factory = lambda: get_chat_model(self.config, caller="arbitration")  # noqa: E731
        self._chat_factory = chat_factory

    def _cfg(self) -> dict:
        return self.config.get("arbitration") or {}

    def is_enabled(self) -> bool:
        return bool(self._cfg().get("enabled", True))

    # ---------- Main entry ----------

    def arbitrate(self, memory_id: str) -> dict:
        """
        Arbitrate a newly written memory against similar existing memories.
        Called in a background thread after add_memory; safe to call synchronously in tests.
        Returns a result dict describing what happened (for logs/tests).
        """
        if not self.is_enabled():
            return {"skipped": "disabled"}

        memory = self.sync.get_memory(memory_id)
        if not memory or memory.status != "active":
            return {"skipped": "memory_not_active"}
        if EXEMPT_TAGS & set(memory.tags or []):
            return {"skipped": "exempt_tag"}

        candidates = self._recall_candidates(memory)
        if not candidates:
            # Nothing similar: no LLM cost, no log entry needed
            return {"action": "keep_both", "reason": "no_candidates", "targets": []}

        chat = self._chat_factory()
        if chat is None:
            return {"skipped": "llm_unavailable"}

        try:
            decision = self._judge(chat, memory, candidates)
        except Exception as e:
            # Fail-open: on any LLM failure keep everything (mirrors "dedup timeout => store all")
            print(f"[WARN] Arbitration LLM call failed for {memory_id}, keeping all: {e}")
            return {"skipped": f"llm_error: {e}"}

        return self._apply(memory, candidates, decision)

    # ---------- Phase 1: candidate recall ----------

    def _recall_candidates(self, memory) -> List:
        """Find similar existing memories via whatever retrieval is available (keyword/semantic/hybrid)."""
        max_candidates = int(self._cfg().get("max_candidates", 5))
        # Title + head of content: enough signal for recall without embedding a huge document
        query = f"{memory.title} {memory.content[:300]}".strip()
        try:
            result = self.search.search(query=query, mode="auto", limit=max_candidates * 2)
        except Exception as e:
            print(f"[WARN] Arbitration candidate recall failed: {e}")
            return []

        candidates = []
        for item in result.get("results", []):
            mem = item.get("memory") or {}
            if mem.get("id") == memory.id or mem.get("status") != "active":
                continue
            tags = mem.get("tags") or []
            if EXEMPT_TAGS & set(tags):
                continue
            candidates.append(mem)
            if len(candidates) >= max_candidates:
                break
        return candidates

    # ---------- Phase 2: LLM judgment ----------

    def _judge(self, chat, memory, candidates: List[dict]) -> dict:
        candidate_blocks = []
        for c in candidates:
            content = (c.get("content") or "").strip()
            if len(content) > 800:
                content = content[:800] + "...(truncated)"
            candidate_blocks.append(
                f"[{c['id']}] {c.get('title', '')} (updated: {c.get('updated_at', '?')})\n{content}"
            )

        new_content = (memory.content or "").strip()
        if len(new_content) > 1500:
            new_content = new_content[:1500] + "...(truncated)"

        prompt = (
            "TASK: MEMORY ARBITRATION\n"
            "A new memory was just written into a personal memory store. Compare it against "
            "the similar existing memories below and decide ONE action:\n"
            "- keep_both: they describe different things, or complement each other → keep everything\n"
            "- supersede: the new memory explicitly replaces/updates outdated existing memories "
            "(e.g. a changed preference or decision) → list the outdated memory ids in targets\n"
            "- duplicate: the new memory adds nothing beyond an existing memory → the new one is redundant\n"
            "Rules:\n"
            "1. When uncertain, ALWAYS choose keep_both. Supersede/duplicate require explicit, "
            "unambiguous evidence in the text (e.g. 'changed to', 'no longer', identical facts).\n"
            "2. Talking about the same topic is NOT enough: two different projects may have "
            "different rules — that is keep_both.\n"
            "3. targets may only contain ids from the existing memories listed below.\n\n"
            f"New memory [{memory.id}] {memory.title} (created: {memory.created_at}):\n{new_content}\n\n"
            "Existing similar memories:\n" + "\n\n".join(candidate_blocks) + "\n\n"
            'Output only JSON: {"action": "keep_both|supersede|duplicate", '
            '"targets": ["mem_xxx"], "reason": "..."}'
            + output_language.current_directive(json_mode=True)
        )
        raw = chat.chat([{"role": "user", "content": prompt}], temperature=0.1)
        data = parse_json_block(raw) or {}

        action = str(data.get("action") or "keep_both")
        if action not in VALID_ACTIONS:
            action = "keep_both"
        valid_ids = {c["id"] for c in candidates}
        targets = [t for t in (data.get("targets") or []) if t in valid_ids]
        if action == "supersede" and not targets:
            # Supersede without valid targets is meaningless — degrade to keep
            action = "keep_both"
        return {"action": action, "targets": targets,
                "reason": str(data.get("reason") or "")[:500]}

    # ---------- Phase 3: apply + audit ----------

    def _apply(self, memory, candidates: List[dict], decision: dict) -> dict:
        action = decision["action"]
        targets = decision["targets"]
        archived = []

        if action == "supersede":
            for target_id in targets:
                if self.sync.delete_memory(target_id, hard_delete=False):
                    archived.append(target_id)
        elif action == "duplicate":
            # The NEW memory is redundant: archive it, keep the established one
            if self.sync.delete_memory(memory.id, hard_delete=False):
                archived.append(memory.id)

        # White-box requirement: every decision (including keep_both) is logged with
        # reasoning, so "why did AI archive/keep this" is always answerable.
        self.db.add_arbitration_log(
            new_memory_id=memory.id,
            action=action,
            target_ids=targets,
            archived_ids=archived,
            reason=decision.get("reason", ""),
        )
        if archived:
            print(f"[arbitration] {action}: new={memory.id}, archived={archived}")
        return {"action": action, "targets": targets, "archived": archived,
                "reason": decision.get("reason", "")}
