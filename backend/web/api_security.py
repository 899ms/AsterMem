"""
Scanner defence REST API (read side of the guard in scan_guard.py)

Background: the guard refuses probes before they reach a handler and, once an address is blocked,
without writing a log line. That is what makes a sweep cheap, but it also means the owner of a
self-hosted instance had no way to tell the guard apart from a guard that was switched off: eight
log lines for eighty-one refused requests, visible only over SSH.
Design intent: expose the guard's own counters over the same authenticated session the rest of the
UI uses, so the security page can answer "is this on, who is blocked, for how long" without
shelling into the box.
Key constraints:
  - Reports whether the middleware is actually installed, not merely how it is configured. An
    instance that set ASTERMEM_SCAN_GUARD=0 must not be shown a page implying it is protected.
  - Releasing an address cannot be used by the address it would free: the guard refuses a blocked
    caller on every path, so the request never reaches this router. It is still session-guarded,
    because everything here — who is blocked, how long, at what threshold — helps time a sweep.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import ipaddress
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.api import verify_session

router = APIRouter(prefix="/api/security", tags=["security"])

_guard = None
_enabled: bool = False


def init_security_api(guard, enabled: bool):
    """Wire in the same ScanGuard instance the middleware uses; a copy would report empty counters."""
    global _guard, _enabled
    _guard = guard
    _enabled = bool(enabled)


@router.get("")
async def get_security_status(admin_id: int = Depends(verify_session)):
    """
    Report the scanner defence state.

    Returns the guard's snapshot plus whether it is installed. When it is disabled the counters are
    omitted rather than sent as zeroes, so the page can say "off" instead of "nothing has happened".
    """
    if not _enabled or _guard is None:
        return {"enabled": False}
    return {"enabled": True, **_guard.snapshot()}


class ReleaseRequest(BaseModel):
    address: str


@router.post("/release")
async def release_address(data: ReleaseRequest, admin_id: int = Depends(verify_session)):
    """
    Lift the block on one address.

    The escape hatch for a client that belongs here and tripped a rule: without it the only way
    back is restarting the service, which on this project also discards whatever the background
    queue was part way through.

    The address is normalised before lookup so the value the page displays is the value that
    matches, whatever spelling of it the caller sends.
    """
    if not _enabled or _guard is None:
        raise HTTPException(status_code=400, detail="Scanner defence is not enabled")
    try:
        address = str(ipaddress.ip_address(data.address.strip()))
    except ValueError:
        raise HTTPException(status_code=400, detail="Not an IP address")
    # Idempotent: an address whose block lapsed between the page loading and the click is already
    # in the state the caller wanted, and reporting that as an error would be noise.
    return {"success": True, "released": _guard.release(address)}
