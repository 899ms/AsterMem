"""
Demo mode: serve AsterMem as a public, read-only showcase

Background: the landing page at mem.asterove.com lets anonymous visitors browse and search a
seeded memory library. Visitors must not be able to persist anything, and the instance must not
burn LLM credits, so this module gates the API surface instead of relying on authentication.
Design intent: a single ASTERMEM_DEMO_MODE env flag drives everything. Enforcement lives in one
middleware placed in front of the routers, so a newly added write endpoint is denied by default
rather than silently exposed — the allowlist enumerates what stays reachable, never what is blocked.
Key constraints:
  - Search is served over POST, so method alone cannot classify a request; paths are matched explicitly
  - /api/agent/call is a full read-write channel behind a Bearer token and is always denied
  - Explore endpoints call an LLM on every request and are denied to keep the demo free to run

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

#: Non-GET endpoints that stay reachable. Every entry is read-only: it runs a query and returns
#: results without mutating stored state. Kept as exact paths so a prefix cannot widen the hole.
ALLOWED_WRITE_PATHS = frozenset({
    "/api/search",
    "/api/search/compare",
    "/api/search/meta",
    "/api/quick-match",
})

#: Denied even for GET. Every entry costs something to serve or hands out access:
#:  - tokens are agent credentials, and agent is the read-write channel they unlock
#:  - explore calls an LLM per request
#:  - export builds a zip on disk first, which anonymous traffic would pile onto the tmpfs
#:  - security reports this host's own defences, including the addresses configured never to be
#:    blocked; a demo runs with sign-in off, so anything readable here is readable by anyone
#: Reads that merely return empty results in a demo are not listed here: the visitor is meant to
#: see those pages, and an empty log or a zeroed usage chart tells the truth about this instance.
BLOCKED_READ_PREFIXES = (
    "/api/tokens",
    "/api/explore/",
    "/api/agent/",
    "/api/export",
    "/api/security",
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_demo_mode() -> bool:
    """Whether this process runs as a public demo. Read at call time so tests can toggle it."""
    return os.environ.get("ASTERMEM_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def _denied(reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "detail": reason,
            "demo_mode": True,
            "source_url": "https://github.com/Asterove/AsterMem",
        },
    )


class DemoReadOnlyMiddleware(BaseHTTPMiddleware):
    """Reject anything that could write to disk or spend API credits."""

    async def dispatch(self, request, call_next):
        path = request.url.path

        if not path.startswith("/api/"):
            return await call_next(request)

        if any(path.startswith(prefix) for prefix in BLOCKED_READ_PREFIXES):
            return _denied("This endpoint is disabled in the public demo.")

        if request.method in SAFE_METHODS:
            return await call_next(request)

        if path in ALLOWED_WRITE_PATHS:
            return await call_next(request)

        return _denied(
            "This is a read-only demo. Run your own instance to add or edit memories."
        )


def seed_demo_library(sync_manager, database) -> Optional[int]:
    """
    Populate the demo library with the English sample set on an empty store.

    Returns the number of memories written, or None when the store already had content —
    the container uses ephemeral storage, so this runs on every cold start.
    """
    from memory.auth import add_sample_memories

    try:
        already_populated = bool(database.list_memories(limit=1))
    except Exception as exc:  # noqa: BLE001 - seeding must never block startup
        print(f"[demo] Could not inspect existing memories, skipping seed: {exc}")
        return None

    count = None
    if not already_populated:
        try:
            count = add_sample_memories(sync_manager, lang="en")
        except Exception as exc:  # noqa: BLE001
            print(f"[demo] Failed to seed sample memories: {exc}")
            return None

    # Runs even when the memories were already there: tmpfs survives a service restart, so a
    # redeploy finds a populated store and would otherwise never backfill the graph.
    _seed_graph_when_chunked(database)
    return count


def _seed_graph_when_chunked(database, timeout_seconds: int = 90) -> None:
    """
    Write the demo knowledge graph once chunking has produced trunks.

    Chunking runs on a background queue, so trunks do not exist yet when seeding returns and the
    entity links have nothing to attach to. Waiting happens off the startup path so a stalled or
    failed chunk run delays the graph instead of the whole service.
    """
    import threading
    import time

    from memory.demo_graph import seed_demo_graph

    expected = len(database.list_memories(limit=200))

    def run():
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            ready = sum(
                1 for memory in database.list_memories(limit=200)
                if database.get_trunks_by_document(memory.id)
            )
            if ready >= expected:
                break
            time.sleep(1)

        try:
            result = seed_demo_graph(database)
            print(f"[demo] Seeded knowledge graph: {result}")
        except Exception as exc:  # noqa: BLE001 - the demo still works without a graph
            print(f"[demo] Failed to seed knowledge graph: {exc}")

    threading.Thread(target=run, name="demo-graph-seed", daemon=True).start()
