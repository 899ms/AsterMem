"""
Chunking Interruption Recovery Tests

Background: Chunking is a background task in an in-process memory queue. When the process is
killed, memories get stuck with trunk_status='chunking', but startup recovery only picks up
'not_chunked' — these memories are never chunked. Since search and exploration operate at
chunk level, they become completely invisible in results (5 memories were lost this way in
production).
Design intent: Lock down "startup recovery must pick up stalled chunks", while confirming
runtime scans do not accidentally grab tasks in progress.
"""


def _create_memory(client, title: str) -> str:
    resp = client.post("/api/memories", json={
        "title": title,
        "content": "Body content for chunking interruption recovery test case.",
        "tags": [],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["memory"]["id"]


def test_stalled_chunking_is_picked_up_by_startup_recovery(app_bundle, client):
    _app, _config, _path, services = app_bundle
    database = services["database"]

    memory_id = _create_memory(client, "Memory stuck in chunking")
    database.update_memory_trunk_status(memory_id, "chunking")

    stalled_ids = {
        m.id for m in database.get_memories_needing_chunking(limit=500, include_stalled=True)
    }
    assert memory_id in stalled_ids


def test_runtime_scan_ignores_in_progress_chunking(app_bundle, client):
    """Runtime scan must not grab 'chunking' status, otherwise in-progress documents would be re-queued"""
    _app, _config, _path, services = app_bundle
    database = services["database"]

    memory_id = _create_memory(client, "Memory currently being chunked")
    database.update_memory_trunk_status(memory_id, "chunking")

    pending_ids = {m.id for m in database.get_memories_needing_chunking(limit=500)}
    assert memory_id not in pending_ids


def test_not_chunked_is_always_pending(app_bundle, client):
    _app, _config, _path, services = app_bundle
    database = services["database"]

    memory_id = _create_memory(client, "Memory not yet chunked")
    database.update_memory_trunk_status(memory_id, "not_chunked")

    for include_stalled in (False, True):
        ids = {
            m.id
            for m in database.get_memories_needing_chunking(
                limit=500, include_stalled=include_stalled
            )
        }
        assert memory_id in ids


def test_ready_documents_are_never_requeued(app_bundle, client):
    _app, _config, _path, services = app_bundle
    database = services["database"]

    memory_id = _create_memory(client, "Memory with chunking complete")
    database.update_memory_trunk_status(memory_id, "ready")

    ids = {
        m.id for m in database.get_memories_needing_chunking(limit=500, include_stalled=True)
    }
    assert memory_id not in ids
