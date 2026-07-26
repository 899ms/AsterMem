"""
Web API endpoints

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import io
import json
import re
import zipfile
import yaml
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile, File, Request, Response, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from memory.database import Database
from memory.storage import MemoryStorage
from memory.vector import VectorStore
from memory.embedding import get_embedding_model, get_chat_model
from memory.recall import DEFAULT_NOISE_FLOOR, MAX_NOISE_FLOOR
from memory.search import SearchEngine
from memory.sync import SyncManager
from memory.auth import AuthError, AuthManager, add_sample_memories
from memory.chunker import create_chunker
from memory.task_queue import ChunkingProcessor
from memory.models import TrunkSearchResult
from memory.sync_tasks import get_sync_task_manager, TaskStatus, ItemStatus


# API routes
router = APIRouter(prefix="/api", tags=["memory"])


# Request/Response models
class MemoryCreate(BaseModel):
    title: str
    content: str
    tags: Optional[List[str]] = None
    priority: int = 5


class MemoryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    priority: Optional[int] = None
    status: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    mode: str = "auto"
    limit: int = 10
    tags: Optional[List[str]] = None
    # No default value: hardcoding a number would cause Playground and Explorer to use
    # different noise floors, making the recall visible in the debug page differ from
    # the real pipeline (historical bug: config 0.69 killed all results while Playground was fine)
    min_score: Optional[float] = None
    level: str = "document"  # document / trunk


class ConfigUpdate(BaseModel):
    """
    Background: After AsterMem refactor, config upgraded from "model.mode single-select"
    to a Provider registry.
    Design intent: PUT /config accepts partial updates to the registry; api_keys are
    submitted by provider id, and the server writes them to .env based on the provider's
    api_key_env — plaintext keys never land in config.yaml.
    Key constraint: providers partial merge only accepts whitelisted fields
    (see _PROVIDER_EDITABLE_FIELDS) to prevent unexpected keys from polluting the config.
    """
    providers: Optional[dict] = None          # {providerId: {base_url?, embedding_model?, chat_model?, name?, api_type?, api_key_env?}}
    add_providers: Optional[List[str]] = None
    remove_providers: Optional[List[str]] = None
    active: Optional[dict] = None             # {embedding_provider?, chat_provider?}
    api_keys: Optional[dict] = None           # {providerId: "sk-..."}
    semantic_enabled: Optional[bool] = None
    min_similarity: Optional[float] = None
    api_log_max: Optional[int] = None


class LoginRequest(BaseModel):
    username: str = "admin"
    password: str


class UpdateCredentialsRequest(BaseModel):
    """Change username / password using a single endpoint; both require the current password"""
    current_password: str
    username: Optional[str] = None
    new_password: Optional[str] = None


class LoginProtectionRequest(BaseModel):
    """Toggle login protection; requires current password even when login is disabled, to prevent unauthorized locking"""
    enabled: bool
    current_password: str


class CreateTokenRequest(BaseModel):
    name: str
    scopes: Optional[List[str]] = None


class ImportTextRequest(BaseModel):
    """Import text request"""
    filename: Optional[str] = None
    title: Optional[str] = None
    content: str
    tags: Optional[List[str]] = None
    priority: int = 5
    auto_title: bool = True
    auto_tags: bool = False
    ai_tags: bool = False  # Use AI for automatic tagging


class GenerateTagsRequest(BaseModel):
    """AI tag generation request"""
    title: str
    content: str
    existing_tags: Optional[List[str]] = None


# Global instances
_sync_manager: Optional[SyncManager] = None
_search_engine: Optional[SearchEngine] = None
_auth_manager: Optional[AuthManager] = None
_memory_tools = None
_config: dict = {}
_config_path: str = ""
_chunking_processor: Optional[ChunkingProcessor] = None


def init_api(sync_manager: SyncManager, search_engine: SearchEngine, config: dict, config_path: str, auth_manager: AuthManager = None, memory_tools = None):
    """Initialize the API"""
    global _sync_manager, _search_engine, _config, _config_path, _auth_manager, _memory_tools, _chunking_processor
    _sync_manager = sync_manager
    _search_engine = search_engine
    _config = config
    _config_path = config_path
    _auth_manager = auth_manager
    _memory_tools = memory_tools
    
    # Initialize chunking processor
    try:
        chunker = create_chunker(config)
        _chunking_processor = ChunkingProcessor(
            database=sync_manager.database,
            vector_store=sync_manager.vector_store,
            chunker=chunker,
            config=config
        )
        _chunking_processor.start()
        print("✅ Chunking processor started")
        recovered_count = _chunking_processor.recover_interrupted_documents()
        if recovered_count:
            print(f"✅ Recovered {recovered_count} documents with incomplete chunking")
        
        # Set chunking callbacks for memory_tools
        if memory_tools and _chunking_processor:
            memory_tools.set_chunking_callbacks(
                on_document_changed=_chunking_processor.queue_document_for_chunking,
                on_document_updated=_chunking_processor.rechunk_document
            )
            print("✅ MCP tools bound to chunking processor")
    except Exception as e:
        print(f"⚠️ Chunking processor initialization failed: {e}")
        _chunking_processor = None


def get_chunking_processor() -> Optional[ChunkingProcessor]:
    return _chunking_processor


def get_sync_manager() -> SyncManager:
    if _sync_manager is None:
        raise HTTPException(status_code=500, detail="Service not initialized")
    return _sync_manager


def get_search_engine() -> SearchEngine:
    if _search_engine is None:
        raise HTTPException(status_code=500, detail="Service not initialized")
    return _search_engine


def get_auth_manager() -> AuthManager:
    if _auth_manager is None:
        raise HTTPException(status_code=500, detail="Auth service not initialized")
    return _auth_manager


def get_memory_tools():
    if _memory_tools is None:
        raise HTTPException(status_code=500, detail="MCP tools not initialized")
    return _memory_tools


def _save_config():
    """Persist in-memory config to config.yaml (shared by Provider settings and login protection toggle)"""
    try:
        with open(_config_path, "w", encoding="utf-8") as f:
            yaml.dump(_config, f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {e}")


def _config_view() -> dict:
    """Return a sanitized config view suitable for the web UI or Agent."""
    from memory.providers import PROVIDER_CATALOG, normalize_config, resolve_api_key

    normalize_config(_config)
    search = get_search_engine()
    providers_view = {}
    for pid, entry in (_config.get("providers") or {}).items():
        providers_view[pid] = {
            "name": entry.get("name", pid),
            "api_type": entry.get("api_type", "openai_compatible"),
            "base_url": entry.get("base_url", ""),
            "api_key_env": entry.get("api_key_env", ""),
            "embedding_model": entry.get("embedding_model", ""),
            "chat_model": entry.get("chat_model", ""),
            "has_api_key": bool(resolve_api_key(entry)) or not entry.get("api_key_env"),
        }
    catalog_view = {
        pid: {
            "name": entry.get("name", pid),
            "category": entry.get("category", "other"),
            "api_type": entry.get("api_type", "openai_compatible"),
            "base_url": entry.get("base_url", ""),
            "api_key_env": entry.get("api_key_env", ""),
            "embedding_model": entry.get("embedding_model", ""),
            "chat_model": entry.get("chat_model", ""),
        }
        for pid, entry in PROVIDER_CATALOG.items()
    }
    return {
        "providers": providers_view,
        "provider_catalog": catalog_view,
        "active": _config.get("active", {}),
        "search": {
            "semantic": {
                "enabled": search.semantic_enabled,
                "min_similarity": _config.get("search", {}).get("semantic", {}).get(
                    "min_similarity", DEFAULT_NOISE_FLOOR
                ),
                "min_similarity_max": MAX_NOISE_FLOOR,
            }
        },
        "server": {
            "port": _config.get("server", {}).get("port", _config.get("server", {}).get("mcp_port", 8765)),
            "api_log_max": _config.get("server", {}).get("api_log_max", 1000),
        },
    }


def _update_env_key(key_name: str, key_value: str):
    """Write an API key to .env and sync to the current process environment."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_name):
        raise HTTPException(status_code=400, detail=f"Invalid API key environment variable: {key_name}")
    env_path = os.path.join(os.path.dirname(_config_path), ".env")
    try:
        env_lines = []
        key_found = False
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(f"{key_name}="):
                        env_lines.append(f"{key_name}={key_value}\n")
                        key_found = True
                    else:
                        env_lines.append(line)
        if not key_found:
            env_lines.append(f"{key_name}={key_value}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(env_lines)
        os.environ[key_name] = key_value
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to persist API key: {e}")


def _apply_config_update(data: ConfigUpdate) -> dict:
    """Apply config updates from both web and Agent, return whether vector rebuild is needed."""
    from memory.providers import PROVIDER_CATALOG, normalize_config, get_provider_entry
    from memory.embedding import get_embedding_model

    normalize_config(_config)
    embedding_changed = False
    registry = _config.setdefault("providers", {})

    if data.add_providers:
        for pid in data.add_providers:
            if pid not in PROVIDER_CATALOG:
                raise HTTPException(status_code=400, detail=f"Unknown provider in catalog: {pid}")
            registry.setdefault(pid, dict(PROVIDER_CATALOG[pid]))

    if data.providers:
        for pid, patch in data.providers.items():
            if not isinstance(pid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", pid):
                raise HTTPException(status_code=400, detail=f"Invalid provider id: {pid}")
            if not isinstance(patch, dict):
                continue
            entry = registry.setdefault(pid, dict(PROVIDER_CATALOG.get(pid, {})))
            for key, value in patch.items():
                if key in _PROVIDER_EDITABLE_FIELDS and value is not None:
                    entry[key] = value

    if data.api_keys:
        for pid, key_value in data.api_keys.items():
            entry = get_provider_entry(_config, pid)
            if not entry:
                raise HTTPException(status_code=400, detail=f"Unknown provider: {pid}")
            env_name = (entry.get("api_key_env") or "").strip()
            if not env_name:
                raise HTTPException(status_code=400, detail=f"Provider '{pid}' does not use an API key")
            if not isinstance(key_value, str) or not key_value.strip():
                raise HTTPException(status_code=400, detail=f"API key for '{pid}' is empty")
            _update_env_key(env_name, key_value.strip())

    if data.active:
        active = _config.setdefault("active", {})
        for field in ("embedding_provider", "chat_provider"):
            new_id = data.active.get(field)
            if new_id is None:
                continue
            if new_id == "":
                if field == "embedding_provider" and active.get(field):
                    embedding_changed = True
                active[field] = ""
                continue
            if not get_provider_entry(_config, new_id):
                raise HTTPException(status_code=400, detail=f"Unknown provider: {new_id}")
            if field == "embedding_provider" and active.get(field) != new_id:
                embedding_changed = True
            active[field] = new_id

        if embedding_changed:
            try:
                search = get_search_engine()
                if search.vector_store:
                    embedding_model = get_embedding_model(_config)
                    if embedding_model:
                        search.vector_store.set_embedding_model(embedding_model)
            except Exception as e:
                print(f"[config] Failed to switch embedding model: {e}")

    if data.remove_providers:
        active = _config.get("active", {})
        active_ids = {active.get("embedding_provider"), active.get("chat_provider")}
        for pid in data.remove_providers:
            if pid in active_ids:
                raise HTTPException(status_code=400, detail=f"Provider '{pid}' is currently active")
            registry.pop(pid, None)

    if data.semantic_enabled is not None:
        _config.setdefault("search", {}).setdefault("semantic", {})["enabled"] = data.semantic_enabled
        get_search_engine().set_semantic_enabled(data.semantic_enabled)
    if data.min_similarity is not None:
        # This value is just the noise floor for semantic recall; the higher the floor,
        # the more likely recall goes to zero entirely, so the upper limit is the safe
        # boundary defined by the recall module — exceeding it is rejected outright
        if not 0 <= data.min_similarity <= MAX_NOISE_FLOOR:
            raise HTTPException(
                status_code=400,
                detail=f"min_similarity must be between 0 and {MAX_NOISE_FLOOR}",
            )
        _config.setdefault("search", {}).setdefault("semantic", {})["min_similarity"] = data.min_similarity
        get_search_engine().set_min_similarity(data.min_similarity)
    if data.api_log_max is not None:
        _config.setdefault("server", {})["api_log_max"] = data.api_log_max
        from memory.api_logger import get_api_logger
        api_logger = get_api_logger()
        if api_logger:
            api_logger.MAX_RECORDS = data.api_log_max

    _save_config()
    return {"success": True, "requires_vector_rebuild": embedding_changed}


def _get_similar_tags(content: str, title: str = "", limit: int = 5, min_score: float = 0.3) -> List[str]:
    """
    Find tags from similar articles via semantic search
    
    Args:
        content: Content to search for
        title: Title (optional, combined with content for search)
        limit: Number of similar articles to return
        min_score: Minimum similarity threshold
    
    Returns:
        List of all tags used by similar articles (may contain duplicates for frequency counting)
    """
    try:
        sync = get_sync_manager()
        if not sync.vector_store:
            return []
        
        # Use title + first 500 chars of content as query
        query = f"{title} {content[:500]}" if title else content[:500]
        
        # Semantic search
        similar_results = sync.vector_store.search(query, limit=limit, min_score=min_score)
        
        # Collect tags from all similar articles
        all_tags = []
        for memory_id, score in similar_results:
            memory = sync.database.get_memory(memory_id)
            if memory and memory.tags:
                all_tags.extend(memory.tags)
        
        return all_tags
    except Exception as e:
        print(f"Failed to get similar article tags: {e}")
        return []


# ==================== Auth middleware ====================

async def verify_session(request: Request) -> int:
    """
    Verify session (page authentication). When the admin disables login protection,
    requests are passed through as the sole admin — a no-login mode for self-hosted
    pure-intranet scenarios. The AI channel (Bearer Token) is unaffected by this toggle.
    """
    auth = get_auth_manager()

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token_info = auth.verify_api_token(auth_header[7:])
        if not token_info:
            raise HTTPException(status_code=401, detail="Invalid API Token")
        required_scope = _required_api_scope(request)
        if required_scope not in set(token_info.get("scopes") or []):
            raise HTTPException(status_code=403, detail=f"API Token lacks {required_scope} permission")
        if required_scope == "destructive":
            expected = f"{request.method.upper()} {request.url.path}"
            if request.headers.get("X-AsterMem-Confirm") != expected:
                raise HTTPException(
                    status_code=428,
                    detail=f"Destructive operation requires X-AsterMem-Confirm: {expected}",
                )
        request.state.api_token_info = token_info
        admin_id = auth.get_primary_admin_id()
        if not admin_id:
            raise HTTPException(status_code=500, detail="Admin does not exist")
        return admin_id

    if not auth.is_login_required():
        admin_id = auth.get_primary_admin_id()
        if admin_id:
            return admin_id

    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    admin_id = auth.verify_session(session_id)
    if not admin_id:
        raise HTTPException(status_code=401, detail="Session expired")
    
    return admin_id


def _required_api_scope(request: Request) -> str:
    """Map existing REST routes to Token scopes; web Cookie sessions are unaffected."""
    path = request.url.path
    method = request.method.upper()

    if path.startswith("/api/auth/credentials") or path.startswith("/api/auth/login-protection"):
        return "admin"
    if path.startswith("/api/tokens"):
        return "admin"
    if path.startswith("/api/logs"):
        return "destructive" if method == "DELETE" else "admin"
    if path in {"/api/clear-database", "/api/restart"}:
        return "destructive"
    if method == "DELETE":
        return "destructive"
    if path.startswith(("/api/config", "/api/providers/", "/api/vector-", "/api/trunk-index", "/api/chunking/")):
        return "config"
    if path.startswith((
        "/api/explore/", "/api/search", "/api/quick-match",
        "/api/generate-tags", "/api/smart-import/", "/api/tags/analyze",
    )):
        return "read"
    if method in {"GET", "HEAD"} or path == "/api/export":
        return "read"
    return "write"


@router.get("/skill/raw")
async def read_astermem_skill(request: Request, admin_id: int = Depends(verify_session)):
    """
    Return the full AsterMem Skill text as plain text so AI can learn usage from a single URL.
    Background: Having users manually download a zip and forward it as an attachment often
    causes AI to get stuck on "where is the file"; and zip is binary which fetch tools can't
    read. This endpoint shares the same read scope as /skill/download — AI can self-serve
    with the user's Bearer Token. For environments that can't install Skills, just call the
    REST API directly following the reference text.
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    skill_dir = os.path.join(project_root, "skill", "astermem")

    parts = []
    for filename in ("SKILL.md", "reference.md"):
        path = os.path.join(skill_dir, filename)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            parts.append(f"<!-- {filename} -->\n\n{f.read().strip()}")
    if not parts:
        raise HTTPException(status_code=404, detail="AsterMem Skill package not found")

    base = str(request.base_url).rstrip("/")
    header = (
        "<!-- AsterMem Skill, plain text -->\n\n"
        f"AsterMem base URL: {base}\n"
        "Authenticate every request with `Authorization: Bearer <your AsterMem API token>`.\n"
        "The `scripts/astermem.sh` wrapper used below ships in the installable package at "
        f"`GET {base}/api/skill/download` (zip). Without it, call the REST endpoints listed "
        "in reference.md directly with the same header."
    )
    return Response(
        content=f"{header}\n\n---\n\n" + "\n\n---\n\n".join(parts),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/skill/download")
async def download_astermem_skill(admin_id: int = Depends(verify_session)):
    """Download the AsterMem Skill package installable by Cursor or Claude Code."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    skill_dir = os.path.join(project_root, "skill", "astermem")
    if not os.path.isdir(skill_dir):
        raise HTTPException(status_code=404, detail="AsterMem Skill package not found")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(skill_dir):
            for filename in files:
                source = os.path.join(root, filename)
                relative = os.path.relpath(source, os.path.dirname(skill_dir))
                archive.write(source, relative)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="astermem-skill.zip"'},
    )


@router.get("/methodology")
async def get_methodology(lang: str = "zh-CN"):
    """
    Public methodology introduction content: reads and serves docs/methodology/{lang}.md.
    This endpoint intentionally does not require authentication so the public landing page
    can link to the full methodology; only exposes Markdown files from a fixed language
    list in the repo, not arbitrary file paths.
    """
    supported = {"en", "ko", "zh-TW", "zh-CN", "ja", "fr", "de", "es", "pt", "ru"}
    if lang not in supported:
        lang = "en"
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    doc_dir = os.path.join(project_root, "docs", "methodology")
    path = os.path.join(doc_dir, f"{lang}.md")
    if not os.path.exists(path):
        path = os.path.join(doc_dir, "en.md")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Methodology docs not found")
    with open(path, "r", encoding="utf-8") as f:
        return {"lang": lang, "content": f.read()}


# ==================== Auth API ====================

@router.post("/auth/login")
async def login(data: LoginRequest, response: Response):
    """Admin login"""
    auth = get_auth_manager()
    admin_id = auth.verify_admin(data.username, data.password)
    
    if not admin_id:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    session_id = auth.create_session(admin_id)
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        max_age=86400,  # 24 hours
        samesite="lax"
    )

    admin = auth.get_admin(admin_id) or {}
    return {
        "success": True,
        "message": "Login successful",
        "username": admin.get("username"),
        # Prompt the frontend to remind user to change credentials when still using admin/admin
        "must_change_credentials": bool(admin.get("is_default_credentials")),
    }


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    """Logout"""
    session_id = request.cookies.get("session_id")
    if session_id:
        auth = get_auth_manager()
        auth.delete_session(session_id)
    
    response.delete_cookie("session_id")
    return {"success": True, "message": "Logged out"}


