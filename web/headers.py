"""Security headers on every API response (BACKLOG L16 S4, doc/SECURITY.md).

The pages' own policy (scripts, styles, images) is set by the UI: frontend/src/proxy.ts. The API serves
data and downloads, never a page, so its policy allows nothing: a file that a browser opens anyway (an upload
with a wrong type, an SVG with a script) cannot run code or reach the app's cookies, because `sandbox` puts it
in an origin of its own. Pure ASGI like the other middlewares here (see CrossSiteGuard in web/server.py).
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from web.limits import API_PREFIX

# every response: no guessing a type from the bytes, no address sent to other sites, never inside a frame
COMMON_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
# /api only: the API's own "/" still serves the old static page (web/static), which needs its scripts
API_POLICY = "default-src 'none'; frame-ancestors 'none'; sandbox"


class SecurityHeaders:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith(API_PREFIX)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in COMMON_HEADERS.items():
                    headers.setdefault(name, value)
                if is_api:
                    headers.setdefault("Content-Security-Policy", API_POLICY)
            await send(message)

        await self.app(scope, receive, send_with_headers)
