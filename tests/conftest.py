"""
AsterMem Test Fixtures

Background: api.py injects module-level global singletons via init_api; direct imports share state.
Design intent: Session-scoped fixtures build an isolated service stack (all storage in tmp dir,
semantic search disabled to avoid real network calls), using TestClient for real HTTP semantics;
all tests share the same app and are isolated via independent data (different titles/IDs).
Key constraint: Semantic search must stay disabled; no test may depend on external network.
"""

import os
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "backend"))

TEST_PASSWORD = "test-password-123"


@pytest.fixture(scope="session")
def app_bundle(tmp_path_factory):
    """Build an isolated AsterMem app: returns (app, config, config_path, services)"""
    import main as backend_main

    tmp = tmp_path_factory.mktemp("astermem-data")
    config_path = str(tmp / "config.yaml")

    config = {
        "auth": {"default_password": TEST_PASSWORD, "salt": "test_salt"},
        "search": {"keyword": {"enabled": True}, "semantic": {"enabled": False, "min_similarity": 0.3}},
        "server": {"port": 8999, "api_log_max": 100},
        "storage": {
            "data_dir": str(tmp / "data"),
            "database": str(tmp / "data" / "memories.db"),
            "memories_dir": str(tmp / "data" / "memories"),
            "chroma_dir": str(tmp / "data" / "chroma"),
            "whoosh_dir": str(tmp / "data" / "whoosh_index"),
        },
    }

    from memory.providers import normalize_config
    normalize_config(config)

    import yaml
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True)

    services = backend_main.build_services(config)
    app = backend_main.create_app(config, config_path, services)
    return app, config, config_path, services


@pytest.fixture(scope="session")
def client(app_bundle):
    """Authenticated TestClient (cookie session)"""
    from starlette.testclient import TestClient

    app, _config, _path, _services = app_bundle
    c = TestClient(app)
    resp = c.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    assert resp.status_code == 200 and resp.json().get("success"), f"login failed: {resp.text}"
    return c


@pytest.fixture(scope="session")
def anon_client(app_bundle):
    """Unauthenticated TestClient for auth boundary tests"""
    from starlette.testclient import TestClient

    app, *_ = app_bundle
    return TestClient(app)


@pytest.fixture(scope="session")
def api_token(client):
    """Create an API token for agent channel tests"""
    resp = client.post("/api/tokens", json={"name": "pytest"})
    assert resp.status_code == 200, resp.text
    token = resp.json().get("token")
    assert token
    return token