@router.get("/auth/check")
async def check_auth(request: Request):
    """
    Check login status. When not logged in, only returns authenticated / login_required
    fields: username and "is still default credentials" are sensitive info only exposed
    to authenticated sessions.
    """
    auth = get_auth_manager()
    login_required = auth.is_login_required()

    if not login_required:
        admin = auth.get_admin() or {}
        return {
            "authenticated": True,
            "login_required": False,
            "username": admin.get("username"),
            "must_change_credentials": bool(admin.get("is_default_credentials")),
        }

    session_id = request.cookies.get("session_id")
    admin_id = auth.verify_session(session_id) if session_id else None
    if not admin_id:
        return {"authenticated": False, "login_required": True}

    admin = auth.get_admin(admin_id) or {}
    return {
        "authenticated": True,
        "login_required": True,
        "username": admin.get("username"),
        "must_change_credentials": bool(admin.get("is_default_credentials")),
    }


@router.post("/auth/credentials")
async def update_credentials(data: UpdateCredentialsRequest, admin_id: int = Depends(verify_session)):
    """Update username / password (requires current password verification; either can be changed independently)"""
    auth = get_auth_manager()
    try:
        admin = auth.update_credentials(
            admin_id,
            current_password=data.current_password,
            username=data.username,
            new_password=data.new_password,
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status, detail=str(e))

    return {
        "success": True,
        "message": "Credentials updated",
        "username": admin.get("username"),
        "must_change_credentials": bool(admin.get("is_default_credentials")),
    }


@router.post("/auth/login-protection")
async def set_login_protection(data: LoginProtectionRequest, response: Response,
                               admin_id: int = Depends(verify_session)):
    """
    Toggle login protection. When disabled, browser connections bypass login (only
    recommended for pure-intranet self-use). Re-enabling continues using the configured
    credentials. When enabling, a session cookie is issued immediately to prevent the
    user from being kicked back to the login page.
    """
    auth = get_auth_manager()
    if not auth.verify_password(admin_id, data.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    auth.set_login_required(data.enabled)
    _save_config()

    if data.enabled:
        response.set_cookie(
            key="session_id",
            value=auth.create_session(admin_id),
            httponly=True,
            max_age=86400,
            samesite="lax",
        )

    return {
        "success": True,
        "login_required": data.enabled,
        "message": "Login protection enabled" if data.enabled else "Login protection disabled",
    }


# ==================== API Token Management ====================

@router.get("/tokens")
async def list_tokens(admin_id: int = Depends(verify_session)):
    """List all API Tokens"""
    auth = get_auth_manager()
    tokens = auth.list_api_tokens()
    return {"tokens": tokens}


@router.post("/tokens")
async def create_token(data: CreateTokenRequest, request: Request,
                       admin_id: int = Depends(verify_session)):
    """Create an API Token"""
    auth = get_auth_manager()
    caller_token = getattr(request.state, "api_token_info", None)
    if caller_token and not set(data.scopes or []).issubset(set(caller_token.get("scopes") or [])):
        raise HTTPException(status_code=403, detail="API Token cannot create tokens with higher permissions than itself")
    try:
        token = auth.create_api_token(data.name, data.scopes)
    except AuthError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"success": True, "token": token, "message": "Token created, please save it securely"}


@router.get("/tokens/{token_id}/reveal")
async def reveal_token(token_id: int, admin_id: int = Depends(verify_session)):
    """Retrieve full Token value (/api/tokens/* already requires admin permission)"""
    auth = get_auth_manager()
    token = auth.get_api_token_value(token_id)
    if not token:
        raise HTTPException(status_code=404, detail="Token not found or already revoked")
    return {"token": token}


@router.delete("/tokens/{token_id}")
async def delete_token(token_id: int, admin_id: int = Depends(verify_session)):
    """Delete an API Token"""
    auth = get_auth_manager()
    success = auth.delete_api_token(token_id)
    
    if success:
        return {"success": True, "message": "Token deleted"}
    else:
        raise HTTPException(status_code=404, detail="Token not found")


@router.post("/tokens/{token_id}/revoke")
async def revoke_token(token_id: int, admin_id: int = Depends(verify_session)):
    """Revoke an API Token"""
    auth = get_auth_manager()
    success = auth.revoke_api_token(token_id)
    
    if success:
        return {"success": True, "message": "Token revoked"}
    else:
        raise HTTPException(status_code=404, detail="Token not found")


# ==================== Memory API (requires authentication) ====================

@router.get("/memories")
async def list_memories(
    status: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    admin_id: int = Depends(verify_session)
):
    """Get memory list"""
    sync = get_sync_manager()
    tag_list = tags.split(",") if tags else None
    memories = sync.list_memories(status, source, tag_list, limit, offset)
    
    # Attach brief info about the first trunk for each memory (e.g. to determine if it's an image)
    result = []
    for m in memories:
        m_dict = m.to_dict()
        # Get first trunk info
        if m.trunk_ids:
            trunks = sync.database.get_trunks_by_document(m.id)
            if trunks:
                first_trunk = trunks[0]
                m_dict["first_trunk"] = {
                    "content_type": first_trunk.content_type,
                    "image_url": first_trunk.image_url if first_trunk.content_type == "image" else None,
                }
        result.append(m_dict)
    
    return {
        "memories": result,
        "count": len(result)
    }


@router.post("/memories")
async def create_memory(data: MemoryCreate, admin_id: int = Depends(verify_session)):
    """Create a memory"""
    sync = get_sync_manager()
    memory = sync.add_memory(
        title=data.title,
        content=data.content,
        tags=data.tags or [],
        priority=data.priority,
        source="api"
    )
    
    # Automatically trigger chunking
    processor = get_chunking_processor()
    if processor:
        processor.queue_document_for_chunking(memory.id)
    
    return {"success": True, "memory": memory.to_dict()}


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str, admin_id: int = Depends(verify_session)):
    """Get a single memory"""
    sync = get_sync_manager()
    memory = sync.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": memory.to_dict()}


@router.put("/memories/{memory_id}")
async def update_memory(memory_id: str, data: MemoryUpdate, admin_id: int = Depends(verify_session)):
    """Update a memory; triggers re-chunking on content change, consistent with Agent tool behavior"""
    sync = get_sync_manager()
    content_changed = data.content is not None
    memory = sync.update_memory(
        memory_id=memory_id,
        title=data.title,
        content=data.content,
        tags=data.tags,
        priority=data.priority,
        status=data.status
    )
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    if content_changed:
        processor = get_chunking_processor()
        if processor:
            try:
                processor.rechunk_document(memory_id)
            except Exception as e:
                print(f"[WARN] Re-chunking failed after REST update {memory_id}: {e}")
    return {"success": True, "memory": memory.to_dict()}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, hard: bool = False, admin_id: int = Depends(verify_session)):
    """Delete a memory"""
    sync = get_sync_manager()
    success = sync.delete_memory(memory_id, hard_delete=hard)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


@router.get("/memories/{memory_id}/history")
async def get_memory_history(memory_id: str, admin_id: int = Depends(verify_session)):
    """Get memory version history"""
    sync = get_sync_manager()
    memory = sync.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    history = sync.database.get_memory_history(memory_id)
    return {
        "memory": memory.to_dict(),
        "history": [h.to_dict() for h in history]
    }


# ==================== Search API ====================

@router.post("/search")
async def search_memories(data: SearchRequest, admin_id: int = Depends(verify_session)):
    """Search memories (supports document-level and trunk-level)"""
    search = get_search_engine()
    
    if data.level == "trunk":
        # Trunk-level search
        result = search.search_trunks(
            query=data.query,
            mode=data.mode,
            limit=data.limit,
            min_score=data.min_score
        )
        return result
    else:
        # Document-level search (default)
        result = search.search(
            query=data.query,
            mode=data.mode,
            limit=data.limit,
            tags=data.tags,
            min_score=data.min_score
        )
        return result


class QuickMatchRequest(BaseModel):
    text: str
    top_k: int = 6


@router.post("/quick-match")
async def quick_match(data: QuickMatchRequest, admin_id: int = Depends(verify_session)):
    """
    Background: quick_match is the most commonly used retrieval tool in the AI channel
    (supports mem_/trunk_ direct lookup + semantic matching). The web search page also
    needs the same capability for "search like AI" debugging and daily retrieval.
    Design intent: Directly reuses memory_tools.quick_match's retrieval pipeline to ensure
    web results are identical to the SKILL channel; returns raw text results for frontend
    paragraph rendering.
    """
    tools = get_memory_tools()
    if not tools:
        raise HTTPException(status_code=503, detail="Memory tools not initialized")
    try:
        result = tools.quick_match(text=data.text, top_k=data.top_k)
        return {"success": True, "result": result}
    except Exception as e:
        print(f"[api] quick_match failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search/compare")
async def compare_search(data: SearchRequest, admin_id: int = Depends(verify_session)):
    """Compare search modes (supports document-level and trunk-level)"""
    search = get_search_engine()
    
    if data.level == "trunk":
        # Trunk-level comparative search
        result = search.compare_trunk_search(
            query=data.query,
            limit=data.limit
        )
    else:
        # Document-level comparative search (default)
        result = search.compare_search(
            query=data.query,
            limit=data.limit
        )
    return result


@router.get("/memories/{memory_id}/related")
async def get_related_memories(memory_id: str, limit: int = 5, admin_id: int = Depends(verify_session)):
    """Get related memories"""
    search = get_search_engine()
    sync = get_sync_manager()
    
    memory = sync.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    related = search.get_related(memory_id, limit)
    return {
        "memory": memory.to_dict(),
        "related": [r.to_dict() for r in related]
    }


class MetaSearchRequest(BaseModel):
    """Meta tag search request"""
    tag_type: Optional[str] = None
    tag_value: Optional[str] = None
    limit: int = 20


@router.post("/search/meta")
async def search_by_meta_tag(data: MetaSearchRequest, admin_id: int = Depends(verify_session)):
    """Search trunks by meta tag"""
    search = get_search_engine()
    
    result = search.search_by_meta_tag(
        tag_type=data.tag_type,
        tag_value=data.tag_value,
        limit=data.limit
    )
    return result


@router.get("/trunks/{trunk_id}/related")
async def get_related_trunks(trunk_id: str, limit: int = 10, admin_id: int = Depends(verify_session)):
    """Get related content for a specified Trunk (based on meta tags)"""
    search = get_search_engine()
    sync = get_sync_manager()
    
    trunk = sync.database.get_trunk(trunk_id)
    if not trunk:
        raise HTTPException(status_code=404, detail="Trunk not found")
    
    related = search.get_related_by_meta(trunk_id, limit)
    return {
        "trunk": trunk.to_dict(),
        "related": [r.to_dict() for r in related]
    }


@router.get("/meta/tags")
async def get_all_meta_tags(admin_id: int = Depends(verify_session)):
    """Get all meta tags"""
    sync = get_sync_manager()
    tags = sync.database.get_all_meta_tags()
    return {
        "tags": tags,
        "count": len(tags)
    }


# ==================== Sync and Data Management API ====================

@router.post("/sync")
async def sync_user_files(admin_id: int = Depends(verify_session)):
    """Sync user directory"""
    sync = get_sync_manager()
    result = sync.sync_user_files()
    return result


def _run_full_sync_task(task_id: str):
    """Execute full sync task in background"""
    import threading
    
    task_manager = get_sync_task_manager()
    task = task_manager.get_task(task_id)
    if not task:
        return
    
    sync = get_sync_manager()
    
    try:
        # 0. Ensure vector collection exists
        if sync.vector_store:
            try:
                sync.vector_store.ensure_collection()
            except Exception as e:
                print(f"Failed to ensure vector collection exists: {e}")
        
        # 1. Scan all MD files
        all_memories = sync.storage.scan_all_memories()
        
        # Initialize task items
        task_manager.start_task(task, [
            {"id": m.id, "title": m.title} for m in all_memories
        ])
        
        # Get AI model and chunking processor
        chat_model = get_chat_model(_config)
        processor = get_chunking_processor()
        
        for memory in all_memories:
            try:
                # Mark as processing
                task_manager.update_item(task, memory.id, ItemStatus.PROCESSING, "Processing...")
                
                existing = sync.database.get_memory(memory.id)
                
                if existing:
                    # Update existing memory
                    existing.title = memory.title
                    existing.content = memory.content
                    existing.tags = memory.tags
                    existing.priority = memory.priority
                    sync.database.update_memory(existing, save_history=False)
                    task_manager.update_item(task, memory.id, ItemStatus.SKIPPED, "Already exists, updated")
                else:
                    # New memory - trigger full workflow
                    ai_tags = []
                    
                    # AI tagging
                    if chat_model and chat_model.is_available():
                        try:
                            task_manager.update_item(task, memory.id, ItemStatus.PROCESSING, "AI tagging...")
                            similar_tags = _get_similar_tags(memory.content, memory.title, limit=5, min_score=0.4)
                            all_tags = sync.database.get_all_tags()
                            ai_tags = chat_model.generate_tags(
                                memory.title, memory.content, memory.tags,
                                tag_tree=all_tags,
                                similar_tags=similar_tags
                            )
                            if ai_tags:
                                memory.tags = list(dict.fromkeys(memory.tags + ai_tags))
                        except Exception as e:
                            print(f"AI tagging failed ({memory.title}): {e}")
                    
                    # Save to database
                    task_manager.update_item(task, memory.id, ItemStatus.PROCESSING, "Saving to database...")
                    sync.database.add_memory(memory)
                    
                    # Save to MD file (update tags)
                    sync.storage.save_memory(memory)
                    
                    # Add to vector store
                    if sync.vector_store:
                        sync.vector_store.add_memory(memory)
                    
                    # Sync to Whoosh
                    sync.database.sync_to_whoosh(memory)
                    
                    # Trigger chunking
                    if processor:
                        processor.queue_document_for_chunking(memory.id)
                    
                    task_manager.update_item(
                        task, memory.id, ItemStatus.SUCCESS, 
                        f"Done, generated {len(ai_tags)} tags" if ai_tags else "Done",
                        ai_tags=ai_tags
                    )
                    
            except Exception as e:
                task_manager.update_item(task, memory.id, ItemStatus.FAILED, str(e))
        
        # Rebuild vector index
        if sync.vector_store:
            active_memories = sync.database.list_memories(status="active", limit=10000)
            sync.vector_store.rebuild_index(active_memories)
        
        # Rebuild Whoosh index
        sync.database.rebuild_whoosh_index()
        
        task_manager.finish_task(task)
        
    except Exception as e:
        task_manager.finish_task(task, error=str(e))


