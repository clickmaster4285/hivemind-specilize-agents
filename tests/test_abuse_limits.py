"""Abuse limits (BACKLOG L16 S2): sign-in back-off and locks per account and per address, request rates,
the body cap, and whose address counts — through the real app, offline."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core import abuse
from src.core.abuse import (
    ACCOUNT_LOCK_FAILURES,
    ADDRESS_BLOCK_FAILURES,
    ANON_BURST,
    LOGIN_BACKOFF_FIRST,
    LOGIN_BACKOFF_MAX,
    LOGIN_FREE_FAILURES,
    LOGIN_LOCK,
    LOGIN_WINDOW,
    USER_BURST,
    RateLimiter,
    account_wait,
    client_address,
)
from src.core.auth import get_db, hash_password
from src.core.db import Base
from src.core.models_db import LoginFailure, User
from web import limits, server

# ~150 sign-ins: at the real bcrypt cost that alone takes half a minute
pytestmark = pytest.mark.usefixtures("cheap_hashing")

PASSWORD = "longenough"
WRONG = "not-the-password"
PROXY = ("127.0.0.1", 50000)           # the UI's Node server, the only trusted proxy
LAN_PC = "192.168.88.20"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add(User(id=1, username="ana", email="ana@example.com", password_hash=hash_password(PASSWORD)))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def app_db(db):
    server.app.dependency_overrides[get_db] = lambda: db
    yield db
    server.app.dependency_overrides.clear()


def _pc(address: str = LAN_PC) -> TestClient:
    """A browser on the LAN, reaching the API through the UI's proxy."""
    return TestClient(server.app, client=PROXY, headers={"X-Forwarded-For": address})


def _login(client: TestClient, username: str = "ana", password: str = WRONG):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _age_failures(db: Session, by: timedelta) -> None:
    for row in db.scalars(select(LoginFailure)):
        row.failed_at -= by
    db.commit()


def _fail(client: TestClient, db: Session, times: int, username: str = "ana") -> None:
    """`times` failed sign-ins, each after any back-off has run out."""
    for _ in range(times):
        assert _login(client, username).status_code == 401
        _age_failures(db, LOGIN_BACKOFF_MAX)


# ── whose address ─────────────────────────────────────────────────────────────────────────────────────

def _scope(peer: str, forwarded: str | None = None) -> dict:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return {"client": (peer, 1), "headers": headers}


def test_the_forwarded_address_counts_only_from_the_trusted_proxy():
    assert client_address(_scope("127.0.0.1", LAN_PC)) == LAN_PC
    assert client_address(_scope("127.0.0.1", f"6.6.6.6, {LAN_PC}")) == LAN_PC     # the proxy's own entry
    assert client_address(_scope("192.168.88.30", "6.6.6.6")) == "192.168.88.30"  # a forged header
    assert client_address(_scope("127.0.0.1")) == "127.0.0.1"                      # a tool on this host
    assert client_address(_scope("127.0.0.1", f"::ffff:{LAN_PC}")) == LAN_PC       # Node's IPv4-on-IPv6


# ── sign-in ───────────────────────────────────────────────────────────────────────────────────────────

def test_the_wait_doubles_past_the_free_failures_and_then_locks():
    assert account_wait(LOGIN_FREE_FAILURES - 1) == timedelta(0)
    assert account_wait(LOGIN_FREE_FAILURES) == LOGIN_BACKOFF_FIRST
    assert account_wait(LOGIN_FREE_FAILURES + 1) == LOGIN_BACKOFF_FIRST * 2
    assert account_wait(ACCOUNT_LOCK_FAILURES - 1) <= LOGIN_BACKOFF_MAX
    assert account_wait(ACCOUNT_LOCK_FAILURES) == LOGIN_LOCK


def test_after_the_free_failures_the_next_try_must_wait(app_db):
    client = _pc()
    for _ in range(LOGIN_FREE_FAILURES):
        assert _login(client).status_code == 401
    refused = _login(client, password=PASSWORD)              # not even the right password gets in now
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) == LOGIN_BACKOFF_FIRST.total_seconds()
    assert "try again in" in refused.json()["detail"]
    _age_failures(app_db, LOGIN_BACKOFF_FIRST)
    assert _login(client, password=PASSWORD).status_code == 200


def test_a_refused_try_is_not_counted(app_db):
    client = _pc()
    _fail(client, app_db, LOGIN_FREE_FAILURES)
    assert _login(client).status_code == 401
    count = len(app_db.scalars(select(LoginFailure).where(LoginFailure.key == "user:1")).all())
    assert _login(client).status_code == 429
    assert len(app_db.scalars(select(LoginFailure).where(LoginFailure.key == "user:1")).all()) == count


def test_enough_failures_lock_the_account_until_they_are_forgotten(app_db):
    client = _pc()
    _fail(client, app_db, ACCOUNT_LOCK_FAILURES)
    locked = _login(client, password=PASSWORD)
    assert locked.status_code == 429 and "min" in locked.json()["detail"]
    other_pc = _pc("192.168.88.21")                         # the account is locked, not just the PC
    assert _login(other_pc, password=PASSWORD).status_code == 429
    _age_failures(app_db, LOGIN_LOCK)
    assert _login(client, password=PASSWORD).status_code == 200


