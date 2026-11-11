"""Self sign-up (BACKLOG L16 S7): open sign-up and invite links both make an account that waits for an
admin; approve / reject; the open switch; sign-ups per address — through the real app, offline."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.auth import get_db, hash_password
from src.core.db import Base
from src.core.models_db import USER_PENDING, User, UserInvite
from src.core.signup import INVITE_LIFETIME, SIGNUP_WINDOW, SIGNUPS_PER_ADDRESS
from web import server

pytestmark = pytest.mark.usefixtures("cheap_hashing")

ADMIN_PASSWORD = "admin-password"
PASSWORD = "longenough"
PROXY = ("127.0.0.1", 50000)            # the UI's Node server, the only trusted proxy
LAN_PC = "192.168.88.20"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add(User(id=1, username="boss", password_hash=hash_password(ADMIN_PASSWORD), role="admin"))
    session.commit()
    server.app.dependency_overrides[get_db] = lambda: session
    yield session
    server.app.dependency_overrides.clear()
    session.close()


def _pc(address: str = LAN_PC) -> TestClient:
    return TestClient(server.app, client=PROXY, headers={"X-Forwarded-For": address})


def _admin() -> TestClient:
    client = _pc("192.168.88.2")
    assert client.post("/api/auth/login", json={"username": "boss", "password": ADMIN_PASSWORD}).status_code == 200
    return client


def _sign_up(client: TestClient, username: str = "sara", email: str | None = None, **extra):
    body = {"username": username, "email": email or f"{username}@example.com", "password": PASSWORD, **extra}
    return client.post("/api/auth/signup", json=body)


def _login(client: TestClient, username: str = "sara"):
    return client.post("/api/auth/login", json={"username": username, "password": PASSWORD})


def _user(db: Session, username: str = "sara") -> User:
    db.expire_all()
    return db.scalar(select(User).where(User.username == username))


# ── open sign-up and approval ─────────────────────────────────────────────────────────────────────────

def test_a_sign_up_waits_until_an_admin_approves_it(db):
    client = _pc()
    created = _sign_up(client, phone="")
    assert created.status_code == 201 and created.json()["status"] == USER_PENDING
    assert _user(db).phone is None                                   # phone is optional
    waiting = _login(client)
    assert waiting.status_code == 403 and "approval" in waiting.json()["detail"]
    admin = _admin()
    listed = {u["username"]: u for u in admin.get("/api/auth/users").json()}
    assert listed["sara"]["status"] == USER_PENDING and listed["sara"]["signup_address"] == LAN_PC
    sara_id = listed["sara"]["id"]
    assert admin.patch(f"/api/auth/users/{sara_id}", json={"status": "active"}).status_code == 200
    assert _login(client).status_code == 200


def test_rejecting_a_sign_up_deletes_it(db):
    _sign_up(_pc())
    admin = _admin()
    rejected = admin.delete(f"/api/auth/users/{_user(db).id}")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert _user(db) is None
    assert _sign_up(_pc()).status_code == 201                        # the name is free again


def test_the_account_rules_apply(db):
    client = _pc()
    assert _sign_up(client, "ab").status_code == 422                 # username too short
    assert client.post("/api/auth/signup", json={"username": "sara", "email": "sara@example.com",
                                                 "password": "short"}).status_code == 422
    assert _sign_up(client, email="not-an-email").status_code == 422
    assert _sign_up(client, email=" ").status_code == 422            # an email is needed
    assert _sign_up(client).status_code == 201
    assert _sign_up(client).status_code == 409                       # username taken
    assert _sign_up(client, "sara2", email="SARA@example.com").status_code == 409   # email in use


def test_a_sign_up_is_always_a_member(db):
    assert _sign_up(_pc(), role="admin").status_code == 201
    assert _user(db).role == "member"


def test_an_admin_can_switch_open_sign_up_off(db):
    admin = _admin()
    assert _pc().get("/api/auth/signup").json()["open"] is True
    assert admin.put("/api/auth/users/signup-settings", json={"open": False}).json() == {"open": False}
    assert _pc().get("/api/auth/signup").json()["open"] is False
    closed = _sign_up(_pc())
    assert closed.status_code == 403 and "invite" in closed.json()["detail"]


def test_only_an_admin_manages_sign_up(db):
    member = _pc()
    _sign_up(member)
    db.get(User, _user(db).id).status = "active"
    db.commit()
    assert _login(member).status_code == 200
    assert member.put("/api/auth/users/signup-settings", json={"open": False}).status_code == 403
    assert member.post("/api/auth/users/invites", json={}).status_code == 403
    assert member.get("/api/auth/users/invites").status_code == 403


def test_one_address_may_sign_up_only_so_often(db):
    client = _pc()
    for n in range(SIGNUPS_PER_ADDRESS):
        assert _sign_up(client, f"user{n}").status_code == 201
    refused = _sign_up(client, "one-more")
    assert refused.status_code == 429
    assert _sign_up(_pc("192.168.88.21"), "elsewhere").status_code == 201
    for user in db.scalars(select(User).where(User.signup_address == LAN_PC)):
        user.created_at -= SIGNUP_WINDOW
    db.commit()
    assert _sign_up(client, "next-day").status_code == 201


# ── invite links ──────────────────────────────────────────────────────────────────────────────────────

def _invite(admin: TestClient, **body) -> dict:
    response = admin.post("/api/auth/users/invites", json=body)
    assert response.status_code == 201
    return response.json()


def test_an_invite_works_when_open_sign_up_is_off_and_still_waits_for_approval(db):
    admin = _admin()
    admin.put("/api/auth/users/signup-settings", json={"open": False})
    invite = _invite(admin, modules=["devices"])
    info = _pc().get("/api/auth/signup", params={"invite": invite["token"]}).json()
    assert info["invite_valid"] is True
    assert _sign_up(_pc(), invite=invite["token"]).status_code == 201
    sara = _user(db)
    assert sara.status == USER_PENDING
    listed = {u["username"]: u for u in admin.get("/api/auth/users").json()}
    assert listed["sara"]["invited_by"] == "boss" and listed["sara"]["modules"] == ["devices"]


def test_an_invite_works_once(db):
    token = _invite(_admin())["token"]
    assert _sign_up(_pc(), invite=token).status_code == 201
    again = _sign_up(_pc(), "other", invite=token)
    assert again.status_code == 410
    assert _pc().get("/api/auth/signup", params={"invite": token}).json()["invite_valid"] is False


def test_an_invite_runs_out(db):
    token = _invite(_admin())["token"]
    for invite in db.scalars(select(UserInvite)):
        invite.expires_at -= INVITE_LIFETIME
    db.commit()
    assert _sign_up(_pc(), invite=token).status_code == 410


def test_an_invite_for_an_email_needs_that_email(db):
    token = _invite(_admin(), email="sara@example.com")["token"]
    info = _pc().get("/api/auth/signup", params={"invite": token}).json()
    assert info["invite"]["email"] == "sara@example.com"
    assert _sign_up(_pc(), email="someone@example.com", invite=token).status_code == 422
    assert _sign_up(_pc(), email="Sara@Example.com", invite=token).status_code == 201


def test_a_revoked_invite_no_longer_works(db):
    admin = _admin()
    invite = _invite(admin)
    assert [i["id"] for i in admin.get("/api/auth/users/invites").json()] == [invite["id"]]
    assert "token" not in admin.get("/api/auth/users/invites").json()[0]   # the link is shown only once
    assert admin.delete(f"/api/auth/users/invites/{invite['id']}").status_code == 200
    assert _sign_up(_pc(), invite=invite["token"]).status_code == 410


def test_the_invite_list_leaves_out_used_and_expired_ones(db):
    admin = _admin()
    used = _invite(admin)["token"]
    _sign_up(_pc(), invite=used)
    expired = _invite(admin)
    db.get(UserInvite, expired["id"]).expires_at -= INVITE_LIFETIME + timedelta(seconds=1)
    db.commit()
    kept = _invite(admin)
    assert [i["id"] for i in admin.get("/api/auth/users/invites").json()] == [kept["id"]]