@router.post("/sync/full")
async def full_sync(admin_id: int = Depends(verify_session)):
    """Full sync (runs asynchronously in background, supports real-time status viewing)"""
    import threading
    
    task_manager = get_sync_task_manager()
    
    # Check if a task is already running
    running = task_manager.get_running_task()
    if running:
        return {
            "success": False,
            "message": "A sync task is already running",
            "task_id": running.id
        }
    
    # Create new task
    task = task_manager.create_task("full_sync")
    
    # Execute in background thread
    thread = threading.Thread(target=_run_full_sync_task, args=(task.id,))
    thread.daemon = True
    thread.start()
    
    return {
        "success": True,
        "message": "Sync task started",
        "task_id": task.id
    }


@router.get("/sync/task/{task_id}")
async def get_sync_task(task_id: str, admin_id: int = Depends(verify_session)):
    """Get sync task status"""
    task_manager = get_sync_task_manager()
    task = task_manager.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return task.to_dict()


@router.get("/sync/tasks")
async def list_sync_tasks(limit: int = 10, admin_id: int = Depends(verify_session)):
    """List sync tasks"""
    task_manager = get_sync_task_manager()
    tasks = task_manager.list_tasks(limit)
    return {
        "tasks": [t.to_dict() for t in tasks]
    }


@router.get("/sync/latest")
async def get_latest_sync_task(admin_id: int = Depends(verify_session)):
    """Get the latest sync task"""
    task_manager = get_sync_task_manager()
    task = task_manager.get_latest_task()
    
    if not task:
        return {"task": None}
    
    return {"task": task.to_dict()}


@router.post("/samples")
async def add_samples(request: Request, admin_id: int = Depends(verify_session)):
    """Add sample data"""
    lang = "en"
    try:
        body = await request.json()
        lang = body.get("lang", "en") if isinstance(body, dict) else "en"
    except Exception:
        pass
    sync = get_sync_manager()
    count = add_sample_memories(sync, lang=lang)
    processor = get_chunking_processor()
    queued = processor.process_pending_documents() if processor else 0
    return {"success": True, "count": count, "queued_for_chunking": queued}


# Tables to clear during factory reset. Ordered by "referencing first" so even with
# foreign key constraints enabled in the future, no errors occur.
_FACTORY_RESET_TABLES = (
    "memory_history",
    "trunk_timeline",
    "chunk_meta_tags",
    "entity_trunk_links",
    "entity_relations",
    "entities",
    "time_events",
    "trunks",
    "memories",
    "profile_field_history",
    "profile_fields",
    "profile_audit_log",
    "profile_claims",
    "profile_dreams",
    "profile_versions",
    "profile_meta",
)


def _data_dir() -> str:
    """data/ root directory. Relative paths in config are resolved relative to the repo root."""
    raw = (_config.get("storage") or {}).get("data_dir", "./data")
    root = os.path.dirname(os.path.abspath(_config_path))
    return raw if os.path.isabs(raw) else os.path.normpath(os.path.join(root, raw))


def _empty_dir(path: str) -> None:
    """
    Clear directory contents but keep the directory itself.

    images/ is mounted by StaticFiles, profile/ is held by ProfileService long-term —
    deleting the entire directory would cause FileNotFoundError on next write.
    """
    import shutil

    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        target = os.path.join(path, name)
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except Exception as e:
            print(f"[WARN] Failed to clear {target}: {e}")


