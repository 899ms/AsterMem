"""
Memory CRUD and Search API Tests

Background: Memory CRUD + keyword search + quick-match form the core pipeline shared by
both Web and AI channels; any field regression breaks both sides.
Design intent: Full end-to-end via TestClient (including Whoosh index writes), semantic
search disabled — only keyword path is verified here; semantic path network layer is
covered by fake HTTP in test_providers.
"""


def _create(client, title, content, tags=None):
    resp = client.post("/api/memories", json={
        "title": title, "content": content, "tags": tags or ["pytest/case"], "priority": 5,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    memory = body.get("memory") or body
    assert memory.get("id", "").startswith("mem_")
    return memory["id"]


def test_memory_crud_roundtrip(client):
    mem_id = _create(client, "CRUD roundtrip", "Original content about vector databases.")

    resp = client.get(f"/api/memories/{mem_id}")
    assert resp.status_code == 200
    detail = resp.json()
    memory = detail.get("memory") or detail
    assert memory["title"] == "CRUD roundtrip"

    resp = client.put(f"/api/memories/{mem_id}", json={"title": "CRUD updated"})
    assert resp.status_code == 200

    resp = client.get(f"/api/memories/{mem_id}")
    memory = resp.json().get("memory") or resp.json()
    assert memory["title"] == "CRUD updated"

    resp = client.delete(f"/api/memories/{mem_id}")
    assert resp.status_code == 200

    # Soft delete: archived memory no longer appears in active list
    resp = client.get("/api/memories", params={"status": "active", "limit": 100})
    ids = [m["id"] for m in resp.json().get("memories", [])]
    assert mem_id not in ids


def test_memory_list_and_filter(client):
    _create(client, "List filter target", "Unique listing content marker xyzzy.", ["pytest/list"])
    resp = client.get("/api/memories", params={"tag": "pytest/list", "limit": 50})
    assert resp.status_code == 200
    memories = resp.json().get("memories", [])
    assert any(m["title"] == "List filter target" for m in memories)


def test_keyword_search_finds_document(client):
    _create(client, "Search anchor", "The quick brown capybara jumps over the whoosh index.")
    resp = client.post("/api/search", json={"query": "capybara", "mode": "keyword", "limit": 5})
    assert resp.status_code == 200
    results = resp.json().get("results", [])
    assert results, "keyword search should find the capybara document"


def test_quick_match_endpoint(client):
    _create(client, "Quickmatch anchor", "Remembering the migration reconciliation checklist.")
    resp = client.post("/api/quick-match", json={"text": "migration reconciliation", "top_k": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["result"], str)


def test_memory_history_versioning(client):
    mem_id = _create(client, "History case", "v1 content")
    client.put(f"/api/memories/{mem_id}", json={"content": "v2 content"})
    resp = client.get(f"/api/memories/{mem_id}/history")
    assert resp.status_code == 200


def test_agent_patch_memory(client, api_token):
    mem_id = _create(client, "Patch target", "The answer is fourty-two.")
    resp = client.post("/api/agent/call", json={
        "tool": "patch_memory",
        "arguments": {"memory_id": mem_id, "old_text": "fourty-two", "new_text": "forty-two"},
    }, headers={"Authorization": f"Bearer {api_token}"})
    assert resp.status_code == 200

    detail = client.get(f"/api/memories/{mem_id}").json()
    memory = detail.get("memory") or detail
    content = memory.get("content", "")
    assert "forty-two" in content and "fourty-two" not in content
