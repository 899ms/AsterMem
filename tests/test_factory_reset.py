"""
Factory Reset (POST /api/clear-database) Regression Tests.

Background: The early implementation only deleted business tables in the database, intentionally
preserving MD source files; the seven profile tables, trunk_timeline, and uploaded images were
not cleaned. After clearing, profile claims would reference memories that no longer exist.
Design intent: Use a fully isolated app stack (its own tmp data_dir and config), seed every
table and directory with data, then run a clear and assert each item is zeroed — any missed
table is immediately exposed.
Key constraint: Cannot reuse the session-scoped client from conftest; that would wipe other
tests' data.
"""

import os

import pytest
import yaml
from starlette.testclient import TestClient

SEED_PASSWORD = "seed-password-123"

# Tables that must be empty after reset. Aligned with api._FACTORY_RESET_TABLES,
# but defined separately so that "code forgot to clear a table" is exposed by the test.
EXPECTED_EMPTY_TABLES = (
    "memories",
    "memory_history",
    "trunks",
    "chunk_meta_tags",
    "entities",
    "entity_trunk_links",
    "entity_relations",
    "trunk_timeline",
    "time_events",
    "profile_versions",
    "profile_claims",
    "profile_dreams",
    "profile_audit_log",
    "profile_meta",
    "profile_fields",
    "profile_field_history",
    "api_tokens",
    "sessions",
)


# create_app writes service instances into module-level globals in each route module. This file
# must build its own app stack (cannot reuse conftest's session-scoped client, or it would wipe
# other tests' data). The cost is overwriting existing globals. Must restore them afterwards,
# otherwise subsequent tests hit the new stack with the old client — manifesting as 401 or
# data mismatch, only reproducible in certain execution orders.
_MODULE_GLOBALS = {
    "web.api": (
        "_sync_manager",
        "_search_engine",
        "_config",
        "_config_path",
        "_auth_manager",
        "_memory_tools",
        "_chunking_processor",
    ),
    "web.api_profile": ("_profile_service", "_dream_manager", "_save_config"),
    "web.api_explore": ("_explorer",),
    "web.api_usage": ("_config", "_save_config"),
    "memory.usage_tracker": ("_usage_tracker",),
}


@pytest.fixture
def reset_bundle(tmp_path):
    """Isolated app stack with data_dir in this test's dedicated tmp directory"""
    import importlib

    import main as backend_main
    from memory.providers import normalize_config

    saved = {
        mod_name: {name: getattr(importlib.import_module(mod_name), name, None) for name in names}
        for mod_name, names in _MODULE_GLOBALS.items()
    }

    config_path = str(tmp_path / "config.yaml")
    config = {
        "auth": {"default_password": SEED_PASSWORD, "salt": "test_salt"},
        "search": {"keyword": {"enabled": True}, "semantic": {"enabled": False, "min_similarity": 0.3}},
        "server": {"port": 8998, "api_log_max": 100},
        "storage": {
            "data_dir": str(tmp_path / "data"),
            "database": str(tmp_path / "data" / "memories.db"),
            "memories_dir": str(tmp_path / "data" / "memories"),
            "chroma_dir": str(tmp_path / "data" / "chroma"),
            "whoosh_dir": str(tmp_path / "data" / "whoosh_index"),
        },
    }
    normalize_config(config)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True)

    services = backend_main.build_services(config)
    app = backend_main.create_app(config, config_path, services)

    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": SEED_PASSWORD})
    assert resp.status_code == 200 and resp.json().get("success"), resp.text

    try:
        yield client, config, services
    finally:
        for mod_name, values in saved.items():
            module = importlib.import_module(mod_name)
            for name, value in values.items():
                setattr(module, name, value)