def _backup_data_dir(database: Database) -> str:
    """
    Copy the entire data/ to backups/data_<timestamp>/ before clearing.

    Checkpoint WAL before copying: the last segment of SQLite writes may still only
    be in the -wal file; copying just the .db would produce a backup missing its tail.
    """
    import shutil

    try:
        with database.get_connection() as conn:
            conn.cursor().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:
        print(f"[WARN] WAL checkpoint failed, backup may be missing the last segment of writes: {e}")

    data_dir = _data_dir()
    dest = os.path.join(
        os.path.dirname(data_dir),
        "backups",
        f"data_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copytree(data_dir, dest, dirs_exist_ok=True)
    return dest


@router.post("/clear-database")
async def clear_database(response: Response, admin_id: int = Depends(verify_session)):
    """
    Factory reset: clear all data and reset credentials to admin / admin.

    Background: Early implementation only deleted database business tables, intentionally
    preserving MD originals for re-syncing, but profile tables, trunk_timeline and uploaded
    images were not in scope. After clearing, profile claims would point to non-existent
    memories, violating the hard constraint "every claim must be traceable to source text".
    Design intent: Clear means literally clear — wipe everything cleanly, no half-states.
    Key constraint: Must backup entire data/ to backups/ before proceeding — if backup fails,
    abort; Chroma is cleared unconditionally — when semantic search is off, vector_store is
    None but old vectors remain on disk, and re-enabling semantic search would match deleted
    memories.
    """
    sync = get_sync_manager()
    auth = get_auth_manager()

    try:
        backup_path = _backup_data_dir(sync.database)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed, clearing aborted: {e}")

    with sync.database.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memories")
        deleted = cursor.fetchone()[0]
        for table in _FACTORY_RESET_TABLES:
            try:
                cursor.execute(f"DELETE FROM {table}")
            except Exception as e:
                print(f"[WARN] Failed to clear table {table}: {e}")

    # Delete and recreate collections when handle exists to keep it valid; clear directory directly otherwise
    if sync.vector_store:
        for name in ("memories", "trunks"):
            try:
                sync.vector_store.client.delete_collection(name)
            except Exception as e:
                print(f"[WARN] Failed to delete vector collection {name}: {e}")
        sync.vector_store.ensure_collection()
    else:
        _empty_dir(os.path.join(_data_dir(), "chroma"))

    # MD originals, manual profiles, uploaded images
    for name in ("memories", "profile", "images"):
        _empty_dir(os.path.join(_data_dir(), name))

    try:
        sync.database.rebuild_whoosh_index()
    except Exception as e:
        print(f"[WARN] Failed to rebuild Whoosh index: {e}")

    # Clear AI usage logs along with business data (user explicitly requested: clearing also clears usage)
    from memory.usage_tracker import get_usage_tracker
    usage_tracker = get_usage_tracker()
    if usage_tracker:
        try:
            usage_tracker.clear()
        except Exception as e:
            print(f"[WARN] Failed to clear AI usage logs: {e}")

    # Reset credentials to admin / admin, invalidate all tokens and sessions
    auth.reset_to_default()
    with sync.database.get_connection() as conn:
        conn.cursor().execute("DELETE FROM api_tokens")
    response.delete_cookie("session_id")

    return {
        "success": True,
        "deleted": deleted,
        "backup_path": backup_path,
        "signed_out": True,
        "message": f"All data cleared and credentials reset, backup at {backup_path}",
    }


@router.post("/restart")
async def restart_service(admin_id: int = Depends(verify_session)):
    """
    Restart the service. On POSIX, directly execv to replace the current image; Windows
    lacks true exec semantics (execv spawns a new process and the parent exits immediately,
    the port isn't released in time so the new process binding always fails), so we spawn
    a relay process that "waits 2 seconds then exec", then self-terminate to release the port.
    """
    import asyncio
    import subprocess
    import sys
    import os
    
    async def delayed_restart():
        await asyncio.sleep(1)  # Wait for response to return
        if os.name == "nt":
            relay = "import os, sys, time; time.sleep(2); os.execv(sys.argv[1], sys.argv[1:])"
            subprocess.Popen(
                [sys.executable, "-c", relay, sys.executable, *sys.argv],
                cwd=os.getcwd(),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            )
            os._exit(0)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    
    # Start delayed restart task
    asyncio.create_task(delayed_restart())
    
    return {"success": True, "message": "Service will restart in 1 second"}


@router.post("/export")
async def export_memories(
    status: Optional[str] = None,
    tags: Optional[str] = None,
    admin_id: int = Depends(verify_session)
):
    """Export memories"""
    sync = get_sync_manager()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_path = os.path.join(
        os.path.dirname(_config_path),
        "data",
        f"export_{timestamp}.zip"
    )
    
    tag_list = tags.split(",") if tags else None
    sync.export_memories(export_path, status, tag_list)
    
    return FileResponse(
        export_path,
        media_type="application/zip",
        filename=f"memories_{timestamp}.zip"
    )


@router.post("/import")
async def import_memories(file: UploadFile = File(...), admin_id: int = Depends(verify_session)):
    """Import memories (ZIP format)"""
    sync = get_sync_manager()
    
    import_path = os.path.join(
        os.path.dirname(_config_path),
        "data",
        f"import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    )
    
    with open(import_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    result = sync.import_memories(import_path)
    os.remove(import_path)
    
    return result


@router.post("/import-text")
async def import_text_memory(data: ImportTextRequest, admin_id: int = Depends(verify_session)):
    """
    Import text memory
    Supports importing from txt/md file content or plain text
    """
    import re
    
    sync = get_sync_manager()
    
    content = data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    
    # Determine title
    title = data.title
    if not title and data.auto_title:
        # Auto-extract title from content
        title = _extract_title_from_content(content)
    if not title and data.filename:
        # Extract title from filename
        title = os.path.splitext(data.filename)[0]
    if not title:
        title = "Imported memory"
    
    # Determine tags
    tags = data.tags or []
    if data.auto_tags:
        # Extract tags from filename
        if data.filename:
            filename_tags = _extract_tags_from_filename(data.filename)
            tags.extend(filename_tags)
        # Extract tags from content (if frontmatter format)
        content_tags, content = _extract_frontmatter_tags(content)
        tags.extend(content_tags)
    
    # Use AI for automatic tagging
    ai_generated_tags = []
    if data.ai_tags:
        try:
            chat_model = get_chat_model(_config)
            if chat_model and chat_model.is_available():
                # 1. Find similar article tags via semantic search
                similar_tags = _get_similar_tags(content, title, limit=5, min_score=0.4)
                
                # 2. Get existing tag system
                all_tags = sync.database.get_all_tags()
                
                # 3. Generate tags (prioritize similar article tags)
                ai_generated_tags = chat_model.generate_tags(
                    title, content, tags, 
                    tag_tree=all_tags,
                    similar_tags=similar_tags
                )
                tags.extend(ai_generated_tags)
        except Exception as e:
            print(f"AI tagging failed: {e}")
    
    # Deduplicate tags
    tags = list(dict.fromkeys(tags))
    
    try:
        # Create memory
        memory = sync.add_memory(
            title=title,
            content=content,
            tags=tags,
            priority=data.priority,
            source="api"
        )
        
        # Automatically trigger chunking
        processor = get_chunking_processor()
        if processor:
            processor.queue_document_for_chunking(memory.id)
        
        return {
            "success": True,
            "memory": memory.to_dict(),
            "message": f"Successfully imported memory: {title}",
            "ai_tags": ai_generated_tags if data.ai_tags else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@router.post("/import-image")
async def import_image_memory(
    file: UploadFile = File(...),
    title: Optional[str] = None,
    tags: Optional[str] = None,
    admin_id: int = Depends(verify_session)
):
    """
    Import image memory
    
    Each image creates an independent document and a trunk.
    Automatically extracts EXIF, generates tags, descriptions and OCR.
    """
    import base64
    import io
    
    sync = get_sync_manager()
    
    # Validate file type
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported image format: {file.content_type}")
    
    # Read image content
    image_data = await file.read()
    
    # Generate base64
    image_base64 = base64.b64encode(image_data).decode('utf-8')
    
    # Extract EXIF info
    exif_data = _extract_exif(image_data)
    
    # Determine title
    if not title:
        # Extract date from EXIF as title, or use filename
        if exif_data.get("DateTime"):
            title = f"Image {exif_data['DateTime']}"
        else:
            title = file.filename or "Untitled image"
    
    # Parse tags
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    
    # Save image to data/images directory
    from memory.models import generate_memory_id, generate_trunk_id, Trunk
    
    memory_id = generate_memory_id()
    trunk_id = generate_trunk_id()
    
    # Ensure image directory exists
    images_dir = os.path.join(os.path.dirname(_config_path), "data", "images")
    os.makedirs(images_dir, exist_ok=True)
    
    # Save image
    file_ext = os.path.splitext(file.filename)[1] if file.filename else ".jpg"
    image_filename = f"{trunk_id}{file_ext}"
    image_path = os.path.join(images_dir, image_filename)
    
    with open(image_path, "wb") as f:
        f.write(image_data)
    
    # Image access URL
    image_url = f"/static/images/{image_filename}"
    
    try:
        # Create memory (document)
        memory = sync.add_memory(
            title=title,
            content=f"![{title}]({image_url})",  # Markdown image format
            tags=tag_list,
            priority=5,
            source="api"
        )
        
        # Create Trunk
        trunk = Trunk(
            id=trunk_id,
            document_id=memory.id,
            order=0,
            content=image_path,  # Store file path
            content_type="image",
            status="pending",
            meta_status="pending",
            image_url=image_url,
            image_exif=exif_data if exif_data else None
        )
        
        sync.database.add_trunk(trunk)
        
        # Update memory trunk info
        sync.database.update_memory_trunk_status(memory.id, "chunking", [trunk_id])
        
        # Add to processing queue (async processing of tags, descriptions, OCR)
        processor = get_chunking_processor()
        if processor:
            processor.task_queue.add_extract_meta_task(trunk_id, memory.id)
        
        return {
            "success": True,
            "memory": memory.to_dict(),
            "trunk": trunk.to_dict(),
            "message": f"Image uploaded: {title}"
        }
        
    except Exception as e:
        # Clean up saved image
        if os.path.exists(image_path):
            os.remove(image_path)
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


def _extract_exif(image_data: bytes) -> dict:
    """Extract image EXIF information"""
    exif_info = {}
    
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        import io
        
        image = Image.open(io.BytesIO(image_data))
        
        # Get basic info
        exif_info["Width"] = image.width
        exif_info["Height"] = image.height
        exif_info["Format"] = image.format
        exif_info["Mode"] = image.mode
        
        # Get EXIF data
        exif_data = image._getexif()
        if exif_data:
            for tag_id, value in exif_data.items():
                tag_name = TAGS.get(tag_id, tag_id)
                
                # Skip unnecessary large data
                if tag_name in ['MakerNote', 'UserComment', 'ComponentsConfiguration', 
                               'FileSource', 'SceneType', 'CFAPattern']:
                    continue
                
                # Handle GPS info
                if tag_name == 'GPSInfo':
                    gps_info = {}
                    for gps_tag_id, gps_value in value.items():
                        gps_tag_name = GPSTAGS.get(gps_tag_id, gps_tag_id)
                        # Convert to serializable format
                        if hasattr(gps_value, '__iter__') and not isinstance(gps_value, (str, bytes)):
                            gps_value = list(gps_value)
                        gps_info[gps_tag_name] = str(gps_value)
                    exif_info["GPSInfo"] = gps_info
                else:
                    # Convert to serializable format
                    if isinstance(value, bytes):
                        value = value.decode('utf-8', errors='ignore')
                    elif hasattr(value, '__iter__') and not isinstance(value, str):
                        value = str(value)
                    exif_info[tag_name] = str(value) if not isinstance(value, (str, int, float)) else value
        
    except ImportError:
        print("PIL not installed, skipping EXIF extraction")
    except Exception as e:
        print(f"EXIF extraction failed: {e}")
    
    return exif_info


def _extract_title_from_content(content: str) -> str:
    """Extract title from content"""
    import re
    
    lines = content.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line:
            # Remove Markdown heading symbols
            title = re.sub(r"^#+\s*", "", line)
            # Limit length
            return title[:100] if len(title) > 100 else title
    return "Untitled"


def _extract_tags_from_filename(filename: str) -> List[str]:
    """Extract tags from filename"""
    import re
    
    # Remove extension
    name = os.path.splitext(filename)[0]
    
    # Find tags in square brackets [tag1][tag2]
    bracket_tags = re.findall(r'\[([^\]]+)\]', name)
    
    # Find hash tags #tag1 #tag2
    hash_tags = re.findall(r'#(\w+)', name)
    
    return bracket_tags + hash_tags


def _extract_frontmatter_tags(content: str) -> tuple:
    """Extract tags from frontmatter"""
    import re
    
    tags = []
    
    # Check for frontmatter
    frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if frontmatter_match:
        frontmatter = frontmatter_match.group(1)
        # Extract tags field
        tags_match = re.search(r'tags:\s*\[([^\]]+)\]', frontmatter)
        if tags_match:
            tags = [t.strip().strip('"\'') for t in tags_match.group(1).split(',')]
        else:
            # Try YAML list format
            tags_match = re.search(r'tags:\s*\n((?:\s*-\s*.+\n)+)', frontmatter)
            if tags_match:
                tags = [t.strip().lstrip('- ').strip('"\'') for t in tags_match.group(1).strip().split('\n')]
        
        # Remove frontmatter, keep only content
        content = content[frontmatter_match.end():]
    
    return tags, content


@router.post("/generate-tags")
async def generate_tags_api(data: GenerateTagsRequest, admin_id: int = Depends(verify_session)):
    """
    Use AI to generate hierarchical tags for content.
    Based on semantic search to find similar article tags + existing tag system,
    format: level1/level2/level3
    """
    try:
        chat_model = get_chat_model(_config)
        
        if not chat_model:
            return {
                "success": False,
                "message": "AI model not configured, please check settings"
            }
        
        if not chat_model.is_available():
            return {
                "success": False,
                "message": "Cannot connect to LM Studio, please ensure the service is running"
            }
        
        # 1. Find similar article tags via semantic search
        similar_tags = _get_similar_tags(data.content, data.title, limit=5, min_score=0.4)
        
        # 2. Get existing tag system
        sync = get_sync_manager()
        all_tags = sync.database.get_all_tags()
        
        # 3. Generate tags (prioritize similar article tags)
        tags = chat_model.generate_tags(
            title=data.title,
            content=data.content,
            existing_tags=data.existing_tags,
            tag_tree=all_tags,
            similar_tags=similar_tags
        )
        
        if tags:
            return {
                "success": True,
                "tags": tags,
                "similar_tags_found": len(set(similar_tags)) if similar_tags else 0,
                "message": f"Successfully generated {len(tags)} hierarchical tags"
            }
        else:
            return {
                "success": False,
                "tags": [],
                "message": "AI could not generate tags, please try again later"
            }
            
    except Exception as e:
        return {
            "success": False,
            "message": f"Tag generation failed: {str(e)}"
        }


# ==================== Stats and Config API ====================

@router.get("/stats")
async def get_stats(admin_id: int = Depends(verify_session)):
    """Get statistics"""
    sync = get_sync_manager()
    return sync.get_stats()


@router.get("/tags")
async def get_all_tags(admin_id: int = Depends(verify_session)):
    """Get all tags"""
    sync = get_sync_manager()
    tags = sync.database.get_all_tags()
    return {"tags": tags}


@router.get("/tags/stats")
async def get_tags_stats(admin_id: int = Depends(verify_session)):
    """Get all tags and their usage counts (including Memory and Trunk levels)"""
    sync = get_sync_manager()
    
    with sync.database.get_connection() as conn:
        cursor = conn.cursor()
        
        tag_counts = {}
        
        # 1. Count Memory-level tags
        cursor.execute("SELECT tags FROM memories WHERE status = 'active'")
        for row in cursor.fetchall():
            tags = row["tags"]
            if tags:
                tag_list = json.loads(tags)
                for tag in tag_list:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        # 2. Count Trunk-level tags
        cursor.execute("SELECT tags FROM trunks WHERE status = 'ready'")
        for row in cursor.fetchall():
            tags = row["tags"]
            if tags:
                tag_list = json.loads(tags)
                for tag in tag_list:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        # Sort by usage count
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "tags": [{"name": name, "count": count} for name, count in sorted_tags],
            "total": len(sorted_tags)
        }


@router.get("/tags/tree")
async def get_tags_tree(admin_id: int = Depends(verify_session)):
    """
    Get the hierarchical tree structure of tags (including Memory and Trunk levels).
    Returns the hierarchical relationships of multi-level tags, format:
    {
        "tree": {
            "Technology": {
                "count": 5,
                "children": {
                    "Programming": {
                        "count": 3,
                        "children": {
                            "Python": {"count": 2, "children": {}},
                            "JavaScript": {"count": 1, "children": {}}
                        }
                    }
                }
            }
        },
        "flat_tags": [{"name": "Technology/Programming/Python", "count": 2}, ...]
    }
    """
    sync = get_sync_manager()
    
    with sync.database.get_connection() as conn:
        cursor = conn.cursor()
        
        # Count usage for each full tag path
        tag_counts = {}
        
        # 1. Count Memory-level tags
        cursor.execute("SELECT tags FROM memories WHERE status = 'active'")
        for row in cursor.fetchall():
            tags = row["tags"]
            if tags:
                tag_list = json.loads(tags)
                for tag in tag_list:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        # 2. Count Trunk-level tags
        cursor.execute("SELECT tags FROM trunks WHERE status = 'ready'")
        for row in cursor.fetchall():
            tags = row["tags"]
            if tags:
                tag_list = json.loads(tags)
                for tag in tag_list:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        # Build tree structure
        tree = {}
        for tag, count in tag_counts.items():
            parts = tag.split('/')
            current = tree
            for i, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                if part not in current:
                    current[part] = {"count": 0, "children": {}, "full_path": '/'.join(parts[:i+1])}
                # Only accumulate count at leaf nodes
                if i == len(parts) - 1:
                    current[part]["count"] += count
                current = current[part]["children"]
        
        # Sorted flat list by usage count
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        flat_tags = [{"name": name, "count": count} for name, count in sorted_tags]
        
        return {
            "tree": tree,
            "flat_tags": flat_tags,
            "total": len(tag_counts)
        }


class TagNormalizeRequest(BaseModel):
    """Tag normalization analysis request"""
    batch_size: int = 100  # Number of tags per batch
    batch_index: int = 0  # Current batch index


@router.post("/tags/analyze")
async def analyze_tags_for_normalization(
    data: TagNormalizeRequest = TagNormalizeRequest(),
    admin_id: int = Depends(verify_session)
):
    """Use AI to analyze tags and find groups that can be normalized (batch processing)"""
    try:
        chat_model = get_chat_model(_config, caller="tagging")
        
        if not chat_model:
            return {
                "success": False,
                "message": "AI model not configured, please check settings"
            }
        
        if not chat_model.is_available():
            return {
                "success": False,
                "message": "Cannot connect to LM Studio, please ensure the service is running"
            }
        
        # Get all tags and usage counts
        sync = get_sync_manager()
        with sync.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tags FROM memories WHERE status = 'active'")
            rows = cursor.fetchall()
            
            tag_counts = {}
            for row in rows:
                tags = row["tags"]
                if tags:
                    tag_list = json.loads(tags)
                    for tag in tag_list:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        if not tag_counts:
            return {
                "success": True,
                "groups": [],
                "total_tags": 0,
                "total_batches": 0,
                "current_batch": 0,
                "has_more": False,
                "message": "No tags found"
            }
        
        # Sort by usage count
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        total_tags = len(sorted_tags)
        
        # Calculate batch info
        batch_size = min(data.batch_size, 100)  # Max 100 tags per batch
        total_batches = (total_tags + batch_size - 1) // batch_size
        current_batch = min(data.batch_index, total_batches - 1)
        
        # Get current batch tags
        start_idx = current_batch * batch_size
        end_idx = min(start_idx + batch_size, total_tags)
        batch_tags = sorted_tags[start_idx:end_idx]
        
        has_more = (current_batch + 1) < total_batches
        
        # Build tag list string
        tags_with_count = [f"{tag}({count})" for tag, count in batch_tags]
        tags_str = ", ".join(tags_with_count)
        
        prompt = f"""Please analyze the following tag list, find tags with similar or duplicate meanings, and suggest merging them into unified tags.

Tag list (usage count in parentheses):
{tags_str}

Please find groups of tags that can be normalized, each group containing:
1. Suggested unified tag name (choose the most commonly used or most standard one)
2. List of original tags to be merged

Output format (strictly follow this JSON format, no other content):
[
  {{"target": "unified_tag_name", "sources": ["original_tag1", "original_tag2"]}},
  {{"target": "unified_tag_name2", "sources": ["original_tag3", "original_tag4"]}}
]

Notes:
1. Only output a JSON array, no other explanation
2. Only merge tags that truly have the same or very similar meaning
3. If no tags need merging, return an empty array []
4. Consider case sensitivity, traditional/simplified Chinese, synonyms, abbreviations, etc."""

        response = chat_model.chat([
            {"role": "user", "content": prompt}
        ], temperature=0.1)
        
        # Parse AI returned JSON
        import re
        valid_groups = []
        
        # Try to extract JSON array
        json_match = re.search(r'\[[\s\S]*\]', response)
        if json_match:
            try:
                groups = json.loads(json_match.group())
                # Validate format
                for g in groups:
                    if isinstance(g, dict) and "target" in g and "sources" in g:
                        # Ensure sources actually exist in tags
                        existing_sources = [s for s in g["sources"] if s in tag_counts]
                        if existing_sources and g["target"]:
                            valid_groups.append({
                                "target": g["target"],
                                "sources": existing_sources,
                                "total_count": sum(tag_counts.get(s, 0) for s in existing_sources)
                            })
            except json.JSONDecodeError:
                pass
        
        batch_info = f"Batch {current_batch + 1}/{total_batches}"
        if valid_groups:
            message = f"{batch_info}: found {len(valid_groups)} groups of tags to normalize"
        else:
            message = f"{batch_info}: no tags need normalization"
        
        return {
            "success": True,
            "groups": valid_groups,
            "total_tags": total_tags,
            "total_batches": total_batches,
            "current_batch": current_batch,
            "has_more": has_more,
            "batch_range": f"{start_idx + 1}-{end_idx}",
            "message": message
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Tag analysis failed: {str(e)}"
        }


class TagMergeRequest(BaseModel):
    """Tag merge request"""
    target: str  # Target tag
    sources: List[str]  # Source tags to merge


@router.post("/tags/merge")
async def merge_tags(data: TagMergeRequest, admin_id: int = Depends(verify_session)):
    """Merge tags: replace multiple source tags with the target tag"""
    try:
        sync = get_sync_manager()
        updated_count = 0
        
        with sync.database.get_connection() as conn:
            cursor = conn.cursor()
            
            # Find memories containing any source tag
            cursor.execute("SELECT id, tags FROM memories WHERE status = 'active'")
            rows = cursor.fetchall()
            
            for row in rows:
                memory_id = row["id"]
                tags_json = row["tags"]
                
                if not tags_json:
                    continue
                
                tags = json.loads(tags_json)
                original_tags = tags.copy()
                
                # Check if contains any source tag
                has_source = any(s in tags for s in data.sources)
                if not has_source:
                    continue
                
                # Remove all source tags
                tags = [t for t in tags if t not in data.sources]
                
                # Add target tag (if not present)
                if data.target not in tags:
                    tags.append(data.target)
                
                # If tags changed, update database
                if set(tags) != set(original_tags):
                    cursor.execute(
                        "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), datetime.now().isoformat(), memory_id)
                    )
                    updated_count += 1
                    
                    # Sync update vector store metadata
                    try:
                        memory = sync.database.get_memory(memory_id)
                        if memory:
                            memory.tags = tags
                            sync.vector_store.update_metadata(memory_id, {
                                "tags": ",".join(tags)
                            })
                    except Exception as e:
                        print(f"Failed to update vector metadata: {e}")
        
        return {
            "success": True,
            "updated": updated_count,
            "message": f"Successfully merged {len(data.sources)} tags into '{data.target}', updated {updated_count} memories"
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Tag merge failed: {str(e)}"
        }


class TagRenameRequest(BaseModel):
    """Tag rename request"""
    old_name: str
    new_name: str


@router.post("/tags/rename")
async def rename_tag(data: TagRenameRequest, admin_id: int = Depends(verify_session)):
    """Rename a single tag"""
    try:
        sync = get_sync_manager()
        updated_count = 0
        
        with sync.database.get_connection() as conn:
            cursor = conn.cursor()
            
            # Find memories containing this tag
            cursor.execute(
                "SELECT id, tags FROM memories WHERE tags LIKE ?",
                (f'%"{data.old_name}"%',)
            )
            rows = cursor.fetchall()
            
            for row in rows:
                memory_id = row["id"]
                tags_json = row["tags"]
                
                if not tags_json:
                    continue
                
                tags = json.loads(tags_json)
                
                if data.old_name in tags:
                    # Replace tag
                    tags = [data.new_name if t == data.old_name else t for t in tags]
                    # Deduplicate
                    tags = list(dict.fromkeys(tags))
                    
                    cursor.execute(
                        "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), datetime.now().isoformat(), memory_id)
                    )
                    updated_count += 1
                    
                    # Sync update vector store metadata
                    try:
                        memory = sync.database.get_memory(memory_id)
                        if memory:
                            memory.tags = tags
                            sync.vector_store.update_metadata(memory_id, {
                                "tags": ",".join(tags)
                            })
                    except Exception as e:
                        print(f"Failed to update vector metadata: {e}")
        
        return {
            "success": True,
            "updated": updated_count,
            "message": f"Successfully renamed '{data.old_name}' to '{data.new_name}', updated {updated_count} memories"
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Tag rename failed: {str(e)}"
        }


class TagDeleteRequest(BaseModel):
    """Delete tag request"""
    tag: str


@router.post("/tags/delete")
async def delete_tag(data: TagDeleteRequest, admin_id: int = Depends(verify_session)):
    """Remove a specified tag from all memories"""
    try:
        sync = get_sync_manager()
        updated_count = 0
        
        with sync.database.get_connection() as conn:
            cursor = conn.cursor()
            
            # Find memories containing this tag
            cursor.execute(
                "SELECT id, tags FROM memories WHERE tags LIKE ?",
                (f'%"{data.tag}"%',)
            )
            rows = cursor.fetchall()
            
            for row in rows:
                memory_id = row["id"]
                tags_json = row["tags"]
                
                if not tags_json:
                    continue
                
                tags = json.loads(tags_json)
                
                if data.tag in tags:
                    tags.remove(data.tag)
                    
                    cursor.execute(
                        "UPDATE memories SET tags = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), datetime.now().isoformat(), memory_id)
                    )
                    updated_count += 1
                    
                    # Sync update vector store metadata
                    try:
                        sync.vector_store.update_metadata(memory_id, {
                            "tags": ",".join(tags)
                        })
                    except Exception as e:
                        print(f"Failed to update vector metadata: {e}")
        
        return {
            "success": True,
            "updated": updated_count,
            "message": f"Successfully deleted tag '{data.tag}', updated {updated_count} memories"
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Tag deletion failed: {str(e)}"
        }


# Provider entry editable fields whitelist: prevent arbitrary keys from being written to config file
_PROVIDER_EDITABLE_FIELDS = {"name", "api_type", "base_url", "api_key_env", "embedding_model", "chat_model", "vlm_model"}


@router.get("/config")
async def get_config(admin_id: int = Depends(verify_session)):
    """
    Background: The settings page needs the full provider registry view to render config cards.
    Design intent: Return registry + active selection + search/server settings; only a
    has_api_key boolean is sent back for keys — plaintext never leaves the server
    (the old version echoed keys back, which was a security flaw, now fixed).
    """
    return _config_view()


@router.put("/config")
async def update_config(data: ConfigUpdate, admin_id: int = Depends(verify_session)):
    """
    Background: The settings page saves provider registry / active selection / search & log settings.
    Design intent: Partial merge semantics — only update submitted fields; API keys are written
    to .env per each provider's api_key_env and synced into process env vars; the config file
    itself never contains secrets. When switching active embedding provider, the VectorStore's
    embedding model is replaced in-place; vector rebuild is triggered explicitly by the frontend
    via /vector-rebuild (dimension changes make old indexes unusable, and rebuilding is a
    user-visible time-consuming operation that should not happen silently on config save).
    Key constraint: config.yaml write failure returns 500 without swallowing the error;
    provider fields are validated against a whitelist.
    """
    result = _apply_config_update(data)
    return {**result, "message": "Configuration updated"}


@router.post("/providers/{provider_id}/test")
async def test_provider_connection(
    provider_id: str,
    request: Request,
    admin_id: int = Depends(verify_session),
):
    """
    Background: Each provider card on the settings page has a Test button, replacing the
    old four test-connection-* endpoints.
    Design intent: Delegates to providers.test_provider to validate both embedding (returns
    dimension) and chat; makes real upstream calls rather than just checking if a key exists,
    so "test passed" truly means "it works".
    """
    from memory.providers import test_provider
    try:
        body: dict = {}
        try:
            candidate = await request.json()
            if isinstance(candidate, dict):
                body = candidate
        except Exception:
            pass
        entry_override = {
            field: body[field]
            for field in ("base_url", "embedding_model", "chat_model")
            if isinstance(body.get(field), str)
        }
        api_key_override = body.get("api_key")
        if not isinstance(api_key_override, str) or not api_key_override.strip():
            api_key_override = None
        return test_provider(
            _config,
            provider_id,
            entry_override=entry_override,
            api_key_override=api_key_override,
        )
    except Exception as e:
        print(f"[api] Provider test failed {provider_id}: {e}")
        return {"success": False, "message": str(e)}


@router.post("/providers/{provider_id}/models")
async def list_provider_models(provider_id: str, request: Request, admin_id: int = Depends(verify_session)):
    """Fetch available models from a provider's /models endpoint.
    Accepts optional {api_key, base_url} in body to override saved values (for unsaved drafts).
    """
    from memory.providers import get_provider_entry, resolve_api_key
    entry = get_provider_entry(_config, provider_id)
    if not entry:
        return {"models": [], "error": f"Provider '{provider_id}' not found"}

    override: dict = {}
    try:
        override = await request.json()
    except Exception:
        pass

    base_url = (override.get("base_url") or entry.get("base_url") or "").rstrip("/")
    api_key = override.get("api_key") or resolve_api_key(entry)
    api_type = entry.get("api_type", "openai_compatible")

    if api_type == "anthropic":
        return {"models": [], "note": "Anthropic does not expose a public model listing endpoint."}

    if api_type == "gemini":
        url = f"{base_url}/models?key={api_key}" if api_key else f"{base_url}/models"
        headers: dict = {"Content-Type": "application/json"}
    else:
        url = f"{base_url}/models"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        if api_type == "gemini":
            raw = body.get("models", [])
            ids = sorted(
                m.get("name", "").replace("models/", "") for m in raw if m.get("name")
            )
        else:
            raw = body.get("data", body.get("models", []))
            if isinstance(raw, list):
                ids = sorted(m.get("id") or m.get("name", "") for m in raw if isinstance(m, dict))
            else:
                ids = []

        return {"models": ids}
    except Exception as e:
        return {"models": [], "error": str(e)}


@router.get("/vector-status")
async def get_vector_status(admin_id: int = Depends(verify_session)):
    """Get vector status (trunk-level)"""
    try:
        sync = get_sync_manager()
        search = get_search_engine()
        
        # Get all active memories
        all_memories = sync.database.list_memories(status="active", limit=10000)
        total_memories = len(all_memories)
        all_memory_ids = {m.id for m in all_memories}
        
        # Get all trunks
        all_trunks = []
        for m in all_memories:
            trunks = sync.database.get_trunks_by_document(m.id)
            all_trunks.extend(trunks)
        
        total_trunks = len(all_trunks)
        all_trunk_ids = {t.id for t in all_trunks}
        
        # Get trunk IDs in the vector store
        vector_trunk_ids = set()
        vector_memory_ids = set()
        if search.vector_store:
            vector_trunk_ids = set(search.vector_store.get_all_trunk_ids())
            vector_memory_ids = set(search.vector_store.get_all_ids())

        vectorized_memory_ids = all_memory_ids & vector_memory_ids
        memory_vectorized_count = len(vectorized_memory_ids)
        memory_not_vectorized_count = total_memories - memory_vectorized_count
        
        # Vectorized = intersection of vector store IDs and active trunk IDs
        vectorized_ids = all_trunk_ids & vector_trunk_ids
        vectorized_count = len(vectorized_ids)
        
        # Not vectorized = active trunks - vectorized
        not_vectorized_ids = all_trunk_ids - vectorized_ids
        not_vectorized_count = len(not_vectorized_ids)
        
        # Orphan vectors = exist in vector store but trunk no longer exists
        orphan_ids = vector_trunk_ids - all_trunk_ids
        orphan_count = len(orphan_ids)
        
        # Get details of non-vectorized memories (grouped by document, max 20)
        not_vectorized_list = []
        for m in all_memories:
            trunks = sync.database.get_trunks_by_document(m.id)
            unvectorized_trunks = [t for t in trunks if t.id in not_vectorized_ids]
            if unvectorized_trunks:
                not_vectorized_list.append({
                    "id": m.id,
                    "title": m.title,
                    "created_at": m.created_at,
                    "trunk_count": len(trunks),
                    "unvectorized_trunk_count": len(unvectorized_trunks)
                })
                if len(not_vectorized_list) >= 20:
                    break
        
        # Calculate document-level statistics
        docs_with_trunks = sum(1 for m in all_memories if m.trunk_status == "ready")
        docs_no_trunks = total_memories - docs_with_trunks
        
        return {
            "semantic_enabled": search.semantic_enabled,
            "vector_store_available": search.vector_store is not None,
            # Memory-level statistics
            "memory_vectorized_count": memory_vectorized_count,
            "memory_not_vectorized_count": memory_not_vectorized_count,
            "memory_vectorized_percent": round(
                memory_vectorized_count / total_memories * 100, 1
            ) if total_memories > 0 else 0,
            # Trunk-level statistics
            "total_trunks": total_trunks,
            "vectorized_count": vectorized_count,
            "not_vectorized_count": not_vectorized_count,
            "orphan_count": orphan_count,
            "vectorized_percent": round(vectorized_count / total_trunks * 100, 1) if total_trunks > 0 else 0,
            # Document-level statistics (backward compatible with old UI)
            "total_memories": total_memories,
            "docs_with_trunks": docs_with_trunks,
            "docs_no_trunks": docs_no_trunks,
            "not_vectorized_list": not_vectorized_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Rebuild index task status
_rebuild_task = {
    "running": False,
    "phase": "",  # "memory" or "trunk"
    "current": 0,
    "total": 0,
    "memory_done": 0,
    "trunk_done": 0,
    "error": None,
    "completed": False
}

import threading


def _rebuild_state_path() -> str:
    """
    Background: A rebuild task may be interrupted by process kill or power loss; the system
    needs to remember "there was an incomplete rebuild" across process restarts.
    Design intent: The state file is stored in the data directory (same lifecycle as the
    vector store); the path is resolved relative to the config file's directory, independent
    of the runtime cwd.
    """
    data_dir = _config.get("storage", {}).get("data_dir", "./data")
    if not os.path.isabs(data_dir):
        data_dir = os.path.normpath(os.path.join(os.path.dirname(_config_path), data_dir))
    return os.path.join(data_dir, "vector_rebuild_state.json")


def _write_rebuild_state(state: dict):
    try:
        os.makedirs(os.path.dirname(_rebuild_state_path()), exist_ok=True)
        with open(_rebuild_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError as e:
        # State file write failure only affects checkpoint-resume capability, should not interrupt rebuild itself
        print(f"[rebuild] Failed to write state file: {e}")


def _clear_rebuild_state():
    try:
        if os.path.exists(_rebuild_state_path()):
            os.remove(_rebuild_state_path())
    except OSError as e:
        print(f"[rebuild] Failed to clean up state file: {e}")


def _run_rebuild_task(resume: bool = False):
    """
    Background: After switching embedding provider, vector dimensions differ and the old
    index must be fully rebuilt; rebuilding may take tens of minutes and cannot restart
    from zero after interruption.
    Design intent:
      - Full mode: clear both collections first, record cleared=true in state file, then
        write entries one by one.
      - Resume mode (resume=True): do not clear; read the set of existing IDs from the
        collection and only write missing items. Chroma data is persistent, so already-written
        vectors naturally serve as checkpoints.
      - On success, delete the state file; any interruption leaves the state file behind,
        and resume_incomplete_rebuild() auto-resumes on next startup.
    Key constraint: Whoosh keyword search is unaffected during rebuild; service stays online.
    """
    global _rebuild_task

    try:
        sync = get_sync_manager()
        search = get_search_engine()

        embedding_model = get_embedding_model(_config)
        active_provider = (_config.get("active") or {}).get("embedding_provider", "?")
        if embedding_model:
            search.vector_store.set_embedding_model(embedding_model)
            print(f"✅ Using provider '{active_provider}' embedding model to rebuild index (resume={resume})")

        memories = sync.database.list_memories(status="active", limit=10000)
        all_trunks = []
        for m in memories:
            trunks = sync.database.get_trunks_by_document(m.id)
            all_trunks.extend([t for t in trunks if t.status == "ready"])

        _rebuild_task["total"] = len(memories) + len(all_trunks)

        existing_memory_ids: set = set()
        existing_trunk_ids: set = set()

        if resume:
            # Resume: keep already-written vectors, only write missing ones
            existing_memory_ids = set(search.vector_store.get_all_ids())
            existing_trunk_ids = set(search.vector_store.get_all_trunk_ids())
            print(f"[rebuild] Resume: existing memory vectors {len(existing_memory_ids)}, trunk vectors {len(existing_trunk_ids)}")
        else:
            # Full rebuild: clear both collections; once cleared is written to state file,
            # a resumed run won't clear again.
            # delete must tolerate non-existent collections ("clear all data" may have deleted them),
            # otherwise rebuild breaks at step one, forming an unrecoverable deadlock.
            try:
                search.vector_store.client.delete_collection("memories")
            except Exception:
                pass
            search.vector_store.collection = search.vector_store.client.get_or_create_collection(
                name="memories", metadata={"hnsw:space": "cosine"})
            try:
                search.vector_store.client.delete_collection("trunks")
            except Exception:
                pass
            search.vector_store.trunk_collection = search.vector_store.client.get_or_create_collection(
                name="trunks", metadata={"hnsw:space": "cosine"})

        _write_rebuild_state({"status": "running", "provider": active_provider, "cleared": True,
                              "started_at": datetime.now().isoformat()})

        _rebuild_task["phase"] = "memory"
        for i, memory in enumerate(memories):
            if memory.status == "active" and memory.id not in existing_memory_ids:
                if search.vector_store.add_memory(memory):
                    _rebuild_task["memory_done"] += 1
            elif memory.id in existing_memory_ids:
                _rebuild_task["memory_done"] += 1
            _rebuild_task["current"] = i + 1

        _rebuild_task["phase"] = "trunk"
        for i, trunk in enumerate(all_trunks):
            if trunk.id not in existing_trunk_ids:
                if search.vector_store.add_trunk(trunk):
                    _rebuild_task["trunk_done"] += 1
            else:
                _rebuild_task["trunk_done"] += 1
            _rebuild_task["current"] = len(memories) + i + 1

        _rebuild_task["completed"] = True
        _clear_rebuild_state()
        print(f"✅ Index rebuild complete: Memory {_rebuild_task['memory_done']}, Trunk {_rebuild_task['trunk_done']}")

    except Exception as e:
        _rebuild_task["error"] = str(e)
        # Keep state file: resume_incomplete_rebuild() will auto-resume on next startup
        print(f"❌ Index rebuild interrupted (checkpoint saved, will auto-resume on next start): {e}")
    finally:
        _rebuild_task["running"] = False


def resume_incomplete_rebuild():
    """
    Background: A previous rebuild that was interrupted (process killed/error) leaves behind
    vector_rebuild_state.json.
    Design intent: Called by main.py after startup completes; if a checkpoint is found, it
    resumes in the background in resume mode. Users don't need to manually re-trigger.
    Keyword search remains available while semantic search is not yet ready.
    Key constraint: Must be called after init_api (depends on global _config / _sync_manager).
    """
    global _rebuild_task
    try:
        if not os.path.exists(_rebuild_state_path()):
            return False
        search = get_search_engine()
        if not search.vector_store:
            print("[rebuild] Found incomplete rebuild checkpoint, but vector store is not enabled, skipping resume")
            return False
        if _rebuild_task["running"]:
            return False
        _rebuild_task = {"running": True, "phase": "resume", "current": 0, "total": 0,
                         "memory_done": 0, "trunk_done": 0, "error": None, "completed": False}
        threading.Thread(target=_run_rebuild_task, kwargs={"resume": True}, daemon=True).start()
        print("[rebuild] Detected incomplete vector rebuild, auto-resuming")
        return True
    except Exception as e:
        print(f"[rebuild] Auto-resume failed: {e}")
        return False


def _start_vector_rebuild() -> dict:
    """Start background vector rebuild, shared by web and Agent."""
    global _rebuild_task

    search = get_search_engine()
    if not search.vector_store:
        raise HTTPException(status_code=400, detail="Vector store not enabled")
    if _rebuild_task["running"]:
        return {"success": False, "message": "A rebuild task is already in progress"}

    _rebuild_task = {
        "running": True,
        "phase": "preparing",
        "current": 0,
        "total": 0,
        "memory_done": 0,
        "trunk_done": 0,
        "error": None,
        "completed": False,
    }
    threading.Thread(target=_run_rebuild_task, daemon=True).start()
    return {"success": True, "message": "Rebuild task started"}


def _rebuild_status_view() -> dict:
    return {
        "running": _rebuild_task["running"],
        "phase": _rebuild_task["phase"],
        "current": _rebuild_task["current"],
        "total": _rebuild_task["total"],
        "memory_done": _rebuild_task["memory_done"],
        "trunk_done": _rebuild_task["trunk_done"],
        "error": _rebuild_task["error"],
        "completed": _rebuild_task["completed"],
        "percent": round(_rebuild_task["current"] / _rebuild_task["total"] * 100) if _rebuild_task["total"] > 0 else 0,
    }


@router.post("/vector-rebuild")
async def rebuild_vectors(admin_id: int = Depends(verify_session)):
    """Start full vector index rebuild task (triggered from settings page after switching embedding provider)"""
    return _start_vector_rebuild()


@router.get("/vector-rebuild/status")
async def get_rebuild_status(admin_id: int = Depends(verify_session)):
    """Get rebuild index task status"""
    return _rebuild_status_view()


@router.get("/trunk-index-status")
async def get_trunk_index_status(admin_id: int = Depends(verify_session)):
    """Get Trunk full-text index status"""
    try:
        sync = get_sync_manager()
        
        # Count all completed trunks
        total_trunks = 0
        memories = sync.database.list_memories(status="active", limit=10000)
        
        for memory in memories:
            trunks = sync.database.get_trunks_by_document(memory.id)
            total_trunks += sum(1 for t in trunks if t.status == "ready")
        
        # Get Whoosh index count
        indexed_count = 0
        if sync.database.whoosh_search:
            indexed_count = sync.database.whoosh_search.get_trunk_count()
        
        return {
            "total_trunks": total_trunks,
            "indexed_count": indexed_count,
            "whoosh_available": sync.database.whoosh_search is not None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trunk-index-rebuild")
async def rebuild_trunk_index(admin_id: int = Depends(verify_session)):
    """Rebuild Trunk Whoosh full-text index"""
    try:
        sync = get_sync_manager()
        
        if not sync.database.whoosh_search:
            raise HTTPException(status_code=400, detail="Whoosh search not enabled")
        
        # Get all completed trunks
        all_trunks = []
        memories = sync.database.list_memories(status="active", limit=10000)
        
        for memory in memories:
            trunks = sync.database.get_trunks_by_document(memory.id)
            for trunk in trunks:
                if trunk.status == "ready":
                    all_trunks.append(trunk.to_dict())
        
        # Rebuild trunk index
        count = sync.database.whoosh_search.rebuild_trunk_index(all_trunks)
        
        return {
            "success": True,
            "message": f"Trunk full-text index rebuild complete",
            "indexed_count": count,
            "total_count": len(all_trunks)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Agent Tool Call Endpoint (SKILL / External Integration Entry) ====================

_AGENT_READ_TOOLS = {
    "quick_match", "search_memories", "search_trunks", "get_memory", "get_trunk",
    "get_related_memories", "get_related_trunks", "get_memory_trunks",
    "list_memories", "list_memories_by_tag", "get_memory_stats", "get_profile",
}
_AGENT_WRITE_TOOLS = {
    "add_memory", "update_memory", "patch_memory", "delete_memory",
    "update_trunk", "patch_trunk",
}
_AGENT_CONFIG_TOOLS = {
    "get_system_config", "configure_provider", "test_provider",
    "rebuild_vector_index", "get_vector_rebuild_status",
}


def _agent_tool_scope(tool_name: str) -> Optional[str]:
    if tool_name in _AGENT_READ_TOOLS:
        return "read"
    if tool_name in _AGENT_WRITE_TOOLS:
        return "write"
    if tool_name in _AGENT_CONFIG_TOOLS:
        return "config"
    return None


@router.post("/agent/call")
async def call_agent_tool(request: Request):
    """
    Background: MCP has been removed. AI clients (Cursor / Claude Code SKILLs, future first-party
    client) read/write the memory store through this unified endpoint.
    Design intent: Retains the old /mcp/call tool dispatch table (17 tool semantics validated
    in production), only replacing the auth semantics with pure Bearer API Token; a single
    endpoint covers all tools, so SKILL CLIs only need to construct {tool, arguments} instead
    of memorizing dozens of REST routes.
    Key constraint: Only accepts API Token (not browser sessions), ensuring the AI channel
    and web management channel credentials can be revoked independently.
    """
    try:
        # Parse request body
        body = await request.json()
        tool_name = body.get("tool")
        arguments = body.get("arguments", {})
        
        if not tool_name:
            raise HTTPException(status_code=400, detail="Missing tool parameter")
        
        # Get token from Header or arguments
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Strip "Bearer " prefix
        elif "token" in arguments:
            token = arguments.get("token")
        
        if not token:
            raise HTTPException(status_code=401, detail="Missing authentication: provide Authorization: Bearer <token> header or token field in request body")
        
        # Verify token (using auth_manager)
        auth_manager = get_auth_manager()
        token_info = auth_manager.verify_api_token(token)
        if not token_info:
            raise HTTPException(status_code=401, detail="Invalid API Token")
        required_scope = _agent_tool_scope(tool_name)
        if required_scope and required_scope not in set(token_info.get("scopes") or []):
            raise HTTPException(status_code=403, detail=f"API Token lacks {required_scope} permission")
        
        # Get global memory_tools
        memory_tools = get_memory_tools()
        
        # Call the corresponding tool (without passing token parameter)
        if tool_name == "add_memory":
            result = memory_tools.add_memory(
                title=arguments.get("title"),
                content=arguments.get("content"),
                tags=arguments.get("tags", []),
                priority=arguments.get("priority", 5)
            )
        elif tool_name == "update_memory":
            result = memory_tools.update_memory(
                memory_id=arguments.get("memory_id"),
                title=arguments.get("title"),
                content=arguments.get("content"),
                tags=arguments.get("tags"),
                priority=arguments.get("priority"),
                status=arguments.get("status")
            )
        elif tool_name == "delete_memory":
            result = memory_tools.delete_memory(
                memory_id=arguments.get("memory_id")
            )
        elif tool_name == "get_memory":
            result = memory_tools.get_memory(
                memory_id=arguments.get("memory_id")
            )
        elif tool_name == "search_memories":
            result = memory_tools.search_memories(
                query=arguments.get("query"),
                limit=arguments.get("limit", 10),
                min_score=arguments.get("min_score")
            )
        elif tool_name == "list_memories":
            result = memory_tools.list_memories(
                status=arguments.get("status"),
                source=arguments.get("source"),
                limit=arguments.get("limit", 20)
            )
        elif tool_name == "list_memories_by_tag":
            tags = arguments.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            result = memory_tools.list_memories_by_tag(
                tags=tags,
                limit=arguments.get("limit", 20)
            )
        elif tool_name == "get_related_memories":
            result = memory_tools.get_related_memories(
                memory_id=arguments.get("memory_id"),
                limit=arguments.get("limit", 5)
            )
        # Trunk (paragraph) level tools
        elif tool_name == "search_trunks":
            result = memory_tools.search_trunks(
                query=arguments.get("query"),
                mode=arguments.get("mode", "auto"),
                limit=arguments.get("limit", 10),
                min_score=arguments.get("min_score")
            )
        elif tool_name == "get_trunk":
            result = memory_tools.get_trunk(
                trunk_id=arguments.get("trunk_id")
            )
        elif tool_name == "get_related_trunks":
            result = memory_tools.get_related_trunks(
                trunk_id=arguments.get("trunk_id"),
                limit=arguments.get("limit", 5)
            )
        elif tool_name == "get_memory_trunks":
            result = memory_tools.get_memory_trunks(
                memory_id=arguments.get("memory_id")
            )
        elif tool_name == "update_trunk":
            result = memory_tools.update_trunk(
                trunk_id=arguments.get("trunk_id"),
                content=arguments.get("content"),
                summary=arguments.get("summary"),
                tags=arguments.get("tags")
            )
        elif tool_name == "patch_trunk":
            result = memory_tools.patch_trunk(
                trunk_id=arguments.get("trunk_id"),
                old_text=arguments.get("old_text"),
                new_text=arguments.get("new_text")
            )
        elif tool_name == "get_memory_stats":
            result = memory_tools.get_stats()
        elif tool_name == "patch_memory":
            result = memory_tools.patch_memory(
                memory_id=arguments.get("memory_id"),
                old_text=arguments.get("old_text"),
                new_text=arguments.get("new_text")
            )
        elif tool_name == "quick_match":
            result = memory_tools.quick_match(
                text=arguments.get("text"),
                top_k=arguments.get("top_k", 6)
            )
        elif tool_name == "get_profile":
            # Lazy import: api_profile depends on verify_session from this module; module-level mutual import would cause a cycle
            from web.api_profile import get_profile_service
            result = get_profile_service().get_profile_text(
                level=arguments.get("level", "standard"),
                with_sources=bool(arguments.get("with_sources")),
            )
        elif tool_name == "get_system_config":
            result = _config_view()
        elif tool_name == "configure_provider":
            provider_id = str(arguments.get("provider_id") or "").strip().lower()
            if not provider_id:
                raise HTTPException(status_code=400, detail="provider_id is required")

            patch = {
                key: arguments[key]
                for key in _PROVIDER_EDITABLE_FIELDS
                if key in arguments and arguments[key] is not None
            }
            api_key = arguments.get("api_key")
            existing = (_config.get("providers") or {}).get(provider_id, {})
            if api_key and not (patch.get("api_key_env") or existing.get("api_key_env")):
                patch["api_key_env"] = f"{provider_id.upper().replace('-', '_')}_API_KEY"

            active = {}
            if arguments.get("use_for_embedding") is True:
                active["embedding_provider"] = provider_id
            if arguments.get("use_for_chat") is True:
                active["chat_provider"] = provider_id

            update = ConfigUpdate(
                providers={provider_id: patch} if patch or provider_id not in (_config.get("providers") or {}) else None,
                active=active or None,
                api_keys={provider_id: api_key} if api_key else None,
                semantic_enabled=arguments.get("semantic_enabled"),
                min_similarity=arguments.get("min_similarity"),
            )
            applied = _apply_config_update(update)
            result = {**applied, "provider_id": provider_id, "config": _config_view()}
        elif tool_name == "test_provider":
            provider_id = str(arguments.get("provider_id") or "").strip().lower()
            if not provider_id:
                raise HTTPException(status_code=400, detail="provider_id is required")
            from memory.providers import test_provider
            result = test_provider(_config, provider_id)
        elif tool_name == "rebuild_vector_index":
            if arguments.get("confirm") is not True:
                raise HTTPException(status_code=400, detail="confirm=true is required to rebuild the vector index")
            result = _start_vector_rebuild()
        elif tool_name == "get_vector_rebuild_status":
            result = _rebuild_status_view()
        else:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
        
        return {
            "success": True,
            "tool": tool_name,
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== API Log endpoints ====================

@router.get("/logs")
async def get_api_logs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    type: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    admin_id: int = Depends(verify_session),
):
    """Get API call log list"""
    from memory.api_logger import get_api_logger
    api_logger = get_api_logger()
    
    if not api_logger:
        return {"logs": [], "stats": {}}
    
    logs = api_logger.get_logs(
        limit=limit,
        offset=offset,
        log_type=type,
        method=method,
        path_contains=path
    )
    stats = api_logger.get_stats()
    
    return {
        "logs": logs,
        "stats": stats
    }


@router.get("/logs/{log_id}")
async def get_api_log_detail(request: Request, log_id: int, admin_id: int = Depends(verify_session)):
    """Get single log detail"""
    from memory.api_logger import get_api_logger
    api_logger = get_api_logger()
    
    if not api_logger:
        raise HTTPException(status_code=404, detail="Log system not initialized")
    
    log = api_logger.get_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    
    return log


@router.delete("/logs")
async def clear_api_logs(request: Request, admin_id: int = Depends(verify_session)):
    """Clear all logs"""
    from memory.api_logger import get_api_logger
    api_logger = get_api_logger()
    
    if not api_logger:
        raise HTTPException(status_code=404, detail="Log system not initialized")
    
    api_logger.clear_logs()
    return {"success": True, "message": "Logs cleared"}


# ==================== 3D Visualization API ====================

@router.get("/visualize/embeddings")
async def get_embeddings_for_visualization(
    method: str = "pca",
    pc_x: int = 1,
    pc_y: int = 2, 
    pc_z: int = 3,
    perplexity: int = 30,
    admin_id: int = Depends(verify_session)
):
    """
    Get all memory embeddings and reduce to 3D space for visualization.
    
    Args:
        method: Dimensionality reduction algorithm (pca, tsne, umap)
        pc_x: Principal component number for X axis (1-based, PCA only)
        pc_y: Principal component number for Y axis
        pc_z: Principal component number for Z axis
        perplexity: t-SNE perplexity parameter
    """
    try:
        search = get_search_engine()
        sync = get_sync_manager()
        
        if not search.vector_store:
            raise HTTPException(status_code=400, detail="Vector store not enabled, please enable semantic search first")
        
        # Get all embeddings
        data = search.vector_store.get_all_embeddings()
        
        import numpy as np
        
        # Check if there's data (compatible with numpy arrays)
        ids = data["ids"]
        embeddings_raw = data["embeddings"]
        
        has_ids = ids is not None and len(ids) > 0
        has_embeddings = embeddings_raw is not None and (
            len(embeddings_raw) > 0 if isinstance(embeddings_raw, list) 
            else embeddings_raw.size > 0 if hasattr(embeddings_raw, 'size') 
            else False
        )
        
        if not has_ids or not has_embeddings:
            return {
                "success": True,
                "count": 0,
                "points": [],
                "message": "No vectorized memories yet"
            }
        
        embeddings = np.array(embeddings_raw)
        n_samples = len(embeddings)
        
        # Check sample count
        if n_samples < 3:
            return {
                "success": True,
                "count": n_samples,
                "points": [],
                "message": "Fewer than 3 memories, cannot perform 3D visualization"
            }
        
        variance_explained = []
        method_info = ""
        
        # Perform dimensionality reduction based on selected method
        if method == "pca":
            from sklearn.decomposition import PCA
            
            # Calculate the maximum number of principal components needed
            max_pc = max(pc_x, pc_y, pc_z)
            n_components = min(max_pc, n_samples, embeddings.shape[1])
            
            pca = PCA(n_components=n_components)
            all_coords = pca.fit_transform(embeddings)
            
            # Select specified principal components (convert to 0-based index)
            idx_x = min(pc_x - 1, n_components - 1)
            idx_y = min(pc_y - 1, n_components - 1)
            idx_z = min(pc_z - 1, n_components - 1)
            
            coords_3d = np.column_stack([
                all_coords[:, idx_x],
                all_coords[:, idx_y],
                all_coords[:, idx_z]
            ])
            
            variance_explained = pca.explained_variance_ratio_.tolist()
            method_info = f"PCA (PC{pc_x}, PC{pc_y}, PC{pc_z})"
            
        elif method == "tsne":
            from sklearn.manifold import TSNE
            
            # t-SNE perplexity cannot exceed sample count
            actual_perplexity = min(perplexity, n_samples - 1, 50)
            
            tsne = TSNE(
                n_components=3, 
                perplexity=actual_perplexity,
                random_state=42,
                max_iter=1000,  # Newer sklearn uses max_iter instead of n_iter
                init='pca'
            )
            coords_3d = tsne.fit_transform(embeddings)
            method_info = f"t-SNE (perplexity={actual_perplexity})"
            
        elif method == "umap":
            try:
                import umap
                
                # UMAP requires enough samples; n_neighbors must be less than sample count,
                # and sample count must be greater than n_neighbors + 1 to avoid sparse matrix eigenvalue decomposition issues
                min_samples_for_umap = 5
                if n_samples < min_samples_for_umap:
                    # Too few samples, fall back to PCA
                    from sklearn.decomposition import PCA
                    n_components = min(3, n_samples, embeddings.shape[1])
                    pca = PCA(n_components=n_components)
                    all_coords = pca.fit_transform(embeddings)
                    # Pad with zeros if fewer than 3 dimensions
                    if n_components < 3:
                        padding = np.zeros((n_samples, 3 - n_components))
                        all_coords = np.hstack([all_coords, padding])
                    coords_3d = all_coords[:, :3]
                    method_info = f"UMAP (insufficient samples, fell back to PCA)"
                else:
                    # Calculate appropriate n_neighbors to avoid sparse matrix issues
                    # n_neighbors should be much smaller than n_samples
                    n_neighbors = min(15, max(2, n_samples - 2))
                    
                    reducer = umap.UMAP(
                        n_components=3,
                        n_neighbors=n_neighbors,
                        min_dist=0.1,
                        random_state=42,
                        metric='euclidean'
                    )
                    coords_3d = reducer.fit_transform(embeddings)
                    method_info = "UMAP"
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail="UMAP not installed, run: pip install umap-learn"
                )
            except Exception as e:
                # If UMAP still fails, fall back to PCA
                from sklearn.decomposition import PCA
                n_components = min(3, n_samples, embeddings.shape[1])
                pca = PCA(n_components=n_components)
                all_coords = pca.fit_transform(embeddings)
                if n_components < 3:
                    padding = np.zeros((n_samples, 3 - n_components))
                    all_coords = np.hstack([all_coords, padding])
                coords_3d = all_coords[:, :3]
                method_info = f"UMAP failed, fell back to PCA: {str(e)[:50]}"
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported dimensionality reduction method: {method}")
        
        # Normalize coordinates to [-1, 1] range
        for i in range(3):
            col = coords_3d[:, i]
            min_val, max_val = col.min(), col.max()
            if max_val - min_val > 0:
                coords_3d[:, i] = 2 * (col - min_val) / (max_val - min_val) - 1
        
        # Get memory details
        memory_map = {}
        for m in sync.list_memories(limit=10000):
            memory_map[m.id] = m
        
        # Get all trunks (for retrieving tags)
        trunk_map = {}
        for t in sync.database.get_all_trunks(status="ready"):
            if t.document_id not in trunk_map:
                trunk_map[t.document_id] = []
            trunk_map[t.document_id].append(t)
        
        # Build response data
        points = []
        for i, memory_id in enumerate(data["ids"]):
            memory = memory_map.get(memory_id)
            metadata = data["metadatas"][i] if i < len(data["metadatas"]) else {}
            
            # Prefer memory object data, then trunk data, then metadata
            tags = []
            if memory and memory.tags:
                tags = memory.tags
            else:
                # Aggregate tags from trunks
                memory_trunks = trunk_map.get(memory_id, [])
                for trunk in memory_trunks:
                    if trunk.tags:
                        for t in trunk.tags:
                            if t and t not in tags:
                                tags.append(t)
            
            # If still empty, try getting from metadata
            if not tags and metadata.get("tags"):
                tags = metadata.get("tags", "").split(",")
            
            # Determine if this is an image type
            is_image = False
            content_type = "text"
            memory_trunks = trunk_map.get(memory_id, [])
            if memory_trunks:
                first_trunk = memory_trunks[0]
                if hasattr(first_trunk, 'content_type') and first_trunk.content_type == "image":
                    is_image = True
                    content_type = "image"
            
            point = {
                "id": memory_id,
                "x": float(coords_3d[i][0]),
                "y": float(coords_3d[i][1]),
                "z": float(coords_3d[i][2]),
                "title": memory.title if memory else metadata.get("title", "Unknown"),
                "tags": tags,
                "priority": memory.priority if memory else metadata.get("priority", 5),
                "source": memory.source if memory else metadata.get("source", "unknown"),
                "is_image": is_image,
                "content_type": content_type,
            }
            
            # Add content summary
            if memory:
                content = memory.content
                point["summary"] = content[:100] + "..." if len(content) > 100 else content
            else:
                doc = data["documents"][i] if i < len(data["documents"]) else ""
                point["summary"] = doc[:100] + "..." if len(doc) > 100 else doc
            
            points.append(point)
        
        return {
            "success": True,
            "count": len(points),
            "points": points,
            "method": method,
            "method_info": method_info,
            "variance_explained": variance_explained,
            "max_components": min(n_samples, embeddings.shape[1]) if method == "pca" else 3
        }
        
    except ImportError as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Missing dependency: {str(e)}. Run: pip install scikit-learn"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Trunk API ====================

@router.get("/memories/{memory_id}/trunks")
async def get_memory_trunks(memory_id: str, admin_id: int = Depends(verify_session)):
    """Get all trunks of a document"""
    sync = get_sync_manager()
    
    memory = sync.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    trunks = sync.database.get_trunks_by_document(memory_id)
    
    return {
        "memory_id": memory_id,
        "trunk_status": memory.trunk_status,
        "trunks": [t.to_dict() for t in trunks]
    }


@router.post("/memories/{memory_id}/rechunk")
async def rechunk_memory(memory_id: str, admin_id: int = Depends(verify_session)):
    """Re-chunk a document"""
    sync = get_sync_manager()
    processor = get_chunking_processor()
    
    if not processor:
        raise HTTPException(status_code=500, detail="Chunking processor not enabled")
    
    memory = sync.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    
    # Queue for re-chunking
    processor.rechunk_document(memory_id)
    
    return {
        "success": True,
        "message": "Queued for re-chunking, please refresh later to see results"
    }


@router.get("/trunks/{trunk_id}")
async def get_trunk(trunk_id: str, admin_id: int = Depends(verify_session)):
    """Get a single Trunk"""
    sync = get_sync_manager()
    
    trunk = sync.database.get_trunk(trunk_id)
    if not trunk:
        raise HTTPException(status_code=404, detail="Trunk not found")
    
    # Get parent document info
    memory = sync.database.get_memory(trunk.document_id)
    
    return {
        "trunk": trunk.to_dict(),
        "document": memory.to_dict() if memory else None
    }


@router.get("/trunks/{trunk_id}/related")
async def get_related_trunks(
    trunk_id: str, 
    limit: int = 10, 
    admin_id: int = Depends(verify_session)
):
    """Get related Trunks"""
    sync = get_sync_manager()
    search = get_search_engine()
    
    trunk = sync.database.get_trunk(trunk_id)
    if not trunk:
        raise HTTPException(status_code=404, detail="Trunk not found")
    
    if not search.vector_store:
        return {
            "trunk_id": trunk_id,
            "related": [],
            "message": "Semantic search not enabled"
        }
    
    # Find related trunks
    related_results = search.vector_store.find_related_trunks(
        trunk_id=trunk_id,
        limit=limit,
        current_document_id=trunk.document_id,
        same_document_boost=0.1
    )
    
    # Build response data
    related = []
    for tid, score, is_same_doc in related_results:
        related_trunk = sync.database.get_trunk(tid)
        if related_trunk:
            # Get parent document title
            doc = sync.database.get_memory(related_trunk.document_id)
            doc_title = doc.title if doc else "Unknown document"
            
            related.append({
                "trunk": related_trunk.to_dict(),
                "document_title": doc_title,
                "score": round(score, 4),
                "is_same_document": is_same_doc
            })
    
    return {
        "trunk_id": trunk_id,
        "related": related
    }


@router.get("/chunking/status")
async def get_chunking_status(admin_id: int = Depends(verify_session)):
    """Get chunking processor status"""
    processor = get_chunking_processor()
    sync = get_sync_manager()
    
    if not processor:
        return {
            "enabled": False,
            "message": "Chunking processor not enabled"
        }
    
    stats = processor.get_stats()
    
    # Get trunk statistics
    db_stats = sync.database.get_stats()
    
    return {
        "enabled": True,
        "queue": stats["queue"],
        "pending_documents": stats["pending_documents"],
        "trunk_total": db_stats.get("trunk_total", 0),
        "trunk_ready": db_stats.get("trunk_ready", 0)
    }


@router.post("/chunking/process-pending")
async def process_pending_documents(admin_id: int = Depends(verify_session)):
    """Process all pending documents"""
    processor = get_chunking_processor()
    
    if not processor:
        raise HTTPException(status_code=500, detail="Chunking processor not enabled")
    
    count = processor.process_pending_documents()
    
    return {
        "success": True,
        "queued": count,
        "message": f"Queued {count} documents for chunking"
    }


@router.get("/visualize/trunk-embeddings")
async def get_trunk_embeddings_for_visualization(
    method: str = "pca",
    pc_x: int = 1,
    pc_y: int = 2, 
    pc_z: int = 3,
    perplexity: int = 30,
    admin_id: int = Depends(verify_session)
):
    """
    Get all trunk embeddings and reduce to 3D space for visualization (trunk-level).
    """
    try:
        search = get_search_engine()
        sync = get_sync_manager()
        
        if not search.vector_store:
            raise HTTPException(status_code=400, detail="Vector store not enabled")
        
        # Get all trunk embeddings
        data = search.vector_store.get_all_trunk_embeddings()
        
        import numpy as np
        
        ids = data["ids"]
        embeddings_raw = data["embeddings"]
        
        has_ids = ids is not None and len(ids) > 0
        has_embeddings = embeddings_raw is not None and (
            len(embeddings_raw) > 0 if isinstance(embeddings_raw, list) 
            else embeddings_raw.size > 0 if hasattr(embeddings_raw, 'size') 
            else False
        )
        
        if not has_ids or not has_embeddings:
            return {
                "success": True,
                "count": 0,
                "points": [],
                "message": "No vectorized trunks yet"
            }
        
        embeddings = np.array(embeddings_raw)
        n_samples = len(embeddings)
        
        if n_samples < 3:
            return {
                "success": True,
                "count": n_samples,
                "points": [],
                "message": "Fewer than 3 trunks, cannot perform 3D visualization"
            }
        
        variance_explained = []
        method_info = ""
        
        # Dimensionality reduction (same logic as document-level)
        if method == "pca":
            from sklearn.decomposition import PCA
            
            max_pc = max(pc_x, pc_y, pc_z)
            n_components = min(max_pc, n_samples, embeddings.shape[1])
            
            pca = PCA(n_components=n_components)
            all_coords = pca.fit_transform(embeddings)
            
            idx_x = min(pc_x - 1, n_components - 1)
            idx_y = min(pc_y - 1, n_components - 1)
            idx_z = min(pc_z - 1, n_components - 1)
            
            coords_3d = np.column_stack([
                all_coords[:, idx_x],
                all_coords[:, idx_y],
                all_coords[:, idx_z]
            ])
            
            variance_explained = pca.explained_variance_ratio_.tolist()
            method_info = f"PCA (PC{pc_x}, PC{pc_y}, PC{pc_z})"
            
        elif method == "tsne":
            from sklearn.manifold import TSNE
            
            actual_perplexity = min(perplexity, n_samples - 1, 50)
            
            tsne = TSNE(
                n_components=3, 
                perplexity=actual_perplexity,
                random_state=42,
                max_iter=1000,
                init='pca'
            )
            coords_3d = tsne.fit_transform(embeddings)
            method_info = f"t-SNE (perplexity={actual_perplexity})"
            
        elif method == "umap":
            try:
                import umap
                
                # UMAP requires enough samples; n_neighbors must be less than sample count,
                # and sample count must be greater than n_neighbors + 1 to avoid sparse matrix eigenvalue decomposition issues
                min_samples_for_umap = 5
                if n_samples < min_samples_for_umap:
                    # Too few samples, fall back to PCA
                    from sklearn.decomposition import PCA
                    n_components = min(3, n_samples, embeddings.shape[1])
                    pca = PCA(n_components=n_components)
                    all_coords = pca.fit_transform(embeddings)
                    # Pad with zeros if fewer than 3 dimensions
                    if n_components < 3:
                        padding = np.zeros((n_samples, 3 - n_components))
                        all_coords = np.hstack([all_coords, padding])
                    coords_3d = all_coords[:, :3]
                    method_info = f"UMAP (insufficient samples, fell back to PCA)"
                else:
                    # Calculate appropriate n_neighbors to avoid sparse matrix issues
                    # n_neighbors should be much smaller than n_samples
                    n_neighbors = min(15, max(2, n_samples - 2))
                    
                    reducer = umap.UMAP(
                        n_components=3,
                        n_neighbors=n_neighbors,
                        min_dist=0.1,
                        random_state=42,
                        metric='euclidean'
                    )
                    coords_3d = reducer.fit_transform(embeddings)
                    method_info = "UMAP"
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail="UMAP not installed"
                )
            except Exception as e:
                # If UMAP still fails, fall back to PCA
                from sklearn.decomposition import PCA
                n_components = min(3, n_samples, embeddings.shape[1])
                pca = PCA(n_components=n_components)
                all_coords = pca.fit_transform(embeddings)
                if n_components < 3:
                    padding = np.zeros((n_samples, 3 - n_components))
                    all_coords = np.hstack([all_coords, padding])
                coords_3d = all_coords[:, :3]
                method_info = f"UMAP failed, fell back to PCA: {str(e)[:50]}"
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported dimensionality reduction method: {method}")
        
        # Normalize coordinates
        for i in range(3):
            col = coords_3d[:, i]
            min_val, max_val = col.min(), col.max()
            if max_val - min_val > 0:
                coords_3d[:, i] = 2 * (col - min_val) / (max_val - min_val) - 1
        
        # Get trunk and document details
        trunk_map = {}
        for t in sync.database.get_all_trunks(status="ready"):
            trunk_map[t.id] = t
        
        memory_map = {}
        for m in sync.list_memories(limit=10000):
            memory_map[m.id] = m
        
        # Build response data
        points = []
        for i, trunk_id in enumerate(data["ids"]):
            trunk = trunk_map.get(trunk_id)
            metadata = data["metadatas"][i] if i < len(data["metadatas"]) else {}
            
            doc_id = metadata.get("document_id", trunk.document_id if trunk else "")
            doc = memory_map.get(doc_id)
            
            # Prefer trunk/doc object data, then metadata
            tags = []
            if trunk and trunk.tags:
                tags = trunk.tags
            elif doc and doc.tags:
                tags = doc.tags
            elif metadata.get("tags"):
                tags = metadata.get("tags", "").split(",")
            
            # Determine if this is an image type
            is_image = False
            content_type = "text"
            if trunk and hasattr(trunk, 'content_type') and trunk.content_type == "image":
                is_image = True
                content_type = "image"
            
            point = {
                "id": trunk_id,
                "x": float(coords_3d[i][0]),
                "y": float(coords_3d[i][1]),
                "z": float(coords_3d[i][2]),
                "document_id": doc_id,
                "document_title": doc.title if doc else "Unknown document",
                "order": trunk.order if trunk else metadata.get("order", 0),
                "title": f"{doc.title if doc else 'Unknown'} - Paragraph {(trunk.order if trunk else metadata.get('order', 0)) + 1}",
                "tags": tags,
                "summary": trunk.summary if trunk else metadata.get("summary", ""),
                "priority": doc.priority if doc else 5,
                "source": doc.source if doc else "unknown",
                "is_image": is_image,
                "content_type": content_type,
            }
            
            # Add content summary
            if trunk:
                content = trunk.content
                point["content_preview"] = content[:100] + "..." if len(content) > 100 else content
            else:
                doc_content = data["documents"][i] if i < len(data["documents"]) else ""
                point["content_preview"] = doc_content[:100] + "..." if len(doc_content) > 100 else doc_content
            
            points.append(point)
        
        return {
            "success": True,
            "count": len(points),
            "points": points,
            "method": method,
            "method_info": method_info,
            "variance_explained": variance_explained,
            "max_components": min(n_samples, embeddings.shape[1]) if method == "pca" else 3
        }
        
    except ImportError as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Missing dependency: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Smart Import API ====================

class SmartImportChunkRequest(BaseModel):
    """Smart import chunking request"""
    content: str


class SmartImportSearchTagsRequest(BaseModel):
    """Search sample tags request"""
    chunks: List[str]


class SmartImportGenerateTagsRequest(BaseModel):
    """Generate tags request"""
    content: str
    sample_tags: Optional[List[str]] = None


@router.post("/smart-import/chunk")
async def smart_import_chunk(data: SmartImportChunkRequest, admin_id: int = Depends(verify_session)):
    """
    Smart import - Step 1: Document chunking.
    Split document content into multiple semantic paragraphs.
    """
    content = data.content.strip()
    if not content:
        return {"chunks": [], "count": 0}
    
    # Use the chunker
    chunker = create_chunker(_config)
    chunks = chunker.chunk(content)
    
    return {
        "chunks": chunks,
        "count": len(chunks)
    }


@router.post("/smart-import/search-tags")
async def smart_import_search_tags(data: SmartImportSearchTagsRequest, admin_id: int = Depends(verify_session)):
    """
    Smart import - Step 2: Search sample tags.
    
    Flow: paragraph content → vectorize → KNN search → collect tags
    """
    if not data.chunks:
        return {"tags": [], "details": []}
    
    sync = get_sync_manager()
    vector_store = sync.vector_store
    database = sync.database
    
    all_tags = []
    details = []
    tag_scores = {}
    
    # For each paragraph: vectorize → KNN search
    for i, chunk in enumerate(data.chunks[:5]):  # Process at most the first 5 paragraphs
        if len(chunk) < 30:
            continue
        
        chunk_text = chunk[:800]  # Limit length
        
        try:
            # 1. Search similar trunks using paragraph content (vectorization happens internally)
            trunk_results = vector_store.search_trunks(
                query=chunk_text,
                limit=5,
                min_score=0.15  # Low threshold to find more content
            )
            
            # trunk_results is [(trunk_id, score), ...]
            for trunk_id, score in trunk_results:
                # Query this trunk's tags from the database
                trunk = database.get_trunk(trunk_id)
                if trunk and trunk.tags:
                    for tag in trunk.tags:
                        if tag:
                            if tag not in tag_scores or score > tag_scores[tag]:
                                tag_scores[tag] = score
                            
                            if tag not in all_tags:
                                all_tags.append(tag)
                                details.append({
                                    "tag": tag,
                                    "from_chunk": i + 1,
                                    "similar_trunk": trunk_id[:8] + "...",
                                    "trunk_summary": trunk.summary[:30] + "..." if trunk.summary else "",
                                    "score": round(score, 3),
                                    "method": "trunk_vector"
                                })
            
            # 2. Also search at document level
            doc_results = vector_store.search(
                query=chunk_text,
                limit=3,
                min_score=0.15
            )
            
            # doc_results is [(memory_id, score), ...]
            for memory_id, score in doc_results:
                memory = sync.get_memory(memory_id)
                if memory and memory.tags:
                    for tag in memory.tags:
                        if tag:
                            if tag not in tag_scores or score > tag_scores[tag]:
                                tag_scores[tag] = score
                            
                            if tag not in all_tags:
                                all_tags.append(tag)
                                details.append({
                                    "tag": tag,
                                    "from_chunk": i + 1,
                                    "similar_doc": memory_id[:8] + "...",
                                    "doc_title": memory.title[:20] + "..." if len(memory.title) > 20 else memory.title,
                                    "score": round(score, 3),
                                    "method": "doc_vector"
                                })
                                
        except Exception as e:
            print(f"Paragraph {i+1} vector search failed: {e}")
            import traceback
            traceback.print_exc()
    
    # Sort by score
    all_tags.sort(key=lambda t: tag_scores.get(t, 0), reverse=True)
    
    return {
        "tags": all_tags[:15],
        "details": details[:15],
        "search_stats": {
            "chunks_processed": min(len(data.chunks), 5),
            "unique_tags_found": len(all_tags),
            "method": "embedding_knn"
        }
    }


@router.post("/smart-import/generate-tags")
async def smart_import_generate_tags(data: SmartImportGenerateTagsRequest, admin_id: int = Depends(verify_session)):
    """
    Smart import - Step 3: AI tag generation.
    Generate hierarchical tags based on content + sample tags.
    """
    content = data.content.strip()
    if not content:
        return {"tags": ["uncategorized"]}
    
    try:
        chat_model = get_chat_model(_config)
        if not chat_model or not chat_model.is_available():
            # If AI is not available, return default tags
            return {"tags": ["uncategorized"], "ai_available": False}
        
        sync = get_sync_manager()
        
        # Get existing tag hierarchy from the system
        all_tags = sync.database.get_all_tags()
        
        # Extract title (from first line of content)
        lines = content.split('\n')
        title = lines[0].strip().lstrip('#').strip() if lines else "Untitled"
        
        # Generate tags
        generated_tags = chat_model.generate_tags(
            title=title,
            content=content[:2000],  # Limit content length
            existing_tags=[],
            tag_tree=all_tags,
            similar_tags=data.sample_tags or []
        )
        
        return {
            "tags": generated_tags if generated_tags else ["uncategorized"],
            "ai_available": True,
            "sample_tags_used": data.sample_tags or []
        }
        
    except Exception as e:
        print(f"AI tag generation failed: {e}")
        return {"tags": ["uncategorized"], "error": str(e)}


# ==================== Knowledge Graph API ====================

@router.get("/knowledge-graph/stats")
async def get_knowledge_graph_stats(admin_id: int = Depends(verify_session)):
    """Get knowledge graph statistics"""
    sync = get_sync_manager()
    stats = sync.database.get_knowledge_graph_stats()
    return {"status": "ok", "stats": stats}


@router.get("/knowledge-graph/entities")
async def get_entities(
    entity_type: Optional[str] = None,
    limit: int = 100,
    admin_id: int = Depends(verify_session)
):
    """Get entity list (automatically merges entities with the same name)"""
    sync = get_sync_manager()
    raw_entities = sync.database.get_all_entities(entity_type=entity_type, limit=limit * 2)
    
    # Merge entities with the same name
    name_groups = {}
    for e in raw_entities:
        # Normalize name
        raw_name = e["name"]
        normalized = raw_name.split('（')[0].split('(')[0].strip().lower()
        
        if normalized not in name_groups:
            name_groups[normalized] = {
                "id": e["id"],  # Use the first ID
                "name": raw_name,
                "entity_type": e["entity_type"],
                "mention_count": 0,
                "merged_ids": []
            }
        
        name_groups[normalized]["merged_ids"].append(e["id"])
        name_groups[normalized]["mention_count"] += e["mention_count"]
        
        # Prefer shorter names
        if len(raw_name) < len(name_groups[normalized]["name"]):
            name_groups[normalized]["name"] = raw_name
    
    # Convert to list and sort
    entities = list(name_groups.values())
    entities.sort(key=lambda x: x["mention_count"], reverse=True)
    entities = entities[:limit]
    
    return {"status": "ok", "entities": entities, "count": len(entities)}


@router.get("/knowledge-graph/entities/{entity_id}")
async def get_entity_detail(entity_id: int, admin_id: int = Depends(verify_session)):
    """Get entity detail and related trunks"""
    sync = get_sync_manager()
    
    # Get related trunks
    trunks = sync.database.get_trunks_by_entity(entity_id)
    
    # Get related entities
    related_entities = sync.database.get_related_entities(entity_id)
    
    # Get related relations
    relations = sync.database.get_entity_relations(entity_id)
    
    # Add document titles; the frontend entity drawer needs to show "appears in which memories" with navigation
    title_cache: dict = {}
    trunk_dicts = []
    for trunk in trunks:
        d = trunk.to_dict()
        doc_id = d.get("document_id")
        if doc_id and doc_id not in title_cache:
            memory = sync.database.get_memory(doc_id)
            title_cache[doc_id] = memory.title if memory else doc_id
        d["document_title"] = title_cache.get(doc_id, "")
        trunk_dicts.append(d)

    return {
        "status": "ok",
        "trunks": trunk_dicts,
        "related_entities": related_entities,
        "relations": relations
    }


@router.get("/knowledge-graph/entities/by-name/{name}")
async def get_entity_by_name(
    name: str, 
    entity_type: Optional[str] = None,
    admin_id: int = Depends(verify_session)
):
    """Query entity and related content by name"""
    sync = get_sync_manager()
    
    entity = sync.database.get_entity_by_name(name, entity_type)
    if not entity:
        return {"status": "ok", "entity": None, "trunks": [], "related_entities": []}
    
    # Get related trunks
    trunks = sync.database.get_trunks_by_entity(entity["id"])
    
    # Get related entities
    related_entities = sync.database.get_related_entities(entity["id"])
    
    return {
        "status": "ok",
        "entity": entity,
        "trunks": [t.to_dict() for t in trunks],
        "related_entities": related_entities
    }


@router.get("/knowledge-graph/relations")
async def get_all_relations(
    entity_id: Optional[int] = None,
    admin_id: int = Depends(verify_session)
):
    """Get entity relations list"""
    sync = get_sync_manager()
    relations = sync.database.get_entity_relations(entity_id)
    return {"status": "ok", "relations": relations, "count": len(relations)}


@router.get("/knowledge-graph/timeline")
async def get_timeline(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin_id: int = Depends(verify_session)
):
    """Get timeline"""
    sync = get_sync_manager()
    
    if start_date and end_date:
        trunks = sync.database.get_trunks_by_time_range(start_date, end_date)
        return {
            "status": "ok",
            "trunks": [t.to_dict() for t in trunks],
            "count": len(trunks)
        }
    else:
        # Return all timeline entries
        entries = sync.database.get_timeline_entries()
        return {
            "status": "ok",
            "entries": entries,
            "count": len(entries)
        }


@router.get("/knowledge-graph/graph-data")
async def get_graph_data(
    limit: int = 100,
    admin_id: int = Depends(verify_session)
):
    """
    Get graph data for visualization.
    Returns nodes and edges for 3D view or knowledge graph page.
    
    Note: Deduplicates entities with the same name by merging.
    """
    sync = get_sync_manager()
    
    # Get entities as nodes
    entities = sync.database.get_all_entities(limit=limit * 2)  # Fetch extra for merging
    
    # Get relations as edges
    relations = sync.database.get_entity_relations()
    
    # ========== Merge entities with the same name ==========
    # Group by normalized name
    name_groups = {}
    for e in entities:
        # Normalize name: strip parenthetical content, spaces, and lowercase
        raw_name = e["name"]
        normalized = raw_name.split('（')[0].split('(')[0].strip().lower()
        
        if normalized not in name_groups:
            name_groups[normalized] = {
                "ids": [],
                "name": raw_name,  # Keep the first occurrence's original name
                "types": set(),
                "mention_count": 0
            }
        
        name_groups[normalized]["ids"].append(e["id"])
        name_groups[normalized]["types"].add(e["entity_type"])
        name_groups[normalized]["mention_count"] += e["mention_count"]
        
        # If new name is shorter, prefer it
        if len(raw_name) < len(name_groups[normalized]["name"]):
            name_groups[normalized]["name"] = raw_name
    
    # Build deduplicated nodes
    nodes = []
    id_mapping = {}  # Old ID -> new ID mapping
    
    for normalized, group in list(name_groups.items())[:limit]:
        # Select the most common type
        primary_type = list(group["types"])[0] if group["types"] else "concept"
        
        # Use the first ID as representative
        primary_id = group["ids"][0]
        node_id = f"entity_{primary_id}"
        
        nodes.append({
            "id": node_id,
            "name": group["name"],
            "type": primary_type,
            "mention_count": group["mention_count"],
            "group": primary_type,
            "merged_ids": group["ids"]  # Record which IDs were merged
        })
        
        # Map all old IDs to new ID
        for old_id in group["ids"]:
            id_mapping[old_id] = node_id
    
    # Build edges
    edges = []
    seen_edges = set()
    missing_entity_ids = set()  # Track entity IDs referenced by edges but missing from node list
    
    # 1. Get edges from relations table
    for r in relations:
        subj_id = r['subject_id']
        obj_id = r['object_id']
        source_id = id_mapping.get(subj_id)
        target_id = id_mapping.get(obj_id)
        
        # If one end is in the node list but the other isn't, record the missing one
        if source_id and not target_id:
            missing_entity_ids.add(obj_id)
        elif target_id and not source_id:
            missing_entity_ids.add(subj_id)
        
        if source_id and target_id and source_id != target_id:
            edge_key = tuple(sorted([source_id, target_id]))
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    "source": source_id,
                    "target": target_id,
                    "relation": r["relation_type"],
                    "type": "relation"
                })
    
    # 2. Get edges from co-occurrence (entities appearing in the same trunk)
    cooccurrences = sync.database.get_entity_cooccurrences(min_count=1, limit=500)
    for co in cooccurrences:
        e1_id = co['entity1_id']
        e2_id = co['entity2_id']
        source_id = id_mapping.get(e1_id)
        target_id = id_mapping.get(e2_id)
        
        # Record missing entities
        if source_id and not target_id:
            missing_entity_ids.add(e2_id)
        elif target_id and not source_id:
            missing_entity_ids.add(e1_id)
        
        if source_id and target_id and source_id != target_id:
            edge_key = tuple(sorted([source_id, target_id]))
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    "source": source_id,
                    "target": target_id,
                    "relation": "co-occurrence",
                    "type": "cooccurrence",
                    "weight": co['co_count']
                })
    
    # 3. Add missing nodes (referenced by edges but not in node list)
    if missing_entity_ids:
        # Get details of missing entities
        for missing_id in list(missing_entity_ids)[:100]:  # Add at most 100
            try:
                missing_entity = sync.database.get_entity(missing_id)
                if missing_entity:
                    node_id = f"entity_{missing_id}"
                    id_mapping[missing_id] = node_id
                    nodes.append({
                        "id": node_id,
                        "name": missing_entity["name"],
                        "type": missing_entity["entity_type"],
                        "mention_count": missing_entity["mention_count"],
                        "group": missing_entity["entity_type"],
                        "merged_ids": [missing_id]
                    })
            except:
                pass
        
        # Re-process edges that were previously skipped due to missing nodes
        for r in relations:
            source_id = id_mapping.get(r['subject_id'])
            target_id = id_mapping.get(r['object_id'])
            if source_id and target_id and source_id != target_id:
                edge_key = tuple(sorted([source_id, target_id]))
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": source_id,
                        "target": target_id,
                        "relation": r["relation_type"],
                        "type": "relation"
                    })
        
        for co in cooccurrences:
            source_id = id_mapping.get(co['entity1_id'])
            target_id = id_mapping.get(co['entity2_id'])
            if source_id and target_id and source_id != target_id:
                edge_key = tuple(sorted([source_id, target_id]))
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": source_id,
                        "target": target_id,
                        "relation": "co-occurrence",
                        "type": "cooccurrence",
                        "weight": co['co_count']
                    })
    
    return {
        "status": "ok",
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges)
    }


