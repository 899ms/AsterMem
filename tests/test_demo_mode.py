"""
Demo mode enforcement tests

Background: mem.asterove.com serves AsterMem as a public read-only showcase. The guarantee that a
visitor cannot persist anything rests entirely on DemoReadOnlyMiddleware, so a regression here
would silently turn the public instance writable.
Design intent: exercise the middleware on a standalone Starlette app instead of the real one.
api.py keeps module-level singletons wired by init_api, so building a second full app inside the
suite would clobber the session-scoped fixture other tests share.
Key constraint: the allowlist is asserted to be read-only by construction, so adding a write path
to it fails the suite rather than quietly widening public access.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import sys

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "backend"))

from memory.demo_mode import (  # noqa: E402
    ALLOWED_WRITE_PATHS,
    BLOCKED_READ_PREFIXES,
    DemoReadOnlyMiddleware,
    is_demo_mode,
)

ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@pytest.fixture(scope="module")
def demo_client():
    """A catch-all app behind the middleware: any request that reaches a handler returns 200."""

    async def echo(request):
        return JSONResponse({"reached": True})

    app = Starlette(routes=[Route("/{path:path}", echo, methods=ALL_METHODS)])
    app.add_middleware(DemoReadOnlyMiddleware)
    return TestClient(app)


@pytest.mark.parametrize("path", [
    "/api/memories",
    "/api/memories/mem_123",
    "/api/import-text",
    "/api/import",
    "/api/clear-database",
    "/api/restart",
    "/api/tokens",
    "/api/auth/credentials",
    "/api/config",
    "/api/samples",
    "/api/sync",
    "/api/tags/merge",
    "/api/tags/delete",
    "/api/vector-rebuild",
    "/api/generate-tags",
    "/api/profile/fields",
])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_requests_are_rejected(demo_client, path, method):
    resp = demo_client.request(method, path)
    assert resp.status_code == 403, f"{method} {path} should be denied"
    assert resp.json()["demo_mode"] is True


def test_agent_channel_is_sealed(demo_client):
    """The Bearer-token channel is a full read-write API and must not answer at all."""
    for method in ALL_METHODS:
        resp = demo_client.request(
            method, "/api/agent/call", headers={"Authorization": "Bearer ast_whatever"}
        )
        assert resp.status_code == 403, f"{method} /api/agent/call should be denied"


@pytest.mark.parametrize("path", sorted(ALLOWED_WRITE_PATHS))
def test_search_endpoints_stay_reachable(demo_client, path):
    resp = demo_client.post(path, json={"query": "anything"})
    assert resp.status_code == 200
    assert resp.json()["reached"] is True


@pytest.mark.parametrize("path", [
    "/api/stats",
    "/api/memories",
    "/api/memories/mem_123",
    "/api/tags",
    "/api/methodology",
    "/api/auth/check",
])
def test_reads_stay_reachable(demo_client, path):
    assert demo_client.get(path).status_code == 200


@pytest.mark.parametrize("prefix", BLOCKED_READ_PREFIXES)
def test_sensitive_reads_are_rejected(demo_client, prefix):
    """Endpoints that leak credentials, spend API credits, or dump the library stay closed."""
    assert demo_client.get(f"{prefix}anything").status_code == 403


def test_non_api_routes_pass_through(demo_client):
    """SPA assets and the landing page must not be gated."""
    for path in ["/", "/home", "/assets/index.js"]:
        assert demo_client.get(path).status_code == 200


def test_allowlist_contains_only_search_paths():
    """Guards against a write endpoint being added to the allowlist by mistake."""
    for path in ALLOWED_WRITE_PATHS:
        assert path.startswith("/api/"), path
        assert "search" in path or "match" in path, (
            f"{path} is not a search endpoint; allowing it would let visitors write"
        )
        assert not any(path.startswith(p) for p in BLOCKED_READ_PREFIXES), path


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("  ", False), ("no", False),
])
def test_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("ASTERMEM_DEMO_MODE", value)
    assert is_demo_mode() is expected


def test_flag_defaults_to_off(monkeypatch):
    """A normal self-hosted instance must never come up read-only by accident."""
    monkeypatch.delenv("ASTERMEM_DEMO_MODE", raising=False)
    assert is_demo_mode() is False


def test_demo_graph_is_grounded_in_the_sample_library():
    """
    The graph is hand-written because no LLM extracts it in demo mode, so nothing checks it at
    runtime. An edge pointing at an undeclared entity, or a fact attributed to a memory that does
    not exist, would render as a broken or invented graph on the public page.
    """
    from memory.auth import SAMPLE_MEMORIES_EN
    from memory.demo_graph import (
        DEMO_ENTITIES,
        DEMO_EVENTS,
        DEMO_RELATIONS,
        DEMO_RELATIVE_EVENTS,
    )

    sample_titles = {sample["title"] for sample in SAMPLE_MEMORIES_EN}
    declared = {(name, kind) for group in DEMO_ENTITIES.values() for name, kind, _, _ in group}

    assert set(DEMO_ENTITIES) <= sample_titles, "entities attached to a non-existent memory"

    for subject, subject_type, _relation, obj, object_type, source in DEMO_RELATIONS:
        assert (subject, subject_type) in declared, f"undeclared subject {subject}"
        assert (obj, object_type) in declared, f"undeclared object {obj}"
        assert source in sample_titles, f"relation cites a non-existent memory: {source}"

    for source, month, day, *_ in DEMO_EVENTS:
        assert source in sample_titles, f"event cites a non-existent memory: {source}"
        assert 1 <= month <= 12 and 1 <= day <= 28, f"unsafe date {month}-{day}"

    for source, *_ in DEMO_RELATIVE_EVENTS:
        assert source in sample_titles, f"event cites a non-existent memory: {source}"


def test_demo_graph_seeding_is_idempotent():
    """
    Demo storage is a tmpfs that outlives a service restart, so seeding meets a populated store on
    every redeploy. Relations and events are inserted without dedup, so a second pass would
    multiply every edge in the graph.
    """
    from memory.demo_graph import seed_demo_graph

    class PopulatedStore:
        def get_all_entities(self, limit=100):
            return [{"id": 1, "name": "Alex"}]

        def upsert_entity(self, *args, **kwargs):
            raise AssertionError("must not write into a store that already has a graph")

        def add_entity_relation(self, *args, **kwargs):
            raise AssertionError("must not write into a store that already has a graph")

        def add_time_event(self, *args, **kwargs):
            raise AssertionError("must not write into a store that already has a graph")

    assert "skipped" in seed_demo_graph(PopulatedStore())


def test_demo_mode_yields_no_chat_model(monkeypatch):
    """
    Every LLM caller goes through get_chat_model, so returning None there is what guarantees a
    public demo cannot run up an API bill even if a code path stays reachable.
    """
    from memory.providers import get_chat_model

    config = {"active": {"chat_provider": "lmstudio"}}

    monkeypatch.setenv("ASTERMEM_DEMO_MODE", "1")
    assert get_chat_model(config, caller="chunking") is None
