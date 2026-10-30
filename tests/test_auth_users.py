"""Admin-provisioned user accounts: the create/list endpoints, the admin gate, and the seed CLI.

Open self-serve registration stays disabled; admins create accounts (web.server.create_user) or the
seed command does (src.core.seed_users.seed_user). Once an account exists, that user's chat queries
land in the jobs table under their own user_id — which is the point of giving each person a login.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.core.auth import require_admin, verify_password
from src.core.db import Base
from src.core.models_db import User  # noqa: F401 — registers the users table on Base
from web.server import UserCreatePayload, create_user, list_users


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def admin() -> User:
    """A stand-in admin for the require_admin dependency (not persisted; the route never reads it)."""
    return User(username="admin", password_hash="x", role="admin")


# --- create_user / list_users endpoints -------------------------------------------------------

def test_admin_creates_member_user_with_team(session, admin):
    out = create_user(
        UserCreatePayload(username="alice", password="hunter2!!", team="IT"),
        _admin=admin,
        db=session,
    )
    assert out["username"] == "alice"
    assert out["role"] == "member"
    assert out["team"] == "IT"
    assert out["created_at"]
    assert "password_hash" not in out  # the hash must never leave the server

    row = session.scalar(select(User).where(User.username == "alice"))
    assert verify_password("hunter2!!", row.password_hash)  # stored hashed, and it verifies


def test_duplicate_username_is_rejected(session, admin):
    create_user(UserCreatePayload(username="bob", password="longenough"), _admin=admin, db=session)
    with pytest.raises(HTTPException) as exc:
        create_user(UserCreatePayload(username="bob", password="longenough2"), _admin=admin, db=session)
    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "username, password, code",
    [
        ("ab", "longenough", 422),       # username too short
        ("carol", "short", 422),          # password under 8 chars
    ],
)
def test_bad_input_is_rejected(session, admin, username, password, code):
    with pytest.raises(HTTPException) as exc:
        create_user(UserCreatePayload(username=username, password=password), _admin=admin, db=session)
    assert exc.value.status_code == code


def test_team_is_trimmed_and_optional(session, admin):
    tagged = create_user(
        UserCreatePayload(username="dave", password="longenough", team="  IT  "),
        _admin=admin, db=session,
    )
    assert tagged["team"] == "IT"
    untagged = create_user(
        UserCreatePayload(username="erin", password="longenough", team="   "),
        _admin=admin, db=session,
    )
    assert untagged["team"] is None


def test_list_users_returns_accounts_without_hashes(session, admin):
    create_user(UserCreatePayload(username="finn", password="longenough", team="IT"), _admin=admin, db=session)
    create_user(UserCreatePayload(username="gail", password="longenough", role="admin"), _admin=admin, db=session)
    rows = list_users(_admin=admin, db=session)
    assert {r["username"] for r in rows} == {"finn", "gail"}
    assert all("password_hash" not in r for r in rows)
    assert {r["username"]: r["team"] for r in rows} == {"finn": "IT", "gail": None}


# --- the admin gate ---------------------------------------------------------------------------

def test_require_admin_allows_admin_and_blocks_member():
    admin = User(username="a", password_hash="x", role="admin")
    member = User(username="m", password_hash="x", role="member")
    assert require_admin(user=admin) is admin
    with pytest.raises(HTTPException) as exc:
        require_admin(user=member)
    assert exc.value.status_code == 403


# --- seed_users CLI ---------------------------------------------------------------------------

@pytest.fixture
def seed_session(monkeypatch):
    """Point seed_user at an in-memory DB (no global engine, no real database)."""
    from src.core import seed_users

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)

    @contextmanager
    def fake_scope(config=None):
        yield s

    monkeypatch.setattr(seed_users, "init_db", lambda config=None: None)
    monkeypatch.setattr(seed_users, "session_scope", fake_scope)
    try:
        yield s
    finally:
        s.close()


def test_seed_creates_then_updates_same_account(seed_session):
    from src.core.seed_users import seed_user

    action, uid = seed_user(username="ops1", password="longenough", team="IT")
    assert action == "created"
    row = seed_session.get(User, uid)
    assert row.team == "IT" and verify_password("longenough", row.password_hash)

    # re-run with no password: keeps the hash, updates the team
    first_hash = row.password_hash
    action, uid2 = seed_user(username="ops1", password=None, team="helpdesk")
    assert action == "updated" and uid2 == uid
    assert seed_session.get(User, uid).team == "helpdesk"
    assert seed_session.get(User, uid).password_hash == first_hash


def test_seed_new_account_requires_a_password(seed_session):
    from src.core.seed_users import seed_user

    with pytest.raises(ValueError, match="password is required"):
        seed_user(username="ops2", password=None)


def test_seed_rejects_short_password(seed_session):
    from src.core.seed_users import seed_user

    with pytest.raises(ValueError, match="at least 8"):
        seed_user(username="ops3", password="short")