# ==================== Timeline API ====================

@router.get("/timeline/events")
async def get_timeline_events(
    start_date: str = None,
    end_date: str = None,
    status: str = None,
    event_type: str = None,
    source_type: str = None,
    limit: int = 100,
    offset: int = 0,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Get time events list"""
    events = sync.database.get_time_events(
        start_date=start_date,
        end_date=end_date,
        status=status,
        event_type=event_type,
        source_type=source_type,
        limit=limit,
        offset=offset
    )
    
    # Check for expired status
    from datetime import datetime
    today = datetime.now().date().isoformat()
    
    for event in events:
        # If status is pending but time has passed, mark as expired
        if event.get('status') == 'pending':
            event_date = event.get('absolute_time', '')[:10]
            if event_date < today:
                event['is_expired'] = True
    
    return {
        "status": "ok",
        "events": events,
        "count": len(events)
    }


@router.get("/timeline/events/{event_id}")
async def get_timeline_event(
    event_id: int,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Get a single time event detail"""
    event = sync.database.get_time_event(event_id)
    
    if not event:
        raise HTTPException(status_code=404, detail="Time event not found")
    
    # Get associated trunk info
    trunk = sync.database.get_trunk(event['trunk_id'])
    if trunk:
        event['trunk'] = {
            "id": trunk.id,
            "content": trunk.content[:200] + "..." if len(trunk.content) > 200 else trunk.content,
            "document_id": trunk.document_id
        }
        # Get document title
        memory = sync.database.get_memory(trunk.document_id)
        if memory:
            event['document_title'] = memory.title
    
    return {
        "status": "ok",
        "event": event
    }


@router.put("/timeline/events/{event_id}")
async def update_timeline_event(
    event_id: int,
    request: Request,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Update a time event"""
    data = await request.json()
    
    success = sync.database.update_time_event(event_id, data)
    
    if not success:
        raise HTTPException(status_code=404, detail="Update failed")
    
    return {"status": "ok", "message": "Updated successfully"}


@router.post("/timeline/events/{event_id}/complete")
async def complete_timeline_event(
    event_id: int,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Mark a time event as completed"""
    success = sync.database.complete_time_event(event_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Operation failed")
    
    return {"status": "ok", "message": "Marked as completed"}


@router.post("/timeline/events/{event_id}/uncomplete")
async def uncomplete_timeline_event(
    event_id: int,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Undo completion of a time event"""
    success = sync.database.uncomplete_time_event(event_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Operation failed")
    
    return {"status": "ok", "message": "Completion undone"}


@router.delete("/timeline/events/{event_id}")
async def delete_timeline_event(
    event_id: int,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Delete a time event"""
    success = sync.database.delete_time_event(event_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Delete failed")
    
    return {"status": "ok", "message": "Deleted"}


@router.get("/timeline/calendar")
async def get_timeline_calendar(
    year: int,
    month: int,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Get calendar view data"""
    events = sync.database.get_time_events_for_calendar(year, month)
    
    # Group by date
    from collections import defaultdict
    from datetime import datetime
    
    events_by_day = defaultdict(list)
    today = datetime.now().date().isoformat()
    
    for event in events:
        day = event.get('absolute_time', '')[:10]
        
        # Check for expired status
        if event.get('status') == 'pending' and day < today:
            event['is_expired'] = True
        
        events_by_day[day].append(event)
    
    return {
        "status": "ok",
        "year": year,
        "month": month,
        "events": dict(events_by_day),
        "total_count": len(events)
    }


@router.get("/timeline/stats")
async def get_timeline_stats(
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Get timeline statistics"""
    stats = sync.database.get_time_events_stats()
    
    return {
        "status": "ok",
        "stats": stats
    }


@router.get("/trunks/{trunk_id}/time-events")
async def get_trunk_time_events(
    trunk_id: str,
    sync: SyncManager = Depends(get_sync_manager),
    admin_id: int = Depends(verify_session),
):
    """Get all time events for a trunk"""
    events = sync.database.get_time_events_by_trunk(trunk_id)
    
    return {
        "status": "ok",
        "events": events,
        "count": len(events)
    }
