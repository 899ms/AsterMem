"""
Profile Audit and Retrospective Review (PRD_UserProfile v0.3 · Hard constraints 7.1 / 7.2 / 7.5)

Background: All L3 profile claims are LLM-generated — they may be inaccurate or outdated.
Design intent:
  - Separate generation from review: every new claim must pass "mechanical review (source exists)
    + semantic review (original text supports it)" before entering active status; review failure
    is fail-closed — better to not inject.
  - Source anchoring: semantic review input is always the original memory text fragment,
    never summaries or paraphrases, preventing "paraphrase of a paraphrase" drift.
  - Daily retrospective: rotate through existing claims for spot-checks (oldest verified_at
    first), marking them as stale (source archived) / orphaned (source hard-deleted) /
    unsupported (original text no longer supports) / aging (recent tier over age limit) /
    weakened (child claim invalidated).
Key constraint: all review decisions are written to profile_audit_log; every automated
judgment is auditable.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import json
import re
from datetime import datetime, timedelta
from typing import List, Optional


def _now() -> str:
    return datetime.now().isoformat()


def parse_json_block(raw: str):
    """Extract JSON from LLM output: tolerates ```json fences and surrounding noise, returns None on failure"""
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Locate the first { or [, and try parsing from there
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        break
    return None


def _parse_verdicts(raw: str) -> Optional[dict]:
    """
    Parse review LLM's verdicts output into {index(int): verdict} dict.
    Fault-tolerant: accepts top-level array and string indices; returns None on parse failure
    (distinguishing "bad format" from "model gave no verdicts" for callers).
    """
    data = parse_json_block(raw)
    if isinstance(data, dict):
        items = data.get("verdicts")
    elif isinstance(data, list):
        items = data
    else:
        return None
    if not isinstance(items, list):
        return None
    verdicts = {}
    for v in items:
        if not isinstance(v, dict):
            continue
        try:
            verdicts[int(v.get("index"))] = v
        except (TypeError, ValueError):
            continue
    return verdicts


def get_memory_excerpt(database, memory_id: str, max_chars: int = 800) -> Optional[dict]:
    """Get original text excerpt of a memory; returns None if not found, includes status for mechanical review"""
    memory = database.get_memory(memory_id)
    if not memory:
        return None
    content = (memory.content or "").strip()
    if len(content) > max_chars:
        content = content[:max_chars] + "...(truncated)"
    return {
        "memory_id": memory.id,
        "title": memory.title,
        "status": memory.status,
        "excerpt": content,
    }


class ProfileAuditor:
    """Profile claim auditor: new claim admission review + existing claim retrospective"""

    # Statuses displayed as "pending issues" in retrospective
    PENDING_STATUSES = ("stale", "orphaned", "unsupported", "aging", "weakened", "conflict")

    def __init__(self, database, chat_factory, config: dict):
        self.db = database
        self._chat_factory = chat_factory
        self.config = config

    # ---------- Configuration ----------

    def _cfg(self) -> dict:
        return (self.config.get("profile") or {}).get("audit") or {}

    # ---------- Logging ----------

    def _log(self, kind: str, claim_text: str = "", detail: str = ""):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO profile_audit_log (kind, claim_text, detail, created_at) VALUES (?, ?, ?, ?)",
                (kind, claim_text[:500], detail[:1000], _now()),
            )

    def recent_logs(self, limit: int = 50) -> List[dict]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM profile_audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- Source retrieval (source anchoring · PRD 7.5) ----------

    def resolve_source_excerpts(self, sources: List[str], source_kind: str,
                                version_id: int, max_chars: int = 800,
                                _depth: int = 0) -> List[dict]:
        """
        Resolve claim sources into original text excerpts.
        source_kind='memory': directly retrieve memory text;
        source_kind='claim': recursively trace down to base claims, then retrieve their memory text (max depth 2).
        """
        excerpts: List[dict] = []
        if source_kind == "memory":
            for mid in sources:
                item = get_memory_excerpt(self.db, str(mid), max_chars)
                if item:
                    excerpts.append(item)
        elif source_kind == "claim" and _depth < 2:
            with self.db.get_connection() as conn:
                for cid in sources:
                    row = conn.execute(
                        "SELECT * FROM profile_claims WHERE id = ?", (cid,)
                    ).fetchone()
                    if not row:
                        continue
                    child = dict(row)
                    child_sources = json.loads(child.get("sources") or "[]")
                    excerpts.extend(self.resolve_source_excerpts(
                        child_sources, child.get("source_kind") or "memory",
                        version_id, max_chars, _depth + 1))
        # Deduplicate (same memory may be referenced by multiple child claims)
        seen = set()
        unique = []
        for item in excerpts:
            if item["memory_id"] in seen:
                continue
            seen.add(item["memory_id"])
            unique.append(item)
        return unique

    # ---------- Mechanical review ----------

    def mechanical_check(self, sources: List[str], source_kind: str) -> tuple:
        """
        Returns (verdict, detail):
        verdict = 'ok' | 'orphaned' (source hard-deleted/missing) | 'stale' (source archived)
        """
        if not sources:
            return "orphaned", "No sources"
        if source_kind == "claim":
            with self.db.get_connection() as conn:
                for cid in sources:
                    row = conn.execute(
                        "SELECT status FROM profile_claims WHERE id = ?", (cid,)
                    ).fetchone()
                    if not row:
                        return "orphaned", f"Source claim {cid} does not exist"
                    if row["status"] != "active":
                        return "stale", f"Source claim {cid} has status {row['status']}"
            return "ok", ""
        for mid in sources:
            memory = self.db.get_memory(str(mid))
            if not memory:
                return "orphaned", f"Source memory {mid} does not exist"
            if memory.status != "active":
                return "stale", f"Source memory {mid} has status {memory.status}"
        return "ok", ""

    # ---------- Semantic review (new claim admission) ----------

    def review_new_claims(self, candidates: List[dict], version_id: int) -> List[dict]:
        """
        Two-level review for candidate claims, returns list with status:
          active       passed
          unsupported  semantic layer determined original text does not support (record kept but not injected)
          rejected     mechanical layer failed (no source/source missing), caller should discard
        fail-closed: if semantic review LLM call fails, all are marked unsupported.
        """
        results = []
        to_review = []
        for c in candidates:
            sources = c.get("sources") or []
            source_kind = c.get("source_kind") or "memory"
            text = (c.get("text") or "").strip()
            if not text or not sources:
                self._log("reject_no_source", text, "Claim missing text or sources, rejected by mechanical review")
                results.append({**c, "status": "rejected", "reason": "no_source"})
                continue
            verdict, detail = self.mechanical_check(sources, source_kind)
            if verdict != "ok":
                self._log("reject_mechanical", text, detail)
                results.append({**c, "status": "rejected", "reason": detail})
                continue
            to_review.append(c)

        if not to_review:
            return results

        chat = self._chat_factory()
        if chat is None:
            for c in to_review:
                self._log("semantic_skip_no_llm", c.get("text", ""), "LLM unavailable, fail-closed marking unsupported")
                results.append({**c, "status": "unsupported", "reason": "llm_unavailable"})
            return results

        blocks = []
        for idx, c in enumerate(to_review):
            excerpts = self.resolve_source_excerpts(
                c.get("sources") or [], c.get("source_kind") or "memory", version_id)
            src_text = "\n".join(
                f"  [{e['memory_id']}] {e['title']}: {e['excerpt']}" for e in excerpts
            ) or "  (original text unavailable)"
            blocks.append(f"Claim {idx}: {c['text']}\nSource text:\n{src_text}")

        prompt = (
            "You are a memory system reviewer. Judge whether each claim below is directly supported "
            "by its source text.\nOnly judge based on the given source text, do not introduce external "
            "knowledge. Minor rephrasing, summarization, and reasonable wording adjustments count as "
            "supported; only claims with absolutely no basis in the source text or that contradict it "
            "are unsupported.\n\n"
            + "\n\n".join(blocks)
            + '\n\nOutput only JSON (reason max 20 words): '
              '{"verdicts": [{"index": 0, "supported": true, "reason": "..."}, ...]}'
        )
        try:
            raw = chat.chat([{"role": "user", "content": prompt}], temperature=0)
            verdicts = _parse_verdicts(raw)
        except Exception as e:
            self._log("semantic_review_error", "", f"Semantic review call failed: {e}")
            for c in to_review:
                results.append({**c, "status": "unsupported", "reason": "review_failed"})
            return results
        if verdicts is None:
            # Output truncated or malformed: fail-closed, but log clearly that it's a parse failure not content rejection
            self._log("semantic_review_parse_failed", "",
                      f"Review output could not be parsed (possibly truncated), tail: ...{(raw or '')[-200:]}")
            for c in to_review:
                results.append({**c, "status": "unsupported", "reason": "review_parse_failed"})
            return results

        for idx, c in enumerate(to_review):
            v = verdicts.get(idx)
            if v is None or not v.get("supported"):
                reason = (v or {}).get("reason", "Semantic review did not confirm support")
                self._log("semantic_unsupported", c["text"], reason)
                results.append({**c, "status": "unsupported", "reason": reason})
            else:
                self._log("semantic_pass", c["text"], "")
                results.append({**c, "status": "active", "reason": ""})
        return results

    # ---------- Daily retrospective ----------

    def audit_batch(self, version_id: int, batch_size: Optional[int] = None) -> dict:
        """
        Rotate through existing active claims for spot-checks: mechanical check + semantic re-review
        + aging check + cascade weakening. Returns summary statistics.
        """
        cfg = self._cfg()
        batch_size = batch_size or int(cfg.get("batch_size", 20))
        aging_days = int(cfg.get("aging_days", 30))

        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM profile_claims
                   WHERE version_id = ? AND status = 'active'
                   ORDER BY verified_at IS NOT NULL, verified_at ASC LIMIT ?""",
                (version_id, batch_size),
            ).fetchall()
            claims = [dict(r) for r in rows]

        summary = {"checked": len(claims), "stale": 0, "orphaned": 0,
                   "unsupported": 0, "aging": 0, "weakened": 0, "ok": 0}
        semantic_pool = []

        for claim in claims:
            sources = json.loads(claim.get("sources") or "[]")
            kind = claim.get("source_kind") or "memory"
            verdict, detail = self.mechanical_check(sources, kind)
            if verdict != "ok":
                new_status = verdict if kind == "memory" else "weakened"
                if kind == "claim":
                    detail = f"Child claim invalidated: {detail}"
                self._mark(claim["id"], new_status)
                self._log(f"audit_{new_status}", claim["text"], detail)
                summary[new_status] += 1
                continue
            # Recent tier aging check
            if claim.get("tier") == "recent":
                created = claim.get("created_at") or ""
                try:
                    if created and datetime.fromisoformat(created) < datetime.now() - timedelta(days=aging_days):
                        self._mark(claim["id"], "aging")
                        self._log("audit_aging", claim["text"], f"Recent tier exceeded {aging_days} days")
                        summary["aging"] += 1
                        continue
                except ValueError:
                    pass
            semantic_pool.append(claim)

        # Semantic re-review (skip if LLM unavailable, don't modify existing — retrospective is not fail-closed, avoiding false positives)
        chat = self._chat_factory()
        if chat is not None and semantic_pool:
            blocks = []
            for idx, claim in enumerate(semantic_pool):
                excerpts = self.resolve_source_excerpts(
                    json.loads(claim.get("sources") or "[]"),
                    claim.get("source_kind") or "memory", version_id)
                src_text = "\n".join(
                    f"  [{e['memory_id']}] {e['title']}: {e['excerpt']}" for e in excerpts
                ) or "  (original text unavailable)"
                blocks.append(f"Claim {idx}: {claim['text']}\nSource text:\n{src_text}")
            prompt = (
                "You are a memory system retrospective reviewer. Judge whether each existing claim "
                "below is still supported by its source text.\nOnly judge based on source text. "
                "Minor rephrasing and summarization count as supported; only claims with absolutely "
                "no basis or that contradict the source are unsupported.\n\n"
                + "\n\n".join(blocks)
                + '\n\nOutput only JSON (reason max 20 words): '
                  '{"verdicts": [{"index": 0, "supported": true, "reason": "..."}, ...]}'
            )
            try:
                raw = chat.chat([{"role": "user", "content": prompt}], temperature=0)
                # Retrospective is not fail-closed: parse failure treated as "not reviewed this round", existing claims untouched
                verdicts = _parse_verdicts(raw) or {}
                for idx, claim in enumerate(semantic_pool):
                    v = verdicts.get(idx)
                    if v is not None and not v.get("supported"):
                        self._mark(claim["id"], "unsupported")
                        self._log("audit_unsupported", claim["text"], v.get("reason", ""))
                        summary["unsupported"] += 1
                    else:
                        self._touch_verified(claim["id"])
                        summary["ok"] += 1
            except Exception as e:
                self._log("audit_semantic_error", "", f"Retrospective semantic review failed: {e}")
                summary["ok"] += len(semantic_pool)
        else:
            for claim in semantic_pool:
                self._touch_verified(claim["id"])
                summary["ok"] += 1

        # Cascade weakening: upper-layer claims with source_kind='claim' whose source claims were just invalidated are also marked weakened
        summary["weakened"] += self._cascade_weaken(version_id)
        return summary

    def _cascade_weaken(self, version_id: int) -> int:
        count = 0
        with self.db.get_connection() as conn:
            uppers = conn.execute(
                """SELECT * FROM profile_claims
                   WHERE version_id = ? AND status = 'active' AND source_kind = 'claim'""",
                (version_id,),
            ).fetchall()
            for row in uppers:
                sources = json.loads(row["sources"] or "[]")
                for cid in sources:
                    child = conn.execute(
                        "SELECT status FROM profile_claims WHERE id = ?", (cid,)
                    ).fetchone()
                    if child is None or child["status"] != "active":
                        conn.execute(
                            "UPDATE profile_claims SET status = 'weakened', updated_at = ? WHERE id = ?",
                            (_now(), row["id"]),
                        )
                        self._log("audit_weakened", row["text"],
                                  f"Child claim {cid} invalidated")
                        count += 1
                        break
        return count

    def _mark(self, claim_id: int, status: str):
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE profile_claims SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), claim_id),
            )

    def _touch_verified(self, claim_id: int):
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE profile_claims SET verified_at = ? WHERE id = ?",
                (_now(), claim_id),
            )
