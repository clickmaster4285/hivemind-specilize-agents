"""The self-hosted CAPTCHA and the admin unlock (BACKLOG L16 S2b) — through the real app, offline."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core import captcha
from src.core.abuse import ACCOUNT_LOCK_FAILURES, ADDRESS_BLOCK_FAILURES, LOGIN_CAPTCHA_AFTER, LOGIN_WINDOW
from src.core.auth import get_db, hash_password
from src.core.captcha import ALPHABET, CAPTCHA_LENGTH, CAPTCHA_LIFETIME_SECONDS, MAX_OPEN, CaptchaStore
from src.core.db import Base
from src.core.models_db import LoginFailure, User
from web import server
from web.routers.auth import CAPTCHA_HEADER

pytestmark = pytest.mark.usefixtures("cheap_hashing")

PASSWORD = "longenough"
WRONG = "not-the-password"
PROXY = ("127.0.0.1", 50000)
LAN_PC = "192.168.88.20"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add_all([User(id=1, username="boss", password_hash=hash_password(PASSWORD), role="admin"),
                     User(id=2, username="ana", password_hash=hash_password(PASSWORD))])
    session.commit()
    server.app.dependency_overrides[get_db] = lambda: session
    yield session
    server.app.dependency_overrides.clear()
    session.close()


def _pc(address: str = LAN_PC) -> TestClient:
    return TestClient(server.app, client=PROXY, headers={"X-Forwarded-For": address})


def _login(client: TestClient, username: str = "ana", password: str = WRONG, **extra):
    return client.post("/api/auth/login", json={"username": username, "password": password, **extra})


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _answer(store: CaptchaStore, captcha_id: str) -> str:
    return store._open[captcha_id][0]


# ── the store ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_captcha_is_a_png_of_its_answer_characters():
    made = CaptchaStore().new()
    header, data = made["image"].split(",", 1)
    assert header == "data:image/png;base64"
    assert base64.b64decode(data).startswith(PNG_SIGNATURE)


def test_an_answer_works_once_and_ignores_case_and_spaces():
    store = CaptchaStore()
    made = store.new()
    answer = _answer(store, made["id"])
    assert len(answer) == CAPTCHA_LENGTH and set(answer) <= set(ALPHABET)
    assert store.check(made["id"], f" {answer.lower()} ")
    assert not store.check(made["id"], answer)                      # used up


def test_a_wrong_answer_uses_the_captcha_up():
    store = CaptchaStore()
    made = store.new()
    answer = _answer(store, made["id"])
    assert not store.check(made["id"], "wrong")
    assert not store.check(made["id"], answer)


def test_a_captcha_runs_out():
    clock = _Clock()
    store = CaptchaStore(clock)
    made = store.new()
    answer = _answer(store, made["id"])
    clock.now += CAPTCHA_LIFETIME_SECONDS
    assert not store.check(made["id"], answer)


def test_unknown_or_missing_answers_are_refused():
    store = CaptchaStore()
    assert not store.check(None, "ABC")
    assert not store.check("no-such-id", "ABC")
    assert not store.check(store.new()["id"], None)
    assert not store.check(store.new()["id"], "ünïcödé")             # not ASCII: just wrong, no crash


def test_only_so_many_wait_at_a_time(monkeypatch):
    monkeypatch.setattr(captcha, "draw", lambda text: b"")           # the picture does not matter here
    store = CaptchaStore()
    first = store.new()["id"]
    for _ in range(MAX_OPEN):
        store.new()
    assert len(store._open) == MAX_OPEN and first not in store._open


# ── login and sign-up ─────────────────────────────────────────────────────────────────────────────────

def test_the_api_hands_out_captchas():
    made = _pc().get("/api/auth/captcha").json()
    assert made["id"] in captcha.store._open and made["image"].startswith("data:image/png")


def test_from_the_third_failure_a_login_needs_a_captcha(db, solved_captcha):
    client = _pc()
    for n in range(1, LOGIN_CAPTCHA_AFTER + 1):
        failed = _login(client)
        assert failed.status_code == 401
        assert (CAPTCHA_HEADER in failed.headers) == (n >= LOGIN_CAPTCHA_AFTER)   # told with the failure
    asked = _login(client, password=PASSWORD)
    assert asked.status_code == 400 and asked.headers[CAPTCHA_HEADER] == "required"
    assert _login(client, password=PASSWORD, **solved_captcha()).status_code == 200


def test_a_try_without_the_captcha_is_not_counted(db):
    client = _pc()
    for _ in range(LOGIN_CAPTCHA_AFTER):
        _login(client)
    for _ in range(ACCOUNT_LOCK_FAILURES):                          # a script, no person to read the picture
        assert _login(client).status_code == 400
    assert db.query(LoginFailure).filter_by(key="user:2").count() == LOGIN_CAPTCHA_AFTER


def test_a_wrong_answer_is_refused_with_a_new_picture_asked(db):
    client = _pc()
    for _ in range(LOGIN_CAPTCHA_AFTER):
        _login(client)
    made = client.get("/api/auth/captcha").json()
    refused = _login(client, password=PASSWORD, captcha_id=made["id"], captcha_answer="WRONG1")
    assert refused.status_code == 400 and "new picture" in refused.json()["detail"]


def test_a_login_without_failures_needs_no_captcha(db):
    assert _login(_pc(), password=PASSWORD).status_code == 200


def test_sign_up_always_needs_a_captcha(db, solved_captcha):
    body = {"username": "sara", "email": "sara@example.com", "password": PASSWORD}
    refused = _pc().post("/api/auth/signup", json=body)
    assert refused.status_code == 400 and refused.headers[CAPTCHA_HEADER] == "required"
    assert _pc().post("/api/auth/signup", json={**body, **solved_captcha()}).status_code == 201


# ── admin unlock ──────────────────────────────────────────────────────────────────────────────────────

def _admin() -> TestClient:
    client = _pc("192.168.88.2")
    assert _login(client, "boss", PASSWORD).status_code == 200
    return client


def _lock(db: Session, key: str, failures: int) -> None:
    db.add_all([LoginFailure(key=key) for _ in range(failures)])
    db.commit()


def test_an_admin_sees_and_ends_an_account_lock(db):
    _lock(db, "user:2", ACCOUNT_LOCK_FAILURES)
    admin = _admin()
    listed = {u["username"]: u for u in admin.get("/api/auth/users").json()}
    assert listed["ana"]["locked_until"] and listed["ana"]["failed_logins"] == ACCOUNT_LOCK_FAILURES
    locks = admin.get("/api/auth/users/locks").json()
    assert [(a["username"], a["failures"]) for a in locks["accounts"]] == [("ana", ACCOUNT_LOCK_FAILURES)]
    assert _login(_pc(), password=PASSWORD).status_code == 429
    assert admin.delete("/api/auth/users/2/lock").json() == {"unlocked": True}
    assert _login(_pc(), password=PASSWORD).status_code == 200      # no wait, no CAPTCHA left either
    assert admin.delete("/api/auth/users/2/lock").json() == {"unlocked": False}


def test_an_admin_unblocks_a_computer(db):
    _lock(db, f"addr:{LAN_PC}", ADDRESS_BLOCK_FAILURES)
    admin = _admin()
    assert admin.get("/api/auth/users/locks").json()["addresses"][0]["address"] == LAN_PC
    assert _login(_pc(), password=PASSWORD).status_code == 429
    assert admin.delete(f"/api/auth/users/locks/address/{LAN_PC}").json() == {"unlocked": True}
    assert _login(_pc(), password=PASSWORD).status_code == 200


def test_old_failures_are_no_lock(db):
    _lock(db, "user:2", ACCOUNT_LOCK_FAILURES)
    for row in db.query(LoginFailure):
        row.failed_at -= LOGIN_WINDOW
    db.commit()
    assert _admin().get("/api/auth/users/locks").json() == {"accounts": [], "addresses": []}


def test_members_cannot_unlock(db):
    member = _pc()
    assert _login(member, password=PASSWORD).status_code == 200
    assert member.get("/api/auth/users/locks").status_code == 403
    assert member.delete("/api/auth/users/2/lock").status_code == 403
    assert member.delete(f"/api/auth/users/locks/address/{LAN_PC}").status_code == 403
