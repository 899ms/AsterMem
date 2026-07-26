# -*- coding: utf-8 -*-
"""
AI Usage Tracker: observability persistence layer for the unified gateway

Background: AsterMem's AI calls are spread across provider adapters, two httpx
side-channels (ai_explorer / meta_extractor), and the vector embedding pipeline.
Previously all upstream-returned usage data was discarded, leaving users unable
to see how many tokens and how much cost embedding / chat / background processing
each consumed.
Design intent: a single reporting point — all AI calls report here
after completion. Only metadata is recorded (caller scenario, model, tokens,
latency, status); request/response content is not stored.
Costs are not persisted — pricing.py computes them on-the-fly at query time,
so pricing changes do not affect historical data.
Key constraints:
  - record() must silently swallow all exceptions; the observability layer must
    never impact business calls
  - When uninitialized (e.g. unit tests constructing adapters directly),
    record_usage is a no-op
  - FIFO retains only the most recent max_records entries to prevent unbounded growth

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_usage_tracker: Optional["UsageTracker"] = None


class UsageTracker:
    """AI call usage tracker: standalone SQLite (data/ai_usage.db), singleton pattern inspired by APILogger"""

    def __init__(self, db_path: str = "data/ai_usage.db", max_records: int = 50000):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_records = max_records
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_usage_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    provider TEXT,
                    provider_name TEXT,
                    model TEXT,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    cached_tokens INTEGER DEFAULT 0,
                    estimated INTEGER DEFAULT 0,
                    duration_ms INTEGER,
                    status TEXT DEFAULT 'success',
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_usage_timestamp
                ON ai_usage_logs(timestamp DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_usage_caller
                ON ai_usage_logs(caller)
            """)
            conn.commit()

    def record(
        self,
        caller: str,
        kind: str,
        model: str = "",
        provider: str = "",
        provider_name: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        estimated: bool = False,
        duration_ms: Optional[int] = None,
        status: str = "success",
        error: Optional[str] = None,
    ) -> None:
        """Insert a single usage record. The observability layer must not raise; failures are printed as warnings."""
        try:
            if not total_tokens:
                total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO ai_usage_logs (
                        timestamp, caller, kind, provider, provider_name, model,
                        prompt_tokens, completion_tokens, total_tokens, cached_tokens,
                        estimated, duration_ms, status, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.now().isoformat(),
                        caller or "unknown",
                        kind,
                        provider or "",
                        provider_name or "",
                        model or "",
                        int(prompt_tokens or 0),
                        int(completion_tokens or 0),
                        int(total_tokens or 0),
                        int(cached_tokens or 0),
                        1 if estimated else 0,
                        duration_ms,
                        status,
                        (error or "")[:300] or None,
                    ),
                )
                conn.commit()
                self._cleanup(conn)
        except Exception as e:
            print(f"[usage] Failed to record AI usage (ignored): {e}")

    def _cleanup(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) FROM ai_usage_logs").fetchone()[0]
        if count > self.max_records:
            conn.execute(
                """
                DELETE FROM ai_usage_logs WHERE id IN (
                    SELECT id FROM ai_usage_logs ORDER BY id ASC LIMIT ?
                )
                """,
                (count - self.max_records,),
            )
            conn.commit()

    # ==================== Queries ====================

    def get_logs(
        self,
        limit: int = 50,
        offset: int = 0,
        caller: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = "SELECT * FROM ai_usage_logs WHERE 1=1"
        count_query = "SELECT COUNT(*) FROM ai_usage_logs WHERE 1=1"
        params: List[Any] = []
        if caller:
            query += " AND caller = ?"
            count_query += " AND caller = ?"
            params.append(caller)
        if kind:
            query += " AND kind = ?"
            count_query += " AND kind = ?"
            params.append(kind)
        if status:
            query += " AND status = ?"
            count_query += " AND status = ?"
            params.append(status)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(count_query, params).fetchone()[0]
            rows = conn.execute(
                query + " ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]
            ).fetchall()
            return {"logs": [dict(r) for r in rows], "total": total}

    def aggregate(self, since_iso: Optional[str] = None) -> Dict[str, Any]:
        """
        Aggregate query: totals + grouping by caller / model / day / kind.
        Costs are not computed here — groups retain (provider, model) for the
        API layer to look up pricing and compute on-the-fly.
        """
        where = "WHERE timestamp >= ?" if since_iso else ""
        params: List[Any] = [since_iso] if since_iso else []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            def rows(sql: str) -> List[dict]:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]

            sums = ("COUNT(*) AS calls, SUM(prompt_tokens) AS prompt_tokens, "
                    "SUM(completion_tokens) AS completion_tokens, "
                    "SUM(total_tokens) AS total_tokens, SUM(cached_tokens) AS cached_tokens, "
                    "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors")
            totals = rows(f"SELECT {sums} FROM ai_usage_logs {where}")[0]
            by_caller = rows(
                f"SELECT caller, kind, provider, model, {sums} FROM ai_usage_logs {where} "
                "GROUP BY caller, kind, provider, model ORDER BY total_tokens DESC"
            )
            by_model = rows(
                f"SELECT provider, provider_name, model, kind, {sums} FROM ai_usage_logs {where} "
                "GROUP BY provider, model, kind ORDER BY total_tokens DESC"
            )
            by_day = rows(
                f"SELECT substr(timestamp, 1, 10) AS day, caller, kind, provider, model, {sums} "
                f"FROM ai_usage_logs {where} GROUP BY day, caller, kind, provider, model ORDER BY day ASC"
            )
            return {"totals": totals, "by_caller": by_caller, "by_model": by_model, "by_day": by_day}

    def distinct_models(self) -> List[Dict[str, str]]:
        """Distinct (provider, model, kind) combinations that have appeared, for the pricing page"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT DISTINCT provider, provider_name, model, kind FROM ai_usage_logs "
                "WHERE model != '' ORDER BY provider, model"
            ).fetchall()
            return [dict(r) for r in rows]

    def clear(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM ai_usage_logs")
            conn.commit()


def init_usage_tracker(db_path: str = "data/ai_usage.db", max_records: int = 50000) -> UsageTracker:
    global _usage_tracker
    _usage_tracker = UsageTracker(db_path, max_records)
    return _usage_tracker


def get_usage_tracker() -> Optional[UsageTracker]:
    return _usage_tracker


def record_usage(**kwargs) -> None:
    """Module-level shortcut: silently no-ops when the tracker is uninitialized, so adapters don't need null checks"""
    tracker = _usage_tracker
    if tracker is not None:
        tracker.record(**kwargs)


def estimate_tokens(text: str) -> int:
    """Rough estimate when upstream does not return usage (~4 chars = 1 token, consistent with Dream's convention)"""
    return max(1, len(text or "") // 4)
