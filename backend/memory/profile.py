"""
User Profile Layer (PRD_UserProfile v0.3 · P1-0a fast loop)

Background: AsterMem's Agent starts each session knowing nothing about the user;
the profile layer distills the memory store into high-density context that the Agent
can retrieve in a single get_profile call.
Design intent (three source layers × three output levels):
  - L1/L2 structured fields: AI auto-fills (source=distilled), user-edited fields
    (source=manual) are never overwritten by AI; on value change the old value is
    archived into profile_field_history (see
    UserProfile.distill/setManual/archiveValue pattern, 10 history entries per field).
    The manual profile manual.md remains user-exclusive, AI has no write access.
  - L3 AI claims: daily distillation produces claims from the **original text** of
    changed memories (hard constraint 7.5 source anchoring), entering active version
    after two-step review by ProfileAuditor, stored in SQLite (traceable).
  - Three output levels core / recent / map, trimmed by level parameter.
Key constraints:
  - Every claim must include source memory_id; the parser rejects sourceless claims.
  - profile.enabled is off by default; when off, get_profile only returns field layer.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import json
import os
import threading
import time
from datetime import datetime
from typing import Callable, List, Optional

import yaml

from . import output_language
from .profile_audit import ProfileAuditor, parse_json_block

# L1 required + L2 optional field schema. key is stable identifier, label is for UI display.
FIELD_SCHEMA = [
    {"key": "nickname", "label": "Nickname", "required": True,
     "hint": "How you want AI to address you"},
    {"key": "gender", "label": "Gender", "required": True, "hint": ""},
    {"key": "language", "label": "Primary Language", "required": True,
     "hint": "e.g. Chinese / English"},
    {"key": "timezone", "label": "Timezone", "required": True,
     "hint": "e.g. Asia/Shanghai"},
    {"key": "occupation", "label": "Occupation", "required": False, "hint": ""},
    {"key": "location", "label": "Location", "required": False, "hint": ""},
    {"key": "organization", "label": "Organization / Team", "required": False, "hint": ""},
    {"key": "focus", "label": "Current Focus", "required": False,
     "hint": "Projects or areas you are currently working on"},
    {"key": "preferences", "label": "Communication Preferences", "required": False,
     "hint": "e.g. keep answers concise, code comments in Chinese"},
    {"key": "taboos", "label": "Taboos / Avoid", "required": False,
     "hint": "Things you don't want AI to do"},
]

_TIER_LABELS = {"core": "Core Traits", "recent": "Recent", "map": "Topic Map"}


def _now() -> str:
    return datetime.now().isoformat()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class ProfileService:
    """Profile layer core service: field layer IO, daily distillation, output rendering, status queries"""

    def __init__(self, database, config: dict, data_dir: str,
                 chat_factory: Optional[Callable] = None):
        self.db = database
        self.config = config
        self.profile_dir = os.path.join(data_dir, "profile")
        os.makedirs(self.profile_dir, exist_ok=True)
        self.fields_path = os.path.join(self.profile_dir, "fields.yaml")
        self.manual_path = os.path.join(self.profile_dir, "manual.md")
        if chat_factory is None:
            from .providers import get_chat_model
            chat_factory = lambda: get_chat_model(self.config, caller="profile")  # noqa: E731
        self._chat_factory = chat_factory
        self.auditor = ProfileAuditor(database, chat_factory, config)
        self._migrate_fields_yaml()

    def _migrate_fields_yaml(self):
        """One-time migration: import values from legacy fields.yaml into profile_fields (treated as user-filled)"""
        if not os.path.exists(self.fields_path):
            return
        with self.db.get_connection() as conn:
            existing = conn.execute("SELECT COUNT(*) AS n FROM profile_fields").fetchone()
            if existing["n"] > 0:
                return
        try:
            with open(self.fields_path, "r", encoding="utf-8") as f:
                values = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError) as e:
            print(f"[WARN] Failed to migrate fields.yaml: {e}")
            return
        allowed = {f["key"] for f in FIELD_SCHEMA}
        with self.db.get_connection() as conn:
            for key, value in values.items():
                text = str(value or "").strip()
                if key in allowed and text:
                    conn.execute(
                        "INSERT OR IGNORE INTO profile_fields (key, value, source, updated_at) "
                        "VALUES (?, ?, 'manual', ?)",
                        (key, text, _now()),
                    )
        os.rename(self.fields_path, self.fields_path + ".migrated")

    # ---------- Configuration ----------

    def _cfg(self) -> dict:
        return self.config.get("profile") or {}

    def is_enabled(self) -> bool:
        return bool(self._cfg().get("enabled"))

    # ---------- Meta (key-value state) ----------

    def get_meta(self, key: str, default=None):
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM profile_meta WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return default
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return row["value"]

    def set_meta(self, key: str, value):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO profile_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # ---------- Versions ----------

    def get_active_version_id(self) -> int:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM profile_versions WHERE status = 'active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                return row["id"]
            cursor = conn.execute(
                "INSERT INTO profile_versions (status, origin, created_at, activated_at) "
                "VALUES ('active', 'init', ?, ?)",
                (_now(), _now()),
            )
            return cursor.lastrowid

    # ---------- L1/L2 Field layer (AI auto-fills, user-edited fields are never overwritten by AI) ----------

    def get_fields(self) -> dict:
        values = {}
        sources = {}
        with self.db.get_connection() as conn:
            for row in conn.execute(
                "SELECT key, value, source, updated_at FROM profile_fields"
            ).fetchall():
                values[row["key"]] = row["value"]
                sources[row["key"]] = {"source": row["source"],
                                       "updated_at": row["updated_at"]}
        missing = [f["key"] for f in FIELD_SCHEMA
                   if f["required"] and not str(values.get(f["key"]) or "").strip()]
        return {"schema": FIELD_SCHEMA, "values": values, "sources": sources,
                "missing_required": missing}

    def _archive_field(self, conn, key: str, old_value: str, old_source: str):
        """Archive old value into history on value change, max 10 entries per field"""
        conn.execute(
            "INSERT INTO profile_field_history (key, value, source, archived_at) "
            "VALUES (?, ?, ?, ?)",
            (key, old_value, old_source, _now()),
        )
        conn.execute(
            """DELETE FROM profile_field_history WHERE key = ? AND id NOT IN (
                   SELECT id FROM profile_field_history WHERE key = ?
                   ORDER BY id DESC LIMIT 10)""",
            (key, key),
        )

    def _set_field(self, key: str, value: str, source: str) -> bool:
        """
        Write a single field. When source='distilled', does not overwrite user-filled (manual) values.
        Empty value = delete field (old value is still archived). Returns whether a write occurred.
        """
        text = str(value or "").strip()
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT value, source FROM profile_fields WHERE key = ?", (key,)
            ).fetchone()
            if source == "distilled" and row and row["source"] == "manual":
                return False
            if not text:
                if row is None:
                    return False
                self._archive_field(conn, key, row["value"], row["source"])
                conn.execute("DELETE FROM profile_fields WHERE key = ?", (key,))
                return True
            if row and row["value"] == text:
                if source == "manual" and row["source"] != "manual":
                    # User confirmed an AI-filled value: upgrade to manual lock, not a value change
                    conn.execute(
                        "UPDATE profile_fields SET source = 'manual', updated_at = ? WHERE key = ?",
                        (_now(), key),
                    )
                    return True
                return False
            if row:
                self._archive_field(conn, key, row["value"], row["source"])
            conn.execute(
                "INSERT INTO profile_fields (key, value, source, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "source = excluded.source, updated_at = excluded.updated_at",
                (key, text, source, _now()),
            )
            return True

    def update_fields(self, values: dict) -> dict:
        """User saves fields (UI operation), can override any source, marked as manual lock after write"""
        allowed = {f["key"] for f in FIELD_SCHEMA}
        for key in (values or {}):
            if key not in allowed:
                raise ValueError(f"Unknown field: {key}")
        for key, value in (values or {}).items():
            self._set_field(key, value, "manual")
        return self.get_fields()

    def get_field_history(self, key: Optional[str] = None, limit: int = 50) -> List[dict]:
        sql = ("SELECT id, key, value, source, archived_at FROM profile_field_history")
        params: list = []
        if key:
            sql += " WHERE key = ?"
            params.append(key)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.db.get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def autofill_fields(self) -> dict:
        """
        AI auto-fills fields based on memory original text and writes directly to DB (source=distilled).
        User-filled (manual) fields are skipped — this is the only protection boundary.
        Users can change AI-filled values anytime; once changed, AI won't touch them again.
        """
        chat = self._chat_factory()
        if chat is None:
            raise RuntimeError("Chat provider unavailable")

        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT id, title, content FROM memories
                   WHERE status = 'active' ORDER BY updated_at DESC LIMIT 15"""
            ).fetchall()
            memories = [dict(r) for r in rows]
        if not memories:
            return {"applied": {}, "skipped_locked": []}

        blocks = []
        for m in memories:
            content = (m["content"] or "").strip()
            if len(content) > 1500:
                content = content[:1500] + "...(truncated)"
            blocks.append(f"[{m['title']}]\n{content}")

        fields = self.get_fields()
        current = fields["values"]
        schema_desc = "\n".join(
            f"- {f['key']}（{f['label']}）：{f['hint'] or ''}" for f in FIELD_SCHEMA
        )
        prompt = (
            "TASK: Profile field suggestions. You are helping fill in user profile fields. "
            "Only infer from the memory text below; provide short values (one line max) for fields "
            "you can infer, do not fabricate anything — simply omit what cannot be inferred. "
            "You may also suggest more accurate values for fields that already have values.\n\n"
            f"Field definitions:\n{schema_desc}\n\n"
            f"Currently filled: {json.dumps(current, ensure_ascii=False)}\n\n"
            "User's memory text:\n" + "\n\n".join(blocks) + "\n\n"
            'Output only JSON: {"suggestions": {"nickname": "...", "occupation": "..."}}'
            + output_language.current_directive(json_mode=True)
        )
        raw = chat.chat([{"role": "user", "content": prompt}], temperature=0.2)
        data = parse_json_block(raw) or {}
        allowed = {f["key"] for f in FIELD_SCHEMA}
        applied = {}
        skipped = []
        for key, value in (data.get("suggestions") or {}).items():
            text = str(value or "").strip()[:120]
            if key not in allowed or not text:
                continue
            meta = fields["sources"].get(key) or {}
            if meta.get("source") == "manual":
                skipped.append(key)
                continue
            if self._set_field(key, text, "distilled"):
                applied[key] = text
        if applied:
            self.auditor._log("field_autofill", ", ".join(applied),
                              f"AI auto-filled {len(applied)} fields")
        return {"applied": applied, "skipped_locked": skipped}

    def get_manual(self) -> str:
        if os.path.exists(self.manual_path):
            try:
                with open(self.manual_path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError as e:
                print(f"[WARN] Failed to read manual.md: {e}")
        return ""

    def update_manual(self, text: str) -> None:
        with open(self.manual_path, "w", encoding="utf-8") as f:
            f.write(text or "")

    # ---------- L3 Claims ----------

    def list_claims(self, version_id: Optional[int] = None,
                    status: Optional[str] = None,
                    tier: Optional[str] = None) -> List[dict]:
        version_id = version_id or self.get_active_version_id()
        sql = "SELECT * FROM profile_claims WHERE version_id = ?"
        params: list = [version_id]
        if status == "pending":
            placeholders = ",".join("?" for _ in ProfileAuditor.PENDING_STATUSES)
            sql += f" AND status IN ({placeholders})"
            params.extend(ProfileAuditor.PENDING_STATUSES)
        elif status:
            sql += " AND status = ?"
            params.append(status)
        if tier:
            sql += " AND tier = ?"
            params.append(tier)
        sql += " ORDER BY tier, id DESC"
        with self.db.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            claims = []
            for r in rows:
                claim = dict(r)
                claim["sources"] = json.loads(claim.get("sources") or "[]")
                claims.append(claim)
            return claims

    def insert_claim(self, version_id: int, tier: str, text: str,
                     sources: List[str], source_kind: str = "memory",
                     status: str = "active", confidence: float = 1.0,
                     cloned_from: Optional[int] = None) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO profile_claims
                   (version_id, tier, text, sources, source_kind, confidence,
                    status, cloned_from, created_at, updated_at, verified_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, tier, text, json.dumps(sources, ensure_ascii=False),
                 source_kind, confidence, status, cloned_from, _now(), _now(),
                 _now() if status == "active" else None),
            )
            return cursor.lastrowid

    def resolve_claim(self, claim_id: int, action: str) -> bool:
        """Resolve a pending claim: keep (confirm still valid, set back to active) / delete (manually remove)"""
        if action not in ("keep", "delete"):
            raise ValueError(f"Unknown action: {action}")
        new_status = "active" if action == "keep" else "deleted"
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE profile_claims SET status = ?, updated_at = ?, verified_at = ? WHERE id = ?",
                (new_status, _now(), _now(), claim_id),
            )
            changed = cursor.rowcount > 0
        if changed:
            self.auditor._log(f"user_{action}", f"claim#{claim_id}", "User manual resolution")
        return changed

    # ---------- Daily distillation (fast loop · source anchoring) ----------

    def distill_daily(self, day: Optional[str] = None) -> dict:
        day = day or _today()
        distill_cfg = self._cfg().get("distill") or {}
        max_memories = int(distill_cfg.get("max_memories", 20))
        per_chars = int(distill_cfg.get("per_source_chars", 2000))

        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT id, title, content FROM memories
                   WHERE status = 'active'
                     AND (substr(updated_at, 1, 10) = ? OR substr(created_at, 1, 10) = ?)
                   ORDER BY updated_at DESC LIMIT ?""",
                (day, day, max_memories),
            ).fetchall()
            changed = [dict(r) for r in rows]

        if not changed:
            return {"skipped": True, "reason": "no_changes", "day": day}

        chat = self._chat_factory()
        if chat is None:
            return {"skipped": True, "reason": "llm_unavailable", "day": day}

        version_id = self.get_active_version_id()
        existing = self.list_claims(version_id, status="active")
        existing_text = "\n".join(
            f"  #{c['id']} [{c['tier']}] {c['text']}" for c in existing[:80]
        ) or "  (none yet)"

        # Source anchoring: input is the original text of changed memories, not summaries
        source_blocks = []
        for m in changed:
            content = (m["content"] or "").strip()
            if len(content) > per_chars:
                content = content[:per_chars] + "...(truncated)"
            source_blocks.append(f"[{m['id']}] {m['title']}\n{content}")

        prompt = (
            "You are the profile distiller for a personal memory system. The user added/modified "
            "the following memories today (original text). Extract claims about the user that are "
            "worth entering the profile 'recent' layer.\n"
            "Rules:\n"
            "1. Each claim must specify source memory id (sources), only referencing memories listed below.\n"
            "2. Only extract facts, states, and activities about the user — do not restate memory content itself.\n"
            "3. If a claim updates/supersedes one in 'existing claims', put the superseded claim's ID in replaces.\n"
            "4. If nothing is worth extracting, return an empty array.\n\n"
            f"Existing claims:\n{existing_text}\n\n"
            "Today's changed memory text:\n" + "\n\n".join(source_blocks) + "\n\n"
            'Output only JSON: {"claims": [{"text": "...", "sources": ["mem_xxx"], "replaces": [12]}]}'
            + output_language.current_directive(json_mode=True)
        )
        try:
            raw = chat.chat([{"role": "user", "content": prompt}], temperature=0.2)
        except Exception as e:
            return {"skipped": True, "reason": f"llm_error: {e}", "day": day}

        data = parse_json_block(raw) or {}
        candidates = []
        valid_memory_ids = {m["id"] for m in changed}
        for item in data.get("claims", []):
            if not isinstance(item, dict):
                continue
            sources = [s for s in (item.get("sources") or []) if s in valid_memory_ids]
            candidates.append({
                "text": (item.get("text") or "").strip(),
                "sources": sources,
                "source_kind": "memory",
                "replaces": item.get("replaces") or [],
            })

        reviewed = self.auditor.review_new_claims(candidates, version_id)
        added = 0
        unsupported = 0
        rejected = 0
        superseded = 0
        existing_ids = {c["id"] for c in existing}
        for c in reviewed:
            if c["status"] == "rejected":
                rejected += 1
                continue
            self.insert_claim(version_id, "recent", c["text"], c["sources"],
                              status=c["status"])
            if c["status"] == "active":
                added += 1
                for rid in c.get("replaces") or []:
                    if rid in existing_ids:
                        with self.db.get_connection() as conn:
                            conn.execute(
                                "UPDATE profile_claims SET status = 'superseded', updated_at = ? "
                                "WHERE id = ? AND version_id = ?",
                                (_now(), rid, version_id),
                            )
                        superseded += 1
            else:
                unsupported += 1

        self.set_meta("last_distill", {"day": day, "at": _now(), "added": added})
        return {"skipped": False, "day": day, "memories": len(changed),
                "added": added, "unsupported": unsupported,
                "rejected": rejected, "superseded": superseded}

    # ---------- Daily task entry ----------

    def run_daily(self, dream_manager=None) -> dict:
        """Fast loop: distillation + retrospective audit + Dream trigger check. Invoked by scheduler or REST manually."""
        if not self.is_enabled():
            return {"skipped": True, "reason": "profile_disabled"}
        result = {"distill": self.distill_daily()}
        try:
            result["autofill"] = self.autofill_fields()
        except Exception as e:
            result["autofill"] = {"error": str(e)}
        result["audit"] = self.auditor.audit_batch(self.get_active_version_id())
        if dream_manager is not None:
            result["dream_suggestion"] = dream_manager.check_triggers()
        self.set_meta("last_daily_run", {"day": _today(), "at": _now()})
        return result

    # ---------- Output rendering ----------

    def get_profile_text(self, level: str = "standard",
                         with_sources: bool = False) -> str:
        """
        Render profile text (XML-wrapped, for Agent context injection).
        level: core (fields + core traits only) / standard (+ recent) / full (+ topic map + stats)
        """
        tiers = {"core": ["core"], "standard": ["core", "recent"],
                 "full": ["core", "recent", "map"]}.get(level, ["core", "recent"])
        lines = [f'<astermem_profile generated_at="{_now()}" level="{level}">']

        fields = self.get_fields()
        field_lines = []
        for f in FIELD_SCHEMA:
            value = str(fields["values"].get(f["key"]) or "").strip()
            if value:
                field_lines.append(f"{f['label']}: {value}")
        # Note: output contains only factual content, not product-layer meta-comments
        # (rules like "user can override" or "manual takes precedence" are internal conflict
        # resolution logic; mixing them into the prompt only wastes tokens and confuses the Agent)
        if field_lines:
            lines.append("[Basic Info]")
            lines.extend(field_lines)

        manual = self.get_manual().strip()
        if manual:
            if len(manual) > 1200:
                manual = manual[:1200] + "...(truncated)"
            lines.append("\n[Self Introduction]")
            lines.append(manual)

        if self.is_enabled():
            version_id = self.get_active_version_id()
            for tier in tiers:
                claims = self.list_claims(version_id, status="active", tier=tier)
                if not claims:
                    continue
                lines.append(f"\n[{_TIER_LABELS[tier]}]")
                for c in claims:
                    suffix = ""
                    if with_sources:
                        suffix = f" (source: {', '.join(str(s) for s in c['sources'])})"
                    lines.append(f"- {c['text']}{suffix}")

        if level == "full":
            lines.append("\n[Memory Store Overview]")
            lines.append(self._stats_line())

        lines.append("</astermem_profile>")
        return "\n".join(lines)

    def _stats_line(self) -> str:
        parts = []
        try:
            stats = self.db.get_stats()
            parts.append(f"{stats.get('active', stats.get('total', 0))} memories")
        except Exception:
            pass
        try:
            entities = self.db.get_all_entities(limit=6)
            names = [e.get("name") for e in entities if e.get("name")]
            if names:
                parts.append(f"Top entities: {', '.join(names)}")
        except Exception:
            pass
        try:
            events = self.db.get_time_events(status="pending", limit=5)
            if events:
                parts.append(f"{len(events)} pending events")
        except Exception:
            pass
        return " / ".join(parts) or "(no stats available)"

    # ---------- Status ----------

    def status(self) -> dict:
        version_id = self.get_active_version_id()
        with self.db.get_connection() as conn:
            counts = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM profile_claims WHERE version_id = ? GROUP BY status",
                (version_id,),
            ).fetchall():
                counts[row["status"]] = row["n"]
        pending = sum(counts.get(s, 0) for s in ProfileAuditor.PENDING_STATUSES)
        return {
            "enabled": self.is_enabled(),
            "active_version_id": version_id,
            "claim_counts": counts,
            "pending_issues": pending,
            "last_distill": self.get_meta("last_distill"),
            "last_daily_run": self.get_meta("last_daily_run"),
            "dream_suggestion": self.get_meta("dream_suggestion"),
            "missing_required_fields": self.get_fields()["missing_required"],
        }


class ProfileScheduler:
    """
    Daily scheduler: daemon thread wakes every minute; after daily_hour and if not yet
    run today, executes run_daily. Catch-up on start: if daily_hour has passed today
    and hasn't run yet, it will also execute.
    """

    def __init__(self, profile_service: ProfileService, dream_manager=None,
                 check_interval: float = 60.0):
        self.profile = profile_service
        self.dream = dream_manager
        self.check_interval = check_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="profile-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[WARN] Profile daily task failed: {e}")
            self._stop.wait(self.check_interval)

    def _tick(self):
        if not self.profile.is_enabled():
            return
        daily_hour = int(self.profile._cfg().get("daily_hour", 3))
        now = datetime.now()
        if now.hour < daily_hour:
            return
        last = self.profile.get_meta("last_daily_run") or {}
        if last.get("day") == _today():
            return
        print(f"[profile] Starting daily profile task {_today()}")
        result = self.profile.run_daily(self.dream)
        print(f"[profile] Daily profile task completed: {json.dumps(result, ensure_ascii=False, default=str)[:300]}")