def test_a_good_sign_in_clears_the_account_count(app_db):
    client = _pc()
    _fail(client, app_db, LOGIN_FREE_FAILURES)
    assert _login(client, password=PASSWORD).status_code == 200
    assert _login(client).status_code == 401
    assert _login(client).status_code == 401                # back to the free failures, no wait


def test_username_and_email_are_one_account(app_db):
    client = _pc()
    _fail(client, app_db, LOGIN_FREE_FAILURES - 1)
    assert _login(client, "ANA@example.com").status_code == 401
    assert _login(client).status_code == 429


def test_an_unknown_name_is_throttled_like_an_account(app_db):
    client, real = _pc(), _pc("192.168.88.99")
    for _ in range(LOGIN_FREE_FAILURES):
        unknown, known = _login(client, "nobody"), _login(real)
        assert (unknown.status_code, unknown.json()) == (known.status_code, known.json())
    assert _login(client, "nobody").json() == _login(real).json()       # both now told to wait


def test_one_pc_trying_many_names_is_blocked(app_db):
    client = _pc()
    for n in range(ADDRESS_BLOCK_FAILURES):
        assert _login(client, f"guess{n}").status_code == 401
    assert _login(client, password=PASSWORD).status_code == 429         # this PC, even the right password
    assert _login(_pc("192.168.88.21"), password=PASSWORD).status_code == 200   # other PCs are not
    _age_failures(app_db, LOGIN_LOCK)
    assert _login(client, password=PASSWORD).status_code == 200


def test_a_forged_address_from_outside_the_proxy_does_not_dodge_the_block(app_db):
    for n in range(ADDRESS_BLOCK_FAILURES):
        forger = TestClient(server.app, client=(LAN_PC, 50000), headers={"X-Forwarded-For": f"10.0.0.{n}"})
        assert _login(forger, f"guess{n}").status_code == 401
    assert _login(TestClient(server.app, client=(LAN_PC, 50000)), password=PASSWORD).status_code == 429


def test_old_failures_are_deleted(app_db):
    client = _pc()
    _login(client)
    _age_failures(app_db, LOGIN_WINDOW)
    _login(client)
    assert len(app_db.scalars(select(LoginFailure)).all()) == 2          # the new failure, account + address


# ── request rates ─────────────────────────────────────────────────────────────────────────────────────

class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_bucket_allows_a_burst_then_refills_at_the_rate():
    clock = _Clock()
    limiter = RateLimiter(per_minute=60, burst=3, clock=clock)
    assert [limiter.take("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.take("a") == pytest.approx(1.0)          # one call a second
    assert limiter.take("b") == 0.0                         # every caller has its own bucket
    clock.now += 1.0
    assert limiter.take("a") == 0.0


def test_idle_buckets_are_dropped():
    clock = _Clock()
    limiter = RateLimiter(per_minute=60, burst=3, clock=clock)
    limiter.take("a")
    clock.now += abuse.BUCKET_PRUNE_SECONDS
    limiter.take("b")
    assert set(limiter._buckets) == {"b"}


@pytest.fixture
def frozen_rates(monkeypatch):
    """The app's buckets on a clock that stands still, so no call is refilled while a test runs."""
    clock = _Clock()
    for limiter in (limits.users, limits.anonymous):
        monkeypatch.setattr(limiter, "clock", clock)
    return clock


def test_calls_without_a_sign_in_are_limited_per_address(frozen_rates):
    client = _pc()
    for _ in range(ANON_BURST):
        assert client.get("/api/auth/sso/status").status_code == 200
    refused = client.get("/api/auth/sso/status")
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) >= 1
    assert _pc("192.168.88.21").get("/api/auth/sso/status").status_code == 200


def test_a_signed_in_user_has_an_own_larger_allowance(app_db, frozen_rates):
    client = _pc()
    assert _login(client, password=PASSWORD).status_code == 200
    same_pc_signed_out = _pc()
    while same_pc_signed_out.get("/api/auth/sso/status").status_code != 429:   # the address's allowance runs out …
        pass
    for _ in range(USER_BURST):                             # … the user's does not depend on it
        assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/auth/me").status_code == 429


def test_only_the_api_is_limited(frozen_rates):
    client = _pc()
    for _ in range(ANON_BURST + 1):
        assert client.get("/").status_code == 200


# ── body cap ──────────────────────────────────────────────────────────────────────────────────────────

SMALL_CAP = 1024


def test_a_declared_large_body_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(limits, "MAX_BODY_BYTES", SMALL_CAP)
    response = _pc().post("/api/auth/login", content=b"x" * (SMALL_CAP + 1),
                          headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_a_streamed_body_is_cut_off_at_the_cap(monkeypatch, app_db):
    monkeypatch.setattr(limits, "MAX_BODY_BYTES", SMALL_CAP)

    def chunks():                                          # no Content-Length: sent chunked
        yield b'{"username": "ana", "password": "'
        yield b"x" * SMALL_CAP
        yield b'"}'

    response = _pc().post("/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]
