"""GET /api/events — one Server-Sent Events stream per signed-in tab (BACKLOG L29, src/core/live_events.py).

Sends `data: {"type": "<topic>"}` when something the user may see changed, and a keep-alive comment when quiet.
About once a minute the sign-in is checked again (expired, signed out, deactivated or deleted → the stream ends,
and the browser reconnects through the normal refresh, or stops at the sign-in page).
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.core import live_events
from src.core.auth import get_current_user, stream_user
from src.core.models_db import User

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 20          # a comment line keeps proxies from closing a quiet stream
RECHECK_SECONDS = 60            # how often the sign-in is checked again while the stream is open


def _still_signed_in(request: Request) -> bool:
    from src.core.db import session_scope

    try:
        with session_scope() as db:
            get_current_user(request, db)
    except HTTPException:
        return False
    return True


@router.get("/api/events")
async def live_events_stream(request: Request, user: User = Depends(stream_user)) -> StreamingResponse:
    sub = live_events.subscribe(user.id, user.role == "admin")

    async def stream():
        checked = time.monotonic()
        try:
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                topic = await live_events.next_topic(sub, HEARTBEAT_SECONDS)
                if time.monotonic() - checked >= RECHECK_SECONDS:
                    checked = time.monotonic()
                    if not await asyncio.to_thread(_still_signed_in, request):
                        yield f"data: {json.dumps({'type': 'signed_out'})}\n\n"
                        break
                yield f"data: {json.dumps({'type': topic})}\n\n" if topic else ": keep-alive\n\n"
        finally:
            live_events.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})
