"""
AsterMem Server: REST API + Web SPA single-process entry point

Background: AsterMem is a self-hosted personal memory service. AI integration uses
/api/agent/call (Bearer Token) + SKILL packs, and the browser uses an SPA.
Design intent: Maintain the "one command to start" deployment experience — FastAPI serves
both /api/* and web-ui/dist build artifacts; frontend and backend are separate in code,
but run in a single process on a single port.
Key constraints:
  - On first start, randomly assign a port in 8000-9000 and write to config.yaml (avoid 5000)
  - All storage paths are resolved relative to the repo root, independent of runtime cwd
  - Semantic search initialization failure must degrade to keyword search, never block startup

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import json
import os
import random
import shutil
import sys
import time

import yaml

# Repo root: parent of backend/; all relative paths anchor here to avoid cwd drift
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT_DIR, ".env"))

from memory.api_logger import init_api_logger
from memory.arbitration import WriteArbitrator
from memory.auth import AuthManager
from memory.capture import CaptureService
from memory.usage_tracker import init_usage_tracker
from memory.database import Database
from memory.demo_mode import DemoReadOnlyMiddleware, is_demo_mode, seed_demo_library
from memory.demo_profile import maybe_seed_demo_profile
from memory.profile import ProfileService, ProfileScheduler
from memory.profile_dream import DreamManager
from memory import output_language
from memory.providers import get_embedding_model, normalize_config
from memory.recall import DEFAULT_NOISE_FLOOR, migrate_recall_config
from memory.search import SearchEngine
from memory.storage import MemoryStorage
from memory.sync import SyncManager
from memory.tools import MemoryTools
from memory.vector import VectorStore

# ASTERMEM_CONFIG: In Docker scenarios, move config.yaml into the data volume (e.g. /app/data/config.yaml),
# so "backup = copy data/ directory" also works in containers; defaults to repo root when unset
CONFIG_PATH = os.path.abspath(os.environ.get("ASTERMEM_CONFIG") or os.path.join(ROOT_DIR, "config.yaml"))

DEFAULT_CONFIG = {
    "auth": {"default_password": "", "salt": "astermem_salt_change_me", "login_required": True},
    # Language for model-generated text (summaries, tags, suggestions). The Web UI writes its own
    # locale here when the user switches language; "auto" leaves output to follow each memory's
    # own language, which is how the project behaved before the field existed.
    "output_language": "auto",
    # min_similarity is the noise floor for semantic recall, not a relevance threshold:
    # relevance is determined relatively by recall.adaptive_cutoff using the best hit as anchor (see memory/recall.py)
    # timeout_seconds: hard deadline per recall path; a hung backend degrades to partial results (see search.py)
    # recall_budget: caps context injected per search response; 0 = unlimited (see tools.RecallBudget)
    "search": {
        "keyword": {"enabled": True},
        "semantic": {"enabled": True, "min_similarity": DEFAULT_NOISE_FLOOR},
        "timeout_seconds": 5,
        "recall_budget": {"max_chars_per_trunk": 0, "max_total_chars": 0},
    },
    # Write-time conflict arbitration: after each add_memory, similar old memories are recalled
    # and one LLM call decides keep/supersede/duplicate; only soft-archives, fully audited
    # (arbitration_log). Degrades to no-op when no chat provider is configured. (see memory/arbitration.py)
    "arbitration": {"enabled": True, "max_candidates": 5},
    # Conversation capture: capture_conversation tool stores the raw log immediately and distills
    # facts in the background (each with a source link). Off by default: costs one LLM call per capture.
    "capture": {"enabled": False, "min_chars": 80, "max_facts": 10},
    "server": {"api_log_max": 1000},
    "profile": {
        "enabled": False,
        "daily_hour": 3,
        "distill": {"max_memories": 20, "per_source_chars": 2000},
        "audit": {"batch_size": 20, "aging_days": 30},
        "dream": {
            "auto_run_on_trigger": False,
            # auto_activate: replace the human review gate with the auditor gate — a candidate
            # version auto-activates only when it has zero pending claims; otherwise it stays
            # in review as before. Off by default (hard constraint 7.3 preserved by default).
            "auto_activate": False,
            "min_interval_days": 7,
            "trigger": {"new_claims": 50, "pending_issues": 10},
        },
    },
    "storage": {
        "data_dir": "./data",
        "database": "./data/memories.db",
        "memories_dir": "./data/memories",
        "chroma_dir": "./data/chroma",
        "whoosh_dir": "./data/whoosh_index",
    },
}


def load_config() -> tuple[dict, str, bool]:
    """
    Background: On first start there's no config.yaml; existing users may have old schema (model.mode).
    Design intent: Generate default config when missing; perform idempotent schema migration
    (normalize_config) when present; port is assigned once and written, then reused on every
    subsequent start so SKILL credential files remain valid.
    Key constraint: Create a .bak copy before rewriting config file so migration errors can be
    manually reverted.
    Returns (config, config_path, changed).
    """
    changed = False
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
        changed = True

    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = dict(value) if isinstance(value, dict) else value
            changed = True

    changed = normalize_config(config) or changed
    changed = migrate_recall_config(config) or changed

    server_cfg = config.setdefault("server", {})
    # ASTERMEM_PORT: Container deployments need a predictable fixed port (port mapping in compose),
    # when set, always overwrite and write back to config.yaml to keep UI and SKILL credentials consistent
    env_port = os.environ.get("ASTERMEM_PORT")
    if env_port and int(env_port) != server_cfg.get("port"):
        server_cfg["port"] = int(env_port)
        changed = True
    elif not server_cfg.get("port"):
        # Backward compat: legacy mcp_port is kept as port source, otherwise randomly assign in 8000-9000
        server_cfg["port"] = int(server_cfg.get("mcp_port") or random.randint(8000, 9000))
        changed = True

    if changed:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        if os.path.exists(CONFIG_PATH):
            try:
                shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".bak")
            except OSError as e:
                print(f"[WARN] Failed to backup config.yaml (continuing write): {e}")
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    return config, CONFIG_PATH, changed


def _abs(path: str) -> str:
    """Resolve relative paths against the repo root directory"""
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(ROOT_DIR, path))


def build_services(config: dict):
    """
    Background: Database / VectorStore / SearchEngine / SyncManager / MemoryTools have a
    fixed assembly order (tools depends on sync + search, search depends on vector + whoosh).
    Design intent: Assemble in a single factory; tests can pass isolated config to get isolated instances.
    Key constraint: When embedding provider is unavailable, semantic search must degrade gracefully, never throw to startup layer.
    """
    # Prompt sites read the output language straight off this dict, so it has to be bound before
    # any of them can run — and because it is bound by reference, a language saved from the
    # settings page reaches long-lived chunkers and extractors with no further wiring.
    output_language.bind(config)

    storage_cfg = config.get("storage", {})
    data_dir = _abs(storage_cfg.get("data_dir", "./data"))
    os.makedirs(data_dir, exist_ok=True)

    # AI usage tracker: unified gateway observation layer, must be initialized before any AI calls,
    # otherwise record_usage will silently no-op and lose data
    usage_tracker = init_usage_tracker(
        os.path.join(data_dir, "ai_usage.db"),
        config.get("server", {}).get("ai_usage_log_max", 50000),
    )

    database = Database(_abs(storage_cfg.get("database", "./data/memories.db")),
                        whoosh_dir=_abs(storage_cfg.get("whoosh_dir", "./data/whoosh_index")))
    storage = MemoryStorage(_abs(storage_cfg.get("memories_dir", "./data/memories")))

    vector_store = None
    semantic_enabled = config.get("search", {}).get("semantic", {}).get("enabled", False)
    if semantic_enabled:
        try:
            embedding_model = get_embedding_model(config)
            if embedding_model is None:
                print("[WARN] No available embedding provider found, semantic search degraded to keyword search")
                semantic_enabled = False
            elif hasattr(embedding_model, "is_available") and not embedding_model.is_available():
                active = (config.get("active") or {}).get("embedding_provider", "?")
                print(f"[WARN] Embedding provider '{active}' is currently unavailable, semantic search degraded to keyword search")
                semantic_enabled = False
            else:
                vector_store = VectorStore(
                    _abs(storage_cfg.get("chroma_dir", "./data/chroma")),
                    embedding_model,
                    title_resolver=database.get_document_title,
                )
                print(f"[OK] Semantic search enabled (provider: {(config.get('active') or {}).get('embedding_provider')})")
        except Exception as e:
            print(f"[WARN] Failed to initialize vector store, semantic search degraded: {e}")
            semantic_enabled = False

    sync_manager = SyncManager(database, storage, vector_store)
    search_engine = SearchEngine(
        database,
        vector_store,
        database.whoosh_search,
        semantic_enabled,
        config.get("search", {}).get("semantic", {}).get("min_similarity", DEFAULT_NOISE_FLOOR),
        timeout_seconds=config.get("search", {}).get("timeout_seconds", 5),
    )
    memory_tools = MemoryTools(sync_manager, search_engine, config)
    auth_manager = AuthManager(database, config)

    # Write-time conflict arbitration + conversation capture: both run through memory_tools'
    # write path; both degrade to no-op / raw-log-only when no chat provider is available
    arbitrator = WriteArbitrator(database, sync_manager, search_engine, config)
    memory_tools.set_arbitrator(arbitrator)
    capture_service = CaptureService(memory_tools, config)
    memory_tools.set_capture_service(capture_service)

    # The demo is a public showcase: force anonymous access and reseed the library, since the
    # container keeps its data in tmpfs and starts empty on every boot.
    if is_demo_mode():
        config.setdefault("auth", {})["login_required"] = False
        seeded = seed_demo_library(sync_manager, database)
        if seeded:
            print(f"[demo] Seeded {seeded} sample memories")

    api_logger = init_api_logger(
        os.path.join(data_dir, "api_logs.db"),
        config.get("server", {}).get("api_log_max", 1000),
    )

    # Profile layer: fast-loop service + slow-loop Dream; disabled by default (profile.enabled),
    # when disabled only provides L1/L2 field layer, no LLM calls are made
    profile_service = ProfileService(database, config, data_dir)
    dream_manager = DreamManager(database, profile_service, config)

    # Distillation is what normally fills this layer, and the demo disables it, so the profile is
    # written by hand here instead. Runs after the service exists but needs no trunks, unlike the
    # knowledge graph, because claims reference memories directly.
    if is_demo_mode():
        result = maybe_seed_demo_profile(profile_service, database)
        if result and "skipped" not in result:
            print(f"[demo] Seeded profile: {result}")

    return {
        "database": database,
        "storage": storage,
        "vector_store": vector_store,
        "sync_manager": sync_manager,
        "search_engine": search_engine,
        "memory_tools": memory_tools,
        "auth_manager": auth_manager,
        "api_logger": api_logger,
        "usage_tracker": usage_tracker,
        "profile_service": profile_service,
        "dream_manager": dream_manager,
        "arbitrator": arbitrator,
        "capture_service": capture_service,
    }


def should_log_request(path: str, demo_mode: bool, exclude_paths) -> bool:
    """
    Whether a request belongs in the API log.

    The demo answers anonymous public traffic and every visitor reads the same /api/logs page, so
    logging there would show each visitor what the others searched for. Keeping the log empty is
    what makes that page safe to expose, rather than the page being hidden.
    """
    if demo_mode or not path.startswith("/api/"):
        return False
    return not any(path.startswith(excluded) for excluded in exclude_paths)


def create_app(config: dict, config_path: str, services: dict):
    """
    Background: A single process must serve REST API, AI channel (/api/agent/call), and SPA static assets.
    Design intent: Route priority is API routes -> data images -> SPA assets -> SPA fallback;
    fallback redirects all unknown GET paths to index.html, letting React Router handle frontend routing.
    Key constraint: API log middleware must rebuild response body (original response is not reusable
    after reading body_iterator), and must exclude SSE streaming endpoints and file exports to avoid
    breaking streaming.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    from web.api import init_api, router as api_router, _save_config
    from web.api_explore import init_explore_api, router as explore_router
    from web.api_profile import init_profile_api, router as profile_router
    from web.api_usage import init_usage_api, router as usage_router
    from web.api_security import init_security_api, router as security_router
    from web.scan_guard import ScanGuard, ScanGuardMiddleware

    init_api(services["sync_manager"], services["search_engine"], config, config_path,
             services["auth_manager"], services["memory_tools"])
    init_explore_api(services["search_engine"], config)
    init_profile_api(services["profile_service"], services["dream_manager"], _save_config)
    init_usage_api(config, _save_config)

    # Profile daily scheduler: daemon thread; idles when profile.enabled is off, no calls made.
    # The demo has no owner to build a profile for, and distillation would both write to disk
    # and spend API credits, so the thread is never started there.
    if is_demo_mode():
        services["profile_scheduler"] = None
    else:
        scheduler = ProfileScheduler(services["profile_service"], services["dream_manager"])
        scheduler.start()
        services["profile_scheduler"] = scheduler

    app = FastAPI(title="AsterMem", description="Self-hosted personal memory service",
                  version="2.0.0", docs_url=None, redoc_url=None, openapi_url=None)


    api_logger = services["api_logger"]
    demo_mode = is_demo_mode()

    # On by default: an instance reachable from the internet is the case that needs it, and one
    # reachable only on a LAN exempts its whole address range anyway, so it costs nothing there.
    scan_guard = ScanGuard()
    scan_guard_enabled = os.environ.get("ASTERMEM_SCAN_GUARD", "1").strip().lower() not in ("0", "false", "no")
    # The security page reads this same instance: its counters only exist in the object the
    # middleware is holding.
    init_security_api(scan_guard, scan_guard_enabled)

    class APILogMiddleware(BaseHTTPMiddleware):
        """API call logging: provides data for /logs page, excludes streaming and self-referencing paths"""

        EXCLUDE_PATHS = ["/api/logs", "/api/explore/", "/api/export"]
        SENSITIVE_KEYS = {
            "api_key", "api_keys", "password", "current_password", "new_password",
            "token", "authorization", "cookie",
        }

        @classmethod
        def _redact(cls, value):
            """Recursively redact credentials in request/response logs; original HTTP content is unaffected."""
            if isinstance(value, dict):
                return {
                    key: "***" if str(key).lower() in cls.SENSITIVE_KEYS else cls._redact(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [cls._redact(item) for item in value]
            return value

        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if not should_log_request(path, demo_mode, self.EXCLUDE_PATHS):
                return await call_next(request)

            start_time = time.time()
            method = request.method
            client_ip = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent", "")

            request_body = None
            if method in ("POST", "PUT", "PATCH"):
                try:
                    body_bytes = await request.body()
                    if body_bytes:
                        request_body = body_bytes.decode("utf-8")
                        try:
                            request_body = json.loads(request_body)
                        except (json.JSONDecodeError, ValueError):
                            pass  # Non-JSON body (e.g. multipart), keep raw string
                except Exception as e:
                    print(f"[log] Failed to read request body {path}: {e}")

            headers = dict(request.headers)
            if "authorization" in headers:
                headers["authorization"] = "Bearer ***"
            if "cookie" in headers:
                headers["cookie"] = "***"

            response = await call_next(request)
            duration_ms = int((time.time() - start_time) * 1000)

            response_body = None
            if response.status_code != 204:
                try:
                    body_bytes = b""
                    async for chunk in response.body_iterator:
                        body_bytes += chunk
                    content_type = response.headers.get("content-type", "")
                    response = Response(content=body_bytes, status_code=response.status_code,
                                        headers=dict(response.headers), media_type=response.media_type)
                    if body_bytes:
                        if content_type.startswith("application/json") or content_type.startswith("text/"):
                            response_body = body_bytes.decode("utf-8", errors="replace")
                            try:
                                response_body = json.loads(response_body)
                            except (json.JSONDecodeError, ValueError):
                                pass
                        else:
                            response_body = {"binary_bytes": len(body_bytes), "content_type": content_type}
                except Exception as e:
                    print(f"[log] Failed to read response body {path}: {e}")

            log_type = "agent" if path == "/api/agent/call" else "api"
            title = path
            if log_type == "agent" and isinstance(request_body, dict):
                title = f"Agent: {request_body.get('tool', 'unknown')}"

            if api_logger:
                api_logger.log(log_type=log_type, method=method, path=path, title=title,
                               request_headers=headers, request_body=self._redact(request_body),
                               response_status=response.status_code, response_body=self._redact(response_body),
                               duration_ms=duration_ms, client_ip=client_ip, user_agent=user_agent)
            return response

    app.add_middleware(APILogMiddleware)
    # Registered after APILogMiddleware so it runs first, rejecting denied requests before they
    # reach any handler.
    if demo_mode:
        app.add_middleware(DemoReadOnlyMiddleware)
    # Outermost of the three: a refused probe should cost nothing, not a log write.
    if scan_guard_enabled:
        app.add_middleware(ScanGuardMiddleware, guard=scan_guard)
    app.include_router(api_router)
    app.include_router(explore_router)
    app.include_router(profile_router)
    app.include_router(usage_router)
    app.include_router(security_router)

    # User-uploaded images (data/images) are mounted separately to avoid SPA fallback
    images_dir = os.path.join(_abs(config.get("storage", {}).get("data_dir", "./data")), "images")
    os.makedirs(images_dir, exist_ok=True)
    app.mount("/static/images", StaticFiles(directory=images_dir), name="images")

    # SPA: serve web-ui build artifacts; unknown paths fall back to index.html for React Router
    dist_dir = os.path.join(ROOT_DIR, "web-ui", "dist")
    if os.path.isdir(dist_dir):
        assets_dir = os.path.join(dist_dir, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            candidate = os.path.normpath(os.path.join(dist_dir, full_path))
            # Prevent directory traversal: only allow real files within dist, everything else falls back to index.html
            if candidate.startswith(dist_dir) and os.path.isfile(candidate):
                return FileResponse(candidate)
            # A request for a file that is not here is answered as missing rather than with the app
            # shell. Handing 200 and a full page to /secrets.json tells a scanner the path exists and
            # is worth pursuing, and ScanGuard's rules can only name probes already known, so the
            # gap reopens with every fashion in scanning. React Router's own paths never carry a file
            # extension, which is what separates the two cases.
            if "." in full_path.rsplit("/", 1)[-1]:
                return Response(status_code=404)
            return FileResponse(os.path.join(dist_dir, "index.html"))
    else:
        @app.get("/")
        async def no_frontend():
            return JSONResponse({
                "service": "AsterMem API",
                "hint": "Web UI not built. Run: cd web-ui && npm install && npm run build",
            })

    return app


def reset_admin():
    """
    Escape hatch for forgotten passwords: `python server.py --reset-admin`.
    Only resets credentials to admin/admin and clears sessions; memory data is untouched.
    Requires local read/write access to data/, hence no HTTP equivalent endpoint is provided.
    """
    config, _, _ = load_config()
    storage_cfg = config.get("storage", {})
    database = Database(_abs(storage_cfg.get("database", "./data/memories.db")),
                        whoosh_dir=_abs(storage_cfg.get("whoosh_dir", "./data/whoosh_index")))
    admin = AuthManager(database, config).reset_to_default()

    print("\n🔐 Admin credentials have been reset:")
    print(f"   Username: {admin['username']}")
    print("   Password: admin")
    print("   Memory data is unaffected. Please change your credentials in 'Admin -> Login Settings' immediately after logging in.\n")


def main():
    import uvicorn

    if "--reset-admin" in sys.argv[1:]:
        reset_admin()
        return

    config, config_path, _ = load_config()
    services = build_services(config)
    app = create_app(config, config_path, services)

    # If last vector rebuild was interrupted, auto-resume after startup (depends on init_api in create_app for global assembly)
    from web.api import resume_incomplete_rebuild
    resume_incomplete_rebuild()

    port = config["server"]["port"]
    # ASTERMEM_HOST: behind a reverse proxy the app should bind to loopback, otherwise the
    # origin stays reachable on its raw port and visitors can bypass TLS and the proxy's headers.
    # Defaults to all interfaces so a plain local install is still reachable from other devices.
    host = os.environ.get("ASTERMEM_HOST") or "0.0.0.0"
    print("\nAsterMem is running")
    print(f"  Web UI : http://localhost:{port}")
    print(f"  API    : http://localhost:{port}/api")
    print(f"  Agent  : POST http://localhost:{port}/api/agent/call (Bearer token)\n")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
