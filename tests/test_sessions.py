"""Cookie sessions (BACKLOG L16 S1): HttpOnly access + refresh cookies, rotation, reuse detection, logout,
disabled users, the cross-site write check and the signing-key check — through the real app, offline."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.auth import (
    ACCESS_COOKIE,
    MIN_SECRET_BYTES,
    REFRESH_COOKIE,
    REFRESH_REUSE_GRACE,
    check_secret,
    get_db,
    hash_password,
)
from src.core.config import DEV_JWT_SECRET, Config
from src.core.db import Base
from src.core.models_db import RefreshToken, User
from web import server

PASSWORD = "longenough"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add(User(id=1, username="ana", password_hash=hash_password(PASSWORD), role="member"))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(db):
    server.app.dependency_overrides[get_db] = lambda: db
    yield TestClient(server.app)                     # no `with`: the app's startup (DB, models, worker) stays off
    server.app.dependency_overrides.clear()


def _sign_in(client: TestClient):
    response = client.post("/api/auth/login", json={"username": "ana", "password": PASSWORD})
    assert response.status_code == 200
    return response


def _cookie_headers(response) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def _age_used_tokens(db: Session, by: timedelta) -> None:
    for row in db.scalars(select(RefreshToken).where(RefreshToken.used_at.is_not(None))):
        row.used_at -= by
    db.commit()


def test_sign_in_sets_httponly_cookies_and_no_token_in_the_body(client):
    response = _sign_in(client)
    assert set(response.json()) == {"user"}
    cookies = _cookie_headers(response)
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        assert "HttpOnly" in cookies[name] and "SameSite=strict" in cookies[name]
        assert "Secure" not in cookies[name]          # plain http (localhost); https gets Secure
    assert "Path=/api/auth" in cookies[REFRESH_COOKIE]    # the refresh token goes only to /api/auth
    assert client.get("/api/auth/me").json()["username"] == "ana"


def test_over_https_the_cookies_are_secure(db):
    server.app.dependency_overrides[get_db] = lambda: db
    try:
        response = _sign_in(TestClient(server.app, base_url="https://testserver"))
    finally:
        server.app.dependency_overrides.clear()
    assert all("Secure" in h for h in _cookie_headers(response).values())


def test_without_a_cookie_the_api_refuses(client):
    assert client.get("/api/auth/me").status_code == 401


def test_refresh_rotates_and_a_spent_token_shown_again_ends_the_sign_in(client, db):
    _sign_in(client)
    first = client.cookies[REFRESH_COOKIE]
    assert client.post("/api/auth/refresh").status_code == 200
    assert client.cookies[REFRESH_COOKIE] != first
    _age_used_tokens(db, REFRESH_REUSE_GRACE * 2)
    thief = TestClient(server.app, cookies={REFRESH_COOKIE: first})
    assert thief.post("/api/auth/refresh").status_code == 401
    assert client.get("/api/auth/me").status_code == 401           # the whole family is revoked
    assert client.post("/api/auth/refresh").status_code == 401


def test_two_tabs_refreshing_together_are_not_taken_for_theft(client):
    _sign_in(client)
    first = client.cookies[REFRESH_COOKIE]
    assert client.post("/api/auth/refresh").status_code == 200
    other_tab = TestClient(server.app, cookies={REFRESH_COOKIE: first})
    response = other_tab.post("/api/auth/refresh")
    assert response.status_code == 200
    assert REFRESH_COOKIE not in _cookie_headers(response)         # the browser already holds the next one
    assert client.get("/api/auth/me").status_code == 200


def test_logout_stops_the_access_token_at_once(client):
    _sign_in(client)
    access = client.cookies[ACCESS_COOKIE]
    assert client.post("/api/auth/logout").status_code == 200
    copied = TestClient(server.app, cookies={ACCESS_COOKIE: access})
    assert copied.get("/api/auth/me").status_code == 401


def test_a_disabled_user_is_refused_with_a_live_token(client, db):
    _sign_in(client)
    db.get(User, 1).status = "disabled"
    db.commit()
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401


def test_a_write_from_another_site_is_refused(client):
    refused = client.post("/api/auth/login", json={"username": "ana", "password": PASSWORD},
                          headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403
    own = client.post("/api/auth/login", json={"username": "ana", "password": PASSWORD},
                      headers={"Origin": "http://testserver"})
    assert own.status_code == 200
    proxied = client.post("/api/auth/login", json={"username": "ana", "password": PASSWORD},
                          headers={"Origin": "http://localhost:3000", "X-Forwarded-Host": "localhost:3000"})
    assert proxied.status_code == 200                              # through the Next.js rewrite


def test_a_guessable_signing_key_is_refused():
    config = Config.from_env()
    for weak in (DEV_JWT_SECRET, "x" * (MIN_SECRET_BYTES - 1)):
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            check_secret(replace(config, jwt_secret=weak))
    check_secret(replace(config, jwt_secret="x" * MIN_SECRET_BYTES))
