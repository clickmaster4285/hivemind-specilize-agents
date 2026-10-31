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

from src.core.auth import hash_password, require_admin, verify_password
from src.core.db import Base
from src.core.models_db import Job, User  # noqa: F401 — registers the tables on Base
from web.routers.auth import login
from web.routers.users import create_user, deactivate_user, list_users, update_user
from web.schemas import LoginPayload, UserCreatePayload, UserUpdatePayload


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


@pytest.fixture
def admin_row(session) -> User:
    """A real admin row in the DB — needed by edit/deactivate, which refuse to leave zero active admins."""
    u = User(username="root", password_hash=hash_password("adminpass1"), role="admin", status="active")
    session.add(u)
    session.flush()
    return u


# --- create_user / list_users endpoints -------------------------------------------------------

def test_admin_creates_member_user_with_team(session, admin):
    out = create_user(
        UserCreatePayload(
            username="alice", password="hunter2!!", team="IT",
            email="alice@example.com", phone="+92 300 1234567",
        ),
        _admin=admin,
        db=session,
    )
    assert out["username"] == "alice"
    assert out["role"] == "member"
    assert out["team"] == "IT"
    assert out["email"] == "alice@example.com"
    assert out["phone"] == "+92 300 1234567"
    assert out["created_at"]
    assert "password_hash" not in out  # the hash must never leave the server

    row = session.scalar(select(User).where(User.username == "alice"))
    assert verify_password("hunter2!!", row.password_hash)  # stored hashed, and it verifies


def test_duplicate_email_is_rejected(session, admin):
    create_user(
        UserCreatePayload(username="jade", password="longenough", email="dup@example.com"),
        _admin=admin, db=session,
    )
    with pytest.raises(HTTPException) as exc:  # case-insensitive clash
        create_user(
            UserCreatePayload(username="kyle", password="longenough", email="DUP@example.com"),
            _admin=admin, db=session,
        )
    assert exc.value.status_code == 409


def test_bad_email_is_rejected(session, admin):
    with pytest.raises(HTTPException) as exc:
        create_user(
            UserCreatePayload(username="heidi", password="longenough", email="not-an-email"),
            _admin=admin, db=session,
        )
    assert exc.value.status_code == 422


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


# --- edit / deactivate / status / analytics ---------------------------------------------------

def test_update_changes_fields(session, admin_row):
    created = create_user(
        UserCreatePayload(username="ned", password="longenough", team="IT"), _admin=admin_row, db=session
    )
    out = update_user(
        created["id"],
        UserUpdatePayload(team="marketing", email="ned@example.com", phone="999", daily_job_quota=7, status="active"),
        admin=admin_row, db=session,
    )
    assert out["team"] == "marketing"
    assert out["email"] == "ned@example.com"
    assert out["phone"] == "999"
    assert out["daily_job_quota"] == 7
    assert out["updated_at"] is not None


def test_update_rejects_duplicate_email(session, admin_row):
    create_user(UserCreatePayload(username="olive", password="longenough", email="o@example.com"), _admin=admin_row, db=session)
    peter = create_user(UserCreatePayload(username="peter", password="longenough"), _admin=admin_row, db=session)
    with pytest.raises(HTTPException) as exc:  # case-insensitive, excludes the user's own row
        update_user(peter["id"], UserUpdatePayload(email="O@example.com"), admin=admin_row, db=session)
    assert exc.value.status_code == 409


def test_password_reset_changes_login(session, admin_row):
    quinn = create_user(UserCreatePayload(username="quinn", password="origpass1"), _admin=admin_row, db=session)
    update_user(quinn["id"], UserUpdatePayload(password="newpass12"), admin=admin_row, db=session)
    assert login(LoginPayload(username="quinn", password="newpass12"), db=session)["user"]["username"] == "quinn"
    with pytest.raises(HTTPException):
        login(LoginPayload(username="quinn", password="origpass1"), db=session)


