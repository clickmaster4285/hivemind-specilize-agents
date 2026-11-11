"""Request limits for every `/api` call (BACKLOG L16 S2): a body cap and a rate per caller.

A pure ASGI middleware (not `@app.middleware`), so it can count a body while it streams in. The limits
themselves live in src/core/abuse.py.
"""

from __future__ import annotations

import math

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.abuse import (
    ANON_BURST,
    ANON_RATE_PER_MINUTE,
    USER_BURST,
    USER_RATE_PER_MINUTE,
    RateLimiter,
    client_address,
)
from src.core.auth import ACCESS_COOKIE, decode_token

API_PREFIX = "/api"
# the largest real upload is an artifact file (src/tools/artifacts.py, 25 MB) plus multipart overhead
MAX_BODY_BYTES = 30 * 1024 * 1024
BYTES_PER_MB = 1024 * 1024

users = RateLimiter(USER_RATE_PER_MINUTE, USER_BURST)
anonymous = RateLimiter(ANON_RATE_PER_MINUTE, ANON_BURST)


def _too_large() -> str:
    return f"Request too large (max {MAX_BODY_BYTES // BYTES_PER_MB} MB)"


def _caller(scope: Scope) -> tuple[RateLimiter, str]:
    """A valid access cookie → that user's bucket; anything else → the address's. Only the signature is
    checked here (no database): whether the session still holds is the route's job."""
    token = Request(scope).cookies.get(ACCESS_COOKIE)
    if token:
        try:
            return users, decode_token(token)["sub"]
        except HTTPException:
            pass
    return anonymous, client_address(scope)


def _capped(receive: Receive) -> Receive:
    """Refuse a body that grows past the cap while it arrives (chunked, or a wrong Content-Length).
    FastAPI passes its HTTPException on while it reads a body, so the client gets the 413."""
    seen = 0

    async def wrapped() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail=_too_large())
        return message

    return wrapped


class RequestLimits:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(API_PREFIX):
            await self.app(scope, receive, send)
            return
        declared = Request(scope).headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            await JSONResponse({"detail": _too_large()}, status_code=413)(scope, receive, send)
            return
        limiter, key = _caller(scope)
        wait = limiter.take(key)
        if wait:
            seconds = math.ceil(wait)
            await JSONResponse({"detail": f"Too many requests — try again in {seconds} s"}, status_code=429,
                               headers={"Retry-After": str(seconds)})(scope, receive, send)
            return
        await self.app(scope, _capped(receive), send)


def reset() -> None:
    """Forget all request counts (the tests start each case from zero)."""
    users.reset()
    anonymous.reset()
