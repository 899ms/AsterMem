"""
Profile REST API (PRD_UserProfile v0.3 · Section 8)

Background: The profile must always be visible, editable, and disableable (trust boundary).
The Web UI is the sole review entry point.
Design intent: Standalone router (/api/profile), reuses verify_session from the main API;
Bearer Token follows existing scope mapping (GET→read, write→write). The Agent side
has a separate get_profile tool via /api/agent/call.
Key constraints: The enabled toggle is persisted to config.yaml; AI auto-fills field-level
values (distilled), but fields manually edited by the user (manual) are never overwritten
by AI; the manual profile manual.md can only be written by the user.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.api import verify_session

router = APIRouter(prefix="/api/profile", tags=["profile"])

_profile_service = None
_dream_manager = None
_save_config = None


def init_profile_api(profile_service, dream_manager, save_config=None):
    global _profile_service, _dream_manager, _save_config
    _profile_service = profile_service
    _dream_manager = dream_manager
    _save_config = save_config


def get_profile_service():
    if _profile_service is None:
        raise HTTPException(status_code=500, detail="Profile service not initialized")
    return _profile_service


def get_dream_manager():
    if _dream_manager is None:
        raise HTTPException(status_code=500, detail="Profile service not initialized")
    return _dream_manager


class FieldsUpdate(BaseModel):
    values: dict


class ManualUpdate(BaseModel):
    content: str


class SettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    daily_hour: Optional[int] = None


class ClaimResolve(BaseModel):
    action: str  # keep | delete


class DreamCreate(BaseModel):
    scope: str = "all"
    instructions: str = ""


class DistillRequest(BaseModel):
    day: Optional[str] = None


# ---------- Profile output ----------

@router.get("")
async def get_profile(level: str = "standard", with_sources: bool = False,
                      admin_id: int = Depends(verify_session)):
    svc = get_profile_service()
    return {"profile": svc.get_profile_text(level=level, with_sources=with_sources),
            "enabled": svc.is_enabled()}


@router.get("/status")
async def profile_status(admin_id: int = Depends(verify_session)):
    return get_profile_service().status()


# ---------- L1/L2 fields and manual profile ----------

@router.get("/fields")
async def get_fields(admin_id: int = Depends(verify_session)):
    return get_profile_service().get_fields()


@router.put("/fields")
async def update_fields(data: FieldsUpdate, admin_id: int = Depends(verify_session)):
    try:
        return get_profile_service().update_fields(data.values)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/fields/autofill")
async def autofill_fields(admin_id: int = Depends(verify_session)):
    """AI infers field values from raw memories and writes them directly; fields manually edited by the user are not overwritten"""
    try:
        result = get_profile_service().autofill_fields()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI autofill failed: {e}")
    result["fields"] = get_profile_service().get_fields()
    return result


@router.get("/fields/history")
async def field_history(key: Optional[str] = None, limit: int = 50,
                        admin_id: int = Depends(verify_session)):
    return {"history": get_profile_service().get_field_history(key=key,
                                                               limit=min(limit, 200))}


@router.get("/manual")
async def get_manual(admin_id: int = Depends(verify_session)):
    return {"content": get_profile_service().get_manual()}


@router.put("/manual")
async def update_manual(data: ManualUpdate, admin_id: int = Depends(verify_session)):
    get_profile_service().update_manual(data.content)
    return {"success": True}


# ---------- Settings (enabled toggle persisted) ----------

@router.put("/settings")
async def update_settings(data: SettingsUpdate, admin_id: int = Depends(verify_session)):
    svc = get_profile_service()
    profile_cfg = svc.config.setdefault("profile", {})
    if data.enabled is not None:
        profile_cfg["enabled"] = bool(data.enabled)
    if data.daily_hour is not None:
        if not 0 <= data.daily_hour <= 23:
            raise HTTPException(status_code=400, detail="daily_hour must be between 0 and 23")
        profile_cfg["daily_hour"] = data.daily_hour
    if _save_config:
        _save_config()
    return {"success": True, "enabled": svc.is_enabled(),
            "daily_hour": profile_cfg.get("daily_hour", 3)}


# ---------- L3 claims ----------

@router.get("/claims")
async def list_claims(status: Optional[str] = None, tier: Optional[str] = None,
                      admin_id: int = Depends(verify_session)):
    return {"claims": get_profile_service().list_claims(status=status, tier=tier)}


@router.post("/claims/{claim_id}/resolve")
async def resolve_claim(claim_id: int, data: ClaimResolve,
                        admin_id: int = Depends(verify_session)):
    try:
        ok = get_profile_service().resolve_claim(claim_id, data.action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Claim not found")
    return {"success": True}


# ---------- Manually trigger quick cycle ----------

@router.post("/distill")
async def run_distill(data: DistillRequest = DistillRequest(),
                      admin_id: int = Depends(verify_session)):
    svc = get_profile_service()
    if not svc.is_enabled():
        raise HTTPException(status_code=400, detail="Profile feature is not enabled")
    return svc.distill_daily(day=data.day)


@router.post("/audit")
async def run_audit(admin_id: int = Depends(verify_session)):
    svc = get_profile_service()
    if not svc.is_enabled():
        raise HTTPException(status_code=400, detail="Profile feature is not enabled")
    return svc.auditor.audit_batch(svc.get_active_version_id())


@router.get("/audit-log")
async def audit_log(limit: int = 50, admin_id: int = Depends(verify_session)):
    return {"logs": get_profile_service().auditor.recent_logs(limit=min(limit, 200))}


# ---------- Dream ----------

@router.get("/dreams")
async def list_dreams(admin_id: int = Depends(verify_session)):
    return {"dreams": get_dream_manager().list_dreams()}


@router.post("/dreams")
async def create_dream(data: DreamCreate, admin_id: int = Depends(verify_session)):
    svc = get_profile_service()
    if not svc.is_enabled():
        raise HTTPException(status_code=400, detail="Profile feature is not enabled")
    try:
        dream = get_dream_manager().start_dream(scope=data.scope,
                                                instructions=data.instructions)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"dream": dream}


@router.get("/dreams/{dream_id}")
async def get_dream(dream_id: int, admin_id: int = Depends(verify_session)):
    dream = get_dream_manager().get_dream(dream_id)
    if not dream:
        raise HTTPException(status_code=404, detail="Dream not found")
    return {"dream": dream}


@router.post("/dreams/{dream_id}/cancel")
async def cancel_dream(dream_id: int, admin_id: int = Depends(verify_session)):
    if not get_dream_manager().cancel_dream(dream_id):
        raise HTTPException(status_code=400, detail="Dream is not running")
    return {"success": True}


# ---------- Candidate version review ----------

@router.get("/versions/{version_id}/diff")
async def version_diff(version_id: int, admin_id: int = Depends(verify_session)):
    return get_dream_manager().diff(version_id)


@router.post("/versions/{version_id}/activate")
async def activate_version(version_id: int, admin_id: int = Depends(verify_session)):
    if not get_dream_manager().activate_version(version_id):
        raise HTTPException(status_code=400, detail="Only candidate versions can be activated")
    return {"success": True}


@router.post("/versions/{version_id}/discard")
async def discard_version(version_id: int, admin_id: int = Depends(verify_session)):
    if not get_dream_manager().discard_version(version_id):
        raise HTTPException(status_code=400, detail="Only candidate versions can be discarded")
    return {"success": True}
