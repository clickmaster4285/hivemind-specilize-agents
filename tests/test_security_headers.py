"""Security headers (BACKLOG L16 S4): every API response carries them, refusals included; the locked-down
policy applies to /api only. Through the real app, offline (the routes used here touch no network)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from web import server
from web.headers import API_POLICY, COMMON_HEADERS
from web.limits import MAX_BODY_BYTES

client = TestClient(server.app)


def _has_common(response) -> None:
    for name, value in COMMON_HEADERS.items():
        assert response.headers[name] == value


def test_api_response_carries_headers_and_policy():
    response = client.get("/api/auth/me")         # no session: a plain 401 from a route
    assert response.status_code == 401
    _has_common(response)
    assert response.headers["Content-Security-Policy"] == API_POLICY


def test_refusal_before_the_route_carries_them_too():
    # refused by RequestLimits from the declared size alone, before any route or guard runs
    response = client.post("/api/auth/login", headers={"Content-Length": str(MAX_BODY_BYTES + 1)})
    assert response.status_code == 413
    _has_common(response)
    assert response.headers["Content-Security-Policy"] == API_POLICY


def test_old_static_page_gets_common_headers_but_no_api_policy():
    response = client.get("/")
    _has_common(response)
    assert "Content-Security-Policy" not in response.headers
