"""
Vector index status reporting tests

Background: /vector-status counted every trunk as something that should carry a vector, but
_run_rebuild_task only embeds trunks whose status is "ready". During a large import most trunks sit
at "pending" for hours, so the settings page reported a permanent shortfall and told the user to
rebuild — an operation that wipes the existing vectors, re-embeds everything at the provider's
expense, and skips the pending trunks by design, leaving the number exactly where it was.
Design intent: report the two situations separately, and decide "needs rebuilding" here rather than
in the UI, since the answer depends on what a rebuild actually covers.
Key constraint: the deciding flag must stay false while trunks are merely unfinished. That is the
regression that sent a user to an expensive no-op, and it is what these tests pin down.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "backend"))

from memory.models import Trunk  # noqa: E402


def _create_memory(client, title: str) -> str:
    resp = client.post("/api/memories", json={
        "title": title,
        "content": "Body content for vector index status reporting test case.",
        "tags": [],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["memory"]["id"]


def _add_trunk(database, document_id: str, order: int, status: str) -> str:
    trunk_id = f"trunk_{document_id}_{order}_{status}"
    database.add_trunk(Trunk(
        id=trunk_id,
        document_id=document_id,
        order=order,
        content="Segment body used to exercise vector status reporting.",
        status=status,
    ))
    return trunk_id


def _status(client) -> dict:
    resp = client.get("/api/vector-status")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_unfinished_segments_are_reported_apart_from_missing_vectors(app_bundle, client):
    _app, _config, _path, services = app_bundle
    database = services["database"]

    memory_id = _create_memory(client, "Memory whose segments are still being processed")
    _add_trunk(database, memory_id, 0, "pending")
    _add_trunk(database, memory_id, 1, "processing")

    body = _status(client)
    assert body["processing_trunks"] >= 2
    # They are counted as un-vectorised, which is true, but not as something a rebuild would fix.
    assert body["not_vectorized_count"] >= 2


def test_pending_segments_alone_do_not_ask_for_a_rebuild(app_bundle, client):
    """
    The regression that matters: an import in flight must not be reported as a broken index.
    Rebuilding skips these trunks, so advising it costs a full re-embed and changes nothing.
    """
    _app, _config, _path, services = app_bundle
    database = services["database"]

    memory_id = _create_memory(client, "Memory mid-import")
    _add_trunk(database, memory_id, 0, "pending")
    _add_trunk(database, memory_id, 1, "pending")

    body = _status(client)
    assert body["processing_trunks"] >= 2
    assert body["ready_not_vectorized_count"] == 0
    assert body["needs_rebuild"] is False


def test_a_ready_segment_without_a_vector_does_ask_for_a_rebuild(app_bundle, client):
    """
    A trunk that finished processing and still has no vector is the case rebuilding exists for.

    The flag is tied to whether there is an index to rebuild into: /vector-rebuild refuses the call
    outright when the vector store is off, so advising it there would send the user at a button that
    can only return an error.
    """
    _app, _config, _path, services = app_bundle
    database = services["database"]

    memory_id = _create_memory(client, "Memory with a finished but unindexed segment")
    _add_trunk(database, memory_id, 0, "ready")

    body = _status(client)
    assert body["ready_not_vectorized_count"] >= 1
    assert body["needs_rebuild"] is body["vector_store_available"]


def test_a_disabled_vector_store_never_asks_for_a_rebuild(app_bundle, client):
    """Without an embedding provider there is nothing to rebuild, whatever the segment counts say."""
    _app, _config, _path, services = app_bundle
    search = services["search_engine"]

    body = _status(client)
    if search.vector_store is None:
        assert body["vector_store_available"] is False
        assert body["needs_rebuild"] is False


def test_ready_and_processing_counts_partition_the_total(app_bundle, client):
    """Guards against the two new numbers drifting into double counting or a gap."""
    body = _status(client)
    assert body["ready_trunks"] + body["processing_trunks"] == body["total_trunks"]
    assert body["ready_trunks"] >= 0
    assert body["processing_trunks"] >= 0