def test_deactivate_blocks_login_but_keeps_account(session, admin_row):
    rita = create_user(UserCreatePayload(username="rita", password="longenough"), _admin=admin_row, db=session)
    out = deactivate_user(rita["id"], admin=admin_row, db=session)
    assert out["status"] == "disabled"
    with pytest.raises(HTTPException) as exc:
        login(LoginPayload(username="rita", password="longenough"), db=session)
    assert exc.value.status_code == 403
    # re-enabling via update lets them back in
    update_user(rita["id"], UserUpdatePayload(status="active"), admin=admin_row, db=session)
    assert login(LoginPayload(username="rita", password="longenough"), db=session)["access_token"]


def test_admin_cannot_lock_themselves_out(session, admin_row):
    with pytest.raises(HTTPException) as deact:
        deactivate_user(admin_row.id, admin=admin_row, db=session)
    assert deact.value.status_code == 400
    with pytest.raises(HTTPException) as demote:
        update_user(admin_row.id, UserUpdatePayload(role="member"), admin=admin_row, db=session)
    assert demote.value.status_code == 400


def test_list_reports_query_count(session, admin_row):
    sam = create_user(UserCreatePayload(username="sam", password="longenough"), _admin=admin_row, db=session)
    session.add(Job(id="j1", user_id=sam["id"], mode="chat", status="succeeded", query="how to configure dns"))
    session.add(Job(id="j2", user_id=sam["id"], mode="chat", status="succeeded", query="second question"))
    session.flush()
    rows = list_users(_admin=admin_row, db=session)
    assert next(r for r in rows if r["username"] == "sam")["query_count"] == 2


def test_login_stamps_last_login_at(session, admin_row):
    create_user(UserCreatePayload(username="tom", password="longenough"), _admin=admin_row, db=session)
    assert login(LoginPayload(username="tom", password="longenough"), db=session)["access_token"]
    assert session.scalar(select(User).where(User.username == "tom")).last_login_at is not None


# --- login by username or email ---------------------------------------------------------------

def test_login_accepts_username_or_email(session, admin):
    create_user(
        UserCreatePayload(username="ivy", password="longenough", email="Ivy@Example.com"),
        _admin=admin, db=session,
    )
    by_name = login(LoginPayload(username="ivy", password="longenough"), db=session)
    assert by_name["user"]["username"] == "ivy"
    # email is accepted too, case-insensitively
    by_email = login(LoginPayload(username="ivy@example.com", password="longenough"), db=session)
    assert by_email["user"]["username"] == "ivy"
    assert by_email["access_token"]
    # a wrong password is still refused
    with pytest.raises(HTTPException) as exc:
        login(LoginPayload(username="ivy", password="wrongpass!"), db=session)
    assert exc.value.status_code == 401


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

    action, uid = seed_user(
        username="ops1", password="longenough", team="IT", email="ops1@example.com", phone="123456"
    )
    assert action == "created"
    row = seed_session.get(User, uid)
    assert row.team == "IT" and verify_password("longenough", row.password_hash)
    assert row.email == "ops1@example.com" and row.phone == "123456"

    # re-run with no password: keeps the hash, updates the team
    first_hash = row.password_hash
    action, uid2 = seed_user(username="ops1", password=None, team="helpdesk")
    assert action == "updated" and uid2 == uid
    assert seed_session.get(User, uid).team == "helpdesk"
    assert seed_session.get(User, uid).password_hash == first_hash


def test_seed_rejects_duplicate_email_across_accounts(seed_session):
    from src.core.seed_users import seed_user

    seed_user(username="ops9", password="longenough", email="shared@example.com")
    # a second account cannot take the same email (case-insensitive)
    with pytest.raises(ValueError, match="already in use"):
        seed_user(username="ops10", password="longenough", email="SHARED@example.com")
    # but the owner may re-run with their own email (idempotent update)
    action, _ = seed_user(username="ops9", password=None, email="shared@example.com", team="IT")
    assert action == "updated"


def test_seed_new_account_requires_a_password(seed_session):
    from src.core.seed_users import seed_user

    with pytest.raises(ValueError, match="password is required"):
        seed_user(username="ops2", password=None)


def test_seed_rejects_short_password(seed_session):
    from src.core.seed_users import seed_user

    with pytest.raises(ValueError, match="at least 8"):
        seed_user(username="ops3", password="short")