def _seed(services, data_dir: str) -> None:
    """Seed every business table and data directory to ensure the clear actually works"""
    db = services["database"]
    with db.get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO memories (id, title, content, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("m1", "seed", "seed body", "2026-01-01", "2026-01-01"),
        )
        c.execute(
            "INSERT INTO memory_history (memory_id, version, title, content, changed_at) VALUES (?,?,?,?,?)",
            ("m1", 1, "seed", "old", "2026-01-01"),
        )
        c.execute(
            'INSERT INTO trunks (id, document_id, "order", content, created_at) VALUES (?,?,?,?,?)',
            ("t1", "m1", 0, "seed trunk", "2026-01-01"),
        )
        c.execute(
            "INSERT INTO chunk_meta_tags (chunk_id, tag_type, tag_value) VALUES (?,?,?)",
            ("t1", "topic", "x"),
        )
        c.execute("INSERT INTO entities (name, entity_type) VALUES (?,?)", ("Alice", "person"))
        entity_id = c.lastrowid
        c.execute("INSERT INTO entity_trunk_links (entity_id, trunk_id) VALUES (?,?)", (entity_id, "t1"))
        c.execute(
            "INSERT INTO entity_relations (subject_id, relation_type, object_id) VALUES (?,?,?)",
            (entity_id, "self", entity_id),
        )
        c.execute(
            "INSERT INTO trunk_timeline (trunk_id, time_type, time_value, time_normalized) VALUES (?,?,?,?)",
            ("t1", "mentioned", "2026", "2026-01-01"),
        )
        c.execute(
            "INSERT INTO time_events (trunk_id, original_text, absolute_time) VALUES (?,?,?)",
            ("t1", "seed event", "2026-01-01"),
        )
        c.execute(
            "INSERT INTO profile_versions (status, origin, created_at) VALUES (?,?,?)",
            ("active", "daily", "2026-01-01"),
        )
        version_id = c.lastrowid
        c.execute(
            "INSERT INTO profile_claims (version_id, tier, text, sources, created_at) VALUES (?,?,?,?,?)",
            (version_id, "core", "seed claim", '["m1"]', "2026-01-01"),
        )
        c.execute("INSERT INTO profile_dreams (status, created_at) VALUES (?,?)", ("pending", "2026-01-01"))
        c.execute(
            "INSERT INTO profile_audit_log (kind, claim_text, created_at) VALUES (?,?,?)",
            ("verify", "seed claim", "2026-01-01"),
        )
        c.execute("INSERT INTO profile_meta (key, value) VALUES (?,?)", ("watermark", "2026-01-01"))
        c.execute(
            "INSERT INTO profile_fields (key, value, source, updated_at) VALUES (?,?,?,?)",
            ("nickname", "seed", "distilled", "2026-01-01"),
        )
        c.execute(
            "INSERT INTO profile_field_history (key, value, source, archived_at) VALUES (?,?,?,?)",
            ("nickname", "older", "manual", "2026-01-01"),
        )

    for sub, filename in (("memories", "seed.md"), ("profile", "manual.md"), ("images", "seed.png")):
        d = os.path.join(data_dir, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, filename), "w", encoding="utf-8") as f:
            f.write("seed")

    # AI usage logs (separate ai_usage.db); factory reset requires clearing these too
    services["usage_tracker"].record(caller="chat", kind="chat", model="seed-model",
                                     prompt_tokens=10, completion_tokens=5)


def test_factory_reset_wipes_everything(reset_bundle):
    client, config, services = reset_bundle
    data_dir = config["storage"]["data_dir"]

    _seed(services, data_dir)
    token_resp = client.post("/api/tokens", json={"name": "seed-token"})
    assert token_resp.status_code == 200, token_resp.text
    assert services["usage_tracker"].get_logs()["total"] > 0, "Usage seed data not written; test is invalid"

    # Confirm seed data was actually written before clearing; otherwise assertions are vacuous
    db = services["database"]
    with db.get_connection() as conn:
        for table in EXPECTED_EMPTY_TABLES:
            count = conn.cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count > 0, f"Seed data not written to {table}; test is invalid"

    resp = client.post("/api/clear-database")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("success") is True

    # 1. All tables are zeroed
    with db.get_connection() as conn:
        for table in EXPECTED_EMPTY_TABLES:
            count = conn.cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, f"{table} was not cleared; {count} rows remain"

    # 1b. AI usage logs also zeroed (user requirement: reset must clear usage too)
    assert services["usage_tracker"].get_logs()["total"] == 0, "AI usage logs were not cleared"

    # 2. On-disk source files, profile, and images are cleared, but directories are preserved
    for sub in ("memories", "profile", "images"):
        d = os.path.join(data_dir, sub)
        assert os.path.isdir(d), f"{sub}/ directory was entirely removed; subsequent writes will fail"
        assert os.listdir(d) == [], f"{sub}/ has leftover files: {os.listdir(d)}"

    # 3. Backup exists and contains the database
    backup_path = body.get("backup_path")
    assert backup_path and os.path.isdir(backup_path), f"Backup directory does not exist: {backup_path}"
    assert os.path.isfile(os.path.join(backup_path, "memories.db")), "Backup does not contain database file"


def test_factory_reset_restores_default_credentials(reset_bundle):
    client, config, services = reset_bundle
    _seed(services, config["storage"]["data_dir"])

    assert client.post("/api/clear-database").status_code == 200

    # Old password invalidated, admin / admin becomes effective
    fresh = TestClient(client.app)
    assert fresh.post("/api/auth/login", json={"username": "admin", "password": SEED_PASSWORD}).status_code != 200
    ok = fresh.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert ok.status_code == 200 and ok.json().get("success"), ok.text
    assert ok.json().get("must_change_credentials") is True


def test_factory_reset_invalidates_existing_session(reset_bundle):
    client, config, services = reset_bundle
    _seed(services, config["storage"]["data_dir"])

    assert client.post("/api/clear-database").status_code == 200

    # Cookie obtained before reset should no longer work
    assert client.get("/api/tokens").status_code == 401


def test_factory_reset_clears_chroma_without_vector_store(reset_bundle):
    """When semantic search is off, vector_store is None; stale vectors must still be cleared from disk"""
    client, config, services = reset_bundle
    assert services["vector_store"] is None, "This test requires semantic search to be disabled"

    chroma_dir = config["storage"]["chroma_dir"]
    os.makedirs(chroma_dir, exist_ok=True)
    with open(os.path.join(chroma_dir, "stale.sqlite3"), "w", encoding="utf-8") as f:
        f.write("stale vectors")

    assert client.post("/api/clear-database").status_code == 200
    assert os.listdir(chroma_dir) == [], "Stale vectors not cleared when semantic search is disabled"
