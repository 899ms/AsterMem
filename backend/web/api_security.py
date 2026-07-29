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
  - Read-only. Blocks are load shedding rather than a security boundary, and a write endpoint here
    would be a way to unblock an address by reaching the API — which is what a blocked address
    cannot do, so it belongs behind the session rather than in the guard.
  - Reports whether the middleware is actually installed, not merely how it is configured. An
    instance that set ASTERMEM_SCAN_GUARD=0 must not be shown a page implying it is protected.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from typing import Optional

from fastapi import APIRouter, Depends

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
