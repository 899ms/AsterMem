"""
Explore mode API endpoints

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from fastapi import APIRouter, Depends, Body
from starlette.responses import StreamingResponse
from typing import Optional, AsyncGenerator

from memory.ai_explorer import AIExplorer
from memory.search import SearchEngine
from web.api import verify_session

# Global instance
_explorer: AIExplorer = None

router = APIRouter(prefix="/api/explore", tags=["explore"])

def get_explorer():
    if _explorer is None:
        raise RuntimeError("AI Explorer not initialized")
    return _explorer

def init_explore_api(search_engine: SearchEngine, config: dict):
    """Initialize the Explore API"""
    global _explorer
    
    # Pass the full config so AIExplorer can choose the API based on mode
    _explorer = AIExplorer(search_engine, config)


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "X-Accel-Buffering": "no",
    "Transfer-Encoding": "chunked",
}


async def stream_wrapper(gen: AsyncGenerator) -> AsyncGenerator[bytes, None]:
    """
    Wraps AIExplorer's NDJSON generator into standard SSE frames.

    Background: Each event from the explorer produces a single line of JSON (json.dumps
    escapes newlines, so one event always occupies exactly one physical line), but the
    response declares text/event-stream — browsers parse using the `data:` prefix, so
    without it the frontend receives no events.
    Design intent: Wrap each line with `data: `, append a `[DONE]` sentinel at the end,
    aligned with the frontend's sseStream parser.
    """
    async for chunk in gen:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        for line in text.splitlines():
            if line.strip():
                yield f"data: {line}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


@router.post("/search")
async def explore_search(
    query: str = Body(...),
    context: Optional[str] = Body(None),
    explorer: AIExplorer = Depends(get_explorer),
    admin_id: int = Depends(verify_session),
):
    """AI-guided knowledge exploration"""

    return StreamingResponse(
        stream_wrapper(explorer.explore(query, context)),
        media_type="text/event-stream; charset=utf-8",
        headers=SSE_HEADERS,
    )


@router.post("/drill")
async def explore_drill(
    trunk_id: str = Body(...),
    question: Optional[str] = Body(None),
    explorer: AIExplorer = Depends(get_explorer),
    admin_id: int = Depends(verify_session),
):
    """Drill-down analysis"""

    return StreamingResponse(
        stream_wrapper(explorer.drill_down(trunk_id, question or "")),
        media_type="text/event-stream; charset=utf-8",
        headers=SSE_HEADERS,
    )


@router.post("/generate-memory")
async def generate_memory(
    trunks: list = Body(...),
    query: str = Body(...),
    extra_requirement: Optional[str] = Body(""),
    explorer: AIExplorer = Depends(get_explorer),
    admin_id: int = Depends(verify_session),
):
    """Generate new memory based on exploration results"""

    return StreamingResponse(
        stream_wrapper(explorer.generate_memory(trunks, query, extra_requirement or "")),
        media_type="text/event-stream; charset=utf-8",
        headers=SSE_HEADERS,
    )
