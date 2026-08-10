"""
Profile Dream - Slow-loop consolidation (PRD_UserProfile v0.3 · P1-0b)

Background: Daily distillation only looks at the current day's data. Cross-temporal
pattern recognition, deduplication/merging, conflict resolution, and high-level
generalization require a low-frequency offline consolidation process (inspired by
Anthropic's dreaming mechanism).
Design intent:
  - Dream produces **candidate versions** (profile_versions.status='candidate'),
    not directly activated; users review the diff in the UI then activate/discard
    (hard constraint 7.3).
  - Triggering is user- or event-driven at low frequency: manual trigger, or
    suggestions based on new claim count / pending issue count at end of daily task
    (configurable auto-execution + cooldown period).
  - Source anchoring (hard constraint 7.5): all LLM inputs for merging/rewriting/
    generalizing trace through claims back to the original memory text fragments,
    avoiding "paraphrase of a paraphrase".
Key constraint: Active version is read-only during Dream; if Dream fails midway,
the entire candidate version is discarded.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import json
import threading
from datetime import datetime
from typing import Callable, List, Optional

from .profile_audit import ProfileAuditor, parse_json_block


def _now() -> str:
    return datetime.now().isoformat()


class DreamManager:
    """Dream lifecycle management: trigger suggestions, candidate version pipeline, diff, activate/discard"""

    STAGES = ("dedup", "conflict", "core", "map")

    def __init__(self, database, profile_service, config: dict,
                 chat_factory: Optional[Callable] = None):
        self.db = database
        self.profile = profile_service
        self.config = config
        self._chat_factory = chat_factory or profile_service._chat_factory
        self.auditor = ProfileAuditor(database, self._chat_factory, config)
        self._cancel_flags: dict = {}
        self._lock = threading.Lock()

    def _cfg(self) -> dict:
        return (self.config.get("profile") or {}).get("dream") or {}

    # ---------- Queries ----------

    def list_dreams(self, limit: int = 20) -> List[dict]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM profile_dreams ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_dream(self, dream_id: int) -> Optional[dict]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM profile_dreams WHERE id = ?", (dream_id,)
            ).fetchone()
            return dict(row) if row else None

    def _running_dream(self) -> Optional[dict]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM profile_dreams WHERE status = 'running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    # ---------- Triggers ----------

    def check_triggers(self) -> Optional[dict]:
        """
        Event-driven trigger suggestions: writes to profile_meta['dream_suggestion'] when conditions are met.
        When auto_run_on_trigger=true is configured, directly starts a Dream (still subject to cooldown).
        """
        cfg = self._cfg()
        trigger_cfg = cfg.get("trigger") or {}
        min_interval_days = int(cfg.get("min_interval_days", 7))

        # Cooldown: don't suggest if less than min_interval_days since last Dream ended
        last_end = None
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT ended_at FROM profile_dreams WHERE status IN ('review', 'applied') "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row["ended_at"]:
                last_end = row["ended_at"]
        if last_end:
            try:
                days = (datetime.now() - datetime.fromisoformat(last_end)).days
                if days < min_interval_days:
                    return None
            except ValueError:
                pass
        if self._running_dream():
            return None

        version_id = self.profile.get_active_version_id()
        reasons = []
        with self.db.get_connection() as conn:
            since = last_end or "0000"
            new_claims = conn.execute(
                "SELECT COUNT(*) AS n FROM profile_claims "
                "WHERE version_id = ? AND created_at > ?",
                (version_id, since),
            ).fetchone()["n"]
            placeholders = ",".join("?" for _ in ProfileAuditor.PENDING_STATUSES)
            pending = conn.execute(
                f"SELECT COUNT(*) AS n FROM profile_claims "
                f"WHERE version_id = ? AND status IN ({placeholders})",
                (version_id, *ProfileAuditor.PENDING_STATUSES),
            ).fetchone()["n"]

        if new_claims >= int(trigger_cfg.get("new_claims", 50)):
            reasons.append(f"{new_claims} new claims")
        if pending >= int(trigger_cfg.get("pending_issues", 10)):
            reasons.append(f"{pending} pending issues")
        if not reasons:
            self.profile.set_meta("dream_suggestion", None)
            return None

        suggestion = {"suggested_at": _now(), "reasons": reasons}
        self.profile.set_meta("dream_suggestion", suggestion)
        if cfg.get("auto_run_on_trigger"):
            try:
                self.start_dream(trigger_reason="; ".join(reasons))
            except RuntimeError as e:
                print(f"[WARN] Dream auto-trigger failed: {e}")
        return suggestion

    # ---------- Start / Cancel ----------

    def start_dream(self, scope: str = "all", instructions: str = "",
                    trigger_reason: str = "manual",
                    synchronous: bool = False) -> dict:
        with self._lock:
            if self._running_dream():
                raise RuntimeError("A Dream is already running")
            chat = self._chat_factory()
            if chat is None:
                raise RuntimeError("Chat provider unavailable, cannot run Dream")
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    """INSERT INTO profile_dreams
                       (status, scope, instructions, input_version_id,
                        trigger_reason, created_at)
                       VALUES ('running', ?, ?, ?, ?, ?)""",
                    (scope, instructions or "",
                     self.profile.get_active_version_id(), trigger_reason, _now()),
                )
                dream_id = cursor.lastrowid
        self.profile.set_meta("dream_suggestion", None)
        self._cancel_flags[dream_id] = False
        if synchronous:
            self._run(dream_id)
        else:
            threading.Thread(target=self._run, args=(dream_id,),
                             daemon=True, name=f"profile-dream-{dream_id}").start()
        return self.get_dream(dream_id)

    def cancel_dream(self, dream_id: int) -> bool:
        dream = self.get_dream(dream_id)
        if not dream or dream["status"] != "running":
            return False
        self._cancel_flags[dream_id] = True
        return True

    # ---------- Pipeline ----------

    def _run(self, dream_id: int):
        usage = 0
        try:
            dream = self.get_dream(dream_id)
            input_vid = dream["input_version_id"]
            new_vid = self._clone_version(input_vid, dream_id)
            chat = self._chat_factory()

            for stage in self.STAGES:
                if self._cancel_flags.get(dream_id):
                    raise RuntimeError("Cancelled by user")
                usage += self._run_stage(stage, chat, new_vid,
                                         dream.get("instructions") or "")

            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE profile_versions SET status = 'candidate' WHERE id = ?",
                    (new_vid,),
                )
                conn.execute(
                    "UPDATE profile_dreams SET status = 'review', output_version_id = ?, "
                    "usage_tokens = ?, ended_at = ? WHERE id = ?",
                    (new_vid, usage, _now(), dream_id),
                )

            # Optional unattended mode (dream.auto_activate, off by default): replaces the
            # human gate with the auditor gate. A candidate auto-activates ONLY when it is
            # fully clean — every claim already passed the two-step audit and none is in a
            # pending state. Any flagged conflict/unsupported claim keeps the candidate in
            # 'review' for the human, exactly as before. Full version history + one-click
            # rollback (activate an archived version) still applies.
            if self._cfg().get("auto_activate"):
                try:
                    self._try_auto_activate(new_vid)
                except Exception as e:
                    # The Dream itself succeeded; a gate failure only means the candidate
                    # stays in review for the human, never that the Dream failed
                    print(f"[WARN] Dream auto-activate failed, candidate left in review: {e}")
        except Exception as e:
            print(f"[WARN] Dream {dream_id} failed: {e}")
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE profile_dreams SET status = 'failed', error = ?, "
                    "usage_tokens = ?, ended_at = ? WHERE id = ?",
                    (str(e)[:500], usage, _now(), dream_id),
                )
                # Discard candidate version
                conn.execute(
                    "UPDATE profile_versions SET status = 'discarded' "
                    "WHERE dream_id = ? AND status = 'building'",
                    (dream_id,),
                )
        finally:
            self._cancel_flags.pop(dream_id, None)

    def _clone_version(self, input_vid: int, dream_id: int) -> int:
        """Clone active version as a building candidate; cloned_from records source claim for diff"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO profile_versions (status, origin, dream_id, created_at) "
                "VALUES ('building', 'dream', ?, ?)",
                (dream_id, _now()),
            )
            new_vid = cursor.lastrowid
            rows = conn.execute(
                "SELECT * FROM profile_claims WHERE version_id = ? AND status = 'active'",
                (input_vid,),
            ).fetchall()
            for r in rows:
                conn.execute(
                    """INSERT INTO profile_claims
                       (version_id, tier, text, sources, source_kind, confidence,
                        status, cloned_from, created_at, updated_at, verified_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
                    (new_vid, r["tier"], r["text"], r["sources"], r["source_kind"],
                     r["confidence"], r["id"], r["created_at"], _now(), r["verified_at"]),
                )
        return new_vid

    def _load_claims(self, version_id: int, tiers: Optional[List[str]] = None) -> List[dict]:
        sql = "SELECT * FROM profile_claims WHERE version_id = ? AND status = 'active'"
        params: list = [version_id]
        if tiers:
            placeholders = ",".join("?" for _ in tiers)
            sql += f" AND tier IN ({placeholders})"
            params.extend(tiers)
        with self.db.get_connection() as conn:
            rows = conn.execute(sql + " ORDER BY id", params).fetchall()
            claims = []
            for r in rows:
                c = dict(r)
                c["sources"] = json.loads(c.get("sources") or "[]")
                claims.append(c)
            return claims

    def _claims_with_excerpts(self, claims: List[dict], version_id: int) -> str:
        """Source anchoring: each claim is accompanied by its traced-back original text excerpts"""
        blocks = []
        for c in claims:
            excerpts = self.auditor.resolve_source_excerpts(
                c["sources"], c.get("source_kind") or "memory", version_id,
                max_chars=400)
            src = "\n".join(
                f"    [{e['memory_id']}] {e['excerpt']}" for e in excerpts
            ) or "    (original text unavailable)"
            blocks.append(f"#{c['id']} [{c['tier']}] {c['text']}\n  Source text:\n{src}")
        return "\n\n".join(blocks)

    def _chat_json(self, chat, prompt: str) -> tuple:
        """Call LLM and parse JSON, returns (data, estimated tokens). Returns ({}, usage) on parse failure."""
        raw = chat.chat([{"role": "user", "content": prompt}], temperature=0.2)
        usage = (len(prompt) + len(raw or "")) // 4
        return (parse_json_block(raw) or {}), usage

    def _run_stage(self, stage: str, chat, version_id: int, instructions: str) -> int:
        claims = self._load_claims(
            version_id,
            tiers=["recent", "core"] if stage in ("dedup", "conflict") else None)
        if not claims:
            return 0
        valid_ids = {c["id"] for c in claims}
        body = self._claims_with_excerpts(claims, version_id)
        extra = f"\nUser instructions: {instructions}\n" if instructions else ""
        usage = 0

        if stage == "dedup":
            prompt = (
                "TASK: DEDUP\nYou are organizing personal profile claims. Find semantically duplicate or "
                "highly overlapping claim groups, and rewrite them into a single merged claim based on "
                "**source text** (not the claim text itself).\n"
                f"{extra}\nClaim list (with source text):\n{body}\n\n"
                'Output only JSON: {"merges": [{"ids": [1, 2], "text": "merged claim", "tier": "recent"}]}\n'
                "If nothing can be merged, output {\"merges\": []}."
            )
            data, usage = self._chat_json(chat, prompt)
            for merge in data.get("merges", []):
                ids = [i for i in (merge.get("ids") or []) if i in valid_ids]
                text = (merge.get("text") or "").strip()
                if len(ids) < 2 or not text:
                    continue
                members = [c for c in claims if c["id"] in ids]
                merged_sources = self._union_memory_sources(members, version_id)
                tier = merge.get("tier") if merge.get("tier") in ("core", "recent", "map") \
                    else members[0]["tier"]
                self._apply_rewrite(version_id, ids, text, merged_sources, tier,
                                    log_kind="dream_merge")

        elif stage == "conflict":
            prompt = (
                "TASK: CONFLICT\nYou are organizing personal profile claims. Find contradictory claim pairs.\n"
                "Resolution rules: if you can determine which is newer from source text and timestamps, "
                "keep the newer one (resolution=newer_wins, put the kept id in 'keep'); if you can rewrite "
                "both into a more accurate single claim based on their source texts, use resolution=rewrite; "
                "if unable to determine, use resolution=flag to leave it for the user.\n"
                f"{extra}\nClaim list (with source text):\n{body}\n\n"
                'Output only JSON: {"conflicts": [{"ids": [1, 2], "resolution": "newer_wins", '
                '"keep": 2, "text": ""}]}\nIf no conflicts, output {"conflicts": []}.'
            )
            data, usage = self._chat_json(chat, prompt)
            for conflict in data.get("conflicts", []):
                ids = [i for i in (conflict.get("ids") or []) if i in valid_ids]
                if len(ids) < 2:
                    continue
                resolution = conflict.get("resolution")
                members = [c for c in claims if c["id"] in ids]
                if resolution == "newer_wins" and conflict.get("keep") in ids:
                    losers = [i for i in ids if i != conflict["keep"]]
                    self._mark_claims(losers, "superseded")
                    self.auditor._log("dream_conflict_newer",
                                      f"Kept #{conflict['keep']}",
                                      f"Superseded {losers}")
                elif resolution == "rewrite" and (conflict.get("text") or "").strip():
                    merged_sources = self._union_memory_sources(members, version_id)
                    self._apply_rewrite(version_id, ids, conflict["text"].strip(),
                                        merged_sources, members[0]["tier"],
                                        log_kind="dream_conflict_rewrite")
                else:
                    self._mark_claims(ids, "conflict")
                    self.auditor._log("dream_conflict_flag", "", f"Flagged conflict {ids}")

        elif stage == "core":
            recent = [c for c in claims if c["tier"] == "recent"]
            if not recent:
                return usage
            prompt = (
                "TASK: CORE\nYou are generalizing the 'long-term core' of a personal profile. "
                "From the recent claims below (with source text), identify long-term patterns "
                "that hold stable across time.\n"
                "Each core claim must list the recent claim IDs that support it (from), at least 2.\n"
                f"{extra}\nRecent claims:\n"
                + self._claims_with_excerpts(recent, version_id) + "\n\n"
                'Output only JSON: {"core_claims": [{"text": "...", "from": [1, 2]}]}\n'
                "If nothing can be generalized, output {\"core_claims\": []}."
            )
            data, usage = self._chat_json(chat, prompt)
            recent_ids = {c["id"] for c in recent}
            for item in data.get("core_claims", []):
                froms = [i for i in (item.get("from") or []) if i in recent_ids]
                text = (item.get("text") or "").strip()
                if len(froms) < 2 or not text:
                    continue
                # Upper-layer claims reference lower-layer claims (source_kind='claim'); audit recursively traces to original text
                reviewed = self.auditor.review_new_claims(
                    [{"text": text, "sources": froms, "source_kind": "claim"}],
                    version_id)
                status = reviewed[0]["status"] if reviewed else "rejected"
                if status == "rejected":
                    continue
                self.profile.insert_claim(version_id, "core", text,
                                          froms, source_kind="claim", status=status)
                self.auditor._log("dream_core", text, f"Supporting claims {froms}")

        elif stage == "map":
            pool = [c for c in claims if c["tier"] in ("recent", "core")]
            if not pool:
                return usage
            prompt = (
                "TASK: MAP\nYou are reorganizing the 'topic map' of a personal profile: cluster claims "
                "by topic, give each topic a brief summary (may contain multiple sentences), "
                "and list supporting claim IDs (from).\n"
                f"{extra}\nClaim list (with source text):\n"
                + self._claims_with_excerpts(pool, version_id) + "\n\n"
                'Output only JSON: {"map_claims": [{"topic": "Work", "text": "...", "from": [1]}]}\n'
                "If nothing can be organized, output {\"map_claims\": []}."
            )
            data, usage = self._chat_json(chat, prompt)
            new_items = []
            pool_ids = {c["id"] for c in pool}
            for item in data.get("map_claims", []):
                froms = [i for i in (item.get("from") or []) if i in pool_ids]
                text = (item.get("text") or "").strip()
                topic = (item.get("topic") or "").strip()
                if not froms or not text:
                    continue
                new_items.append((f"[{topic}] {text}" if topic else text, froms))
            if new_items:
                # Full layer rebuild: all old map claims are superseded
                old_map = [c["id"] for c in self._load_claims(version_id, tiers=["map"])]
                self._mark_claims(old_map, "superseded")
                for text, froms in new_items:
                    reviewed = self.auditor.review_new_claims(
                        [{"text": text, "sources": froms, "source_kind": "claim"}],
                        version_id)
                    status = reviewed[0]["status"] if reviewed else "rejected"
                    if status == "rejected":
                        continue
                    self.profile.insert_claim(version_id, "map", text,
                                              froms, source_kind="claim", status=status)
                self.auditor._log("dream_map", "", f"Topic map rebuilt with {len(new_items)} items")

        return usage

    def _union_memory_sources(self, members: List[dict], version_id: int) -> List[str]:
        """Union of sources for merged claims = union of member claims traced to memory layer (source anchoring)"""
        result: List[str] = []
        for m in members:
            if (m.get("source_kind") or "memory") == "memory":
                candidates = [str(s) for s in m["sources"]]
            else:
                candidates = [e["memory_id"] for e in self.auditor.resolve_source_excerpts(
                    m["sources"], "claim", version_id)]
            for s in candidates:
                if s not in result:
                    result.append(s)
        return result

    def _apply_rewrite(self, version_id: int, member_ids: List[int], text: str,
                       sources: List[str], tier: str, log_kind: str):
        reviewed = self.auditor.review_new_claims(
            [{"text": text, "sources": sources, "source_kind": "memory"}], version_id)
        status = reviewed[0]["status"] if reviewed else "rejected"
        if status == "rejected":
            return
        self._mark_claims(member_ids, "superseded")
        self.profile.insert_claim(version_id, tier, text, sources, status=status)
        self.auditor._log(log_kind, text, f"Superseded {member_ids}")

    def _mark_claims(self, claim_ids: List[int], status: str):
        if not claim_ids:
            return
        with self.db.get_connection() as conn:
            placeholders = ",".join("?" for _ in claim_ids)
            conn.execute(
                f"UPDATE profile_claims SET status = ?, updated_at = ? "
                f"WHERE id IN ({placeholders})",
                (status, _now(), *claim_ids),
            )

    def _try_auto_activate(self, version_id: int) -> bool:
        """Auditor-gated auto activation: only a candidate with zero pending claims goes live."""
        placeholders = ",".join("?" for _ in ProfileAuditor.PENDING_STATUSES)
        with self.db.get_connection() as conn:
            pending = conn.execute(
                f"SELECT COUNT(*) AS n FROM profile_claims "
                f"WHERE version_id = ? AND status IN ({placeholders})",
                (version_id, *ProfileAuditor.PENDING_STATUSES),
            ).fetchone()["n"]
        if pending > 0:
            self.auditor._log(
                "dream_auto_activate_skipped", f"version#{version_id}",
                f"{pending} pending claim(s) require human review")
            print(f"[dream] Auto-activate skipped for version#{version_id}: "
                  f"{pending} pending claim(s) left in review")
            return False
        activated = self.activate_version(version_id, actor="auto")
        if activated:
            self.auditor._log("dream_auto_activate", f"version#{version_id}",
                              "Candidate passed auditor gate, auto-activated")
            print(f"[dream] Candidate version#{version_id} auto-activated (auditor gate passed)")
        return activated

    # ---------- Candidate version: diff / activate / discard ----------

    def diff(self, version_id: int) -> dict:
        """Diff between candidate version and its source (active) version, for UI review"""
        candidate = self._load_claims(version_id)
        with self.db.get_connection() as conn:
            dream_row = conn.execute(
                "SELECT input_version_id FROM profile_dreams WHERE output_version_id = ?",
                (version_id,),
            ).fetchone()
        base_vid = dream_row["input_version_id"] if dream_row else None
        base = self._load_claims(base_vid) if base_vid else []
        base_by_id = {c["id"]: c for c in base}

        added, modified, unchanged = [], [], []
        covered_base_ids = set()
        for c in candidate:
            origin = base_by_id.get(c.get("cloned_from"))
            if origin is None:
                added.append(c)
            elif origin["text"] != c["text"]:
                covered_base_ids.add(origin["id"])
                modified.append({"before": origin, "after": c})
            else:
                covered_base_ids.add(origin["id"])
                unchanged.append(c)
        removed = [c for c in base if c["id"] not in covered_base_ids]
        return {"version_id": version_id, "base_version_id": base_vid,
                "added": added, "removed": removed, "modified": modified,
                "unchanged_count": len(unchanged)}

    def activate_version(self, version_id: int, actor: str = "user") -> bool:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM profile_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if not row or row["status"] != "candidate":
                return False
            conn.execute(
                "UPDATE profile_versions SET status = 'archived' WHERE status = 'active'")
            conn.execute(
                "UPDATE profile_versions SET status = 'active', activated_at = ? WHERE id = ?",
                (_now(), version_id),
            )
            conn.execute(
                "UPDATE profile_dreams SET status = 'applied' WHERE output_version_id = ?",
                (version_id,),
            )
        detail = ("User activated candidate version" if actor == "user"
                  else "Auto-activated after auditor gate")
        self.auditor._log("dream_activate", f"version#{version_id}", detail)
        return True

    def discard_version(self, version_id: int) -> bool:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM profile_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if not row or row["status"] != "candidate":
                return False
            conn.execute(
                "UPDATE profile_versions SET status = 'discarded' WHERE id = ?",
                (version_id,),
            )
            conn.execute(
                "UPDATE profile_dreams SET status = 'discarded' WHERE output_version_id = ?",
                (version_id,),
            )
        self.auditor._log("dream_discard", f"version#{version_id}", "User discarded candidate version")
        return True
