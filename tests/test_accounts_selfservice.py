"""Accounts for a team of 10+ (BACKLOG L30 T2): my own password, a first password replaced before use, password /
role / status changes ending sign-ins, the 30-day cap, sign out everywhere, reset links, bulk changes, and every
admin change in the security log."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.core import auth
from src.core.auth import ACCESS_COOKIE, SESSION_MAX_AGE, hash_password, start_session
from src.core.db import Base
from src.core.models_db import RefreshToken, SecurityEvent, User, utcnow
from web.routers import auth as auth_routes
from web.routers.users import create_user, reset_link, update_user, update_users
from web.schemas import (PasswordChangePayload, PasswordResetPayload, UserBulkPayload, UserCreatePayload,
                         UserUpdatePayload)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(bind=engine) as s:
        yield s


@pytest.fixture
def admin(db) -> User:
    a = User(username="root", password_hash=hash_password("adminpass1"), role="admin", status="active")
    db.add(a)
    db.commit()
    return a


def _member(db, name="mia", password="firstpass1") -> User:
    u = User(username=name, password_hash=hash_password(password), role="member", status="active")
    db.add(u)
    db.commit()
    return u


def _request(path="/api/auth/me", access: str | None = None) -> Request:
    headers = [(b"cookie", f"{ACCESS_COOKIE}={access}".encode())] if access else []
    return Request({"type": "http", "method": "GET", "path": path, "headers": headers, "query_string": b"",
                    "scheme": "http", "server": ("testserver", 80), "client": ("127.0.0.1", 1)})


def _live_sessions(db, user: User) -> set[str]:
    return set(db.scalars(select(RefreshToken.family).where(RefreshToken.user_id == user.id,
                                                            RefreshToken.revoked_at.is_(None))))


def _logged(db, kind: str) -> list[str]:
    return [e.detail for e in db.scalars(select(SecurityEvent).where(SecurityEvent.kind == kind))]


# ── a first password chosen by an admin ─────────────────────────────────────

def test_a_new_account_must_choose_its_own_password_before_using_the_app(db, admin):
    created = create_user(UserCreatePayload(username="nia", password="givenpass1"), _admin=admin, db=db)
    nia = db.get(User, created["id"])
    access, _refresh = start_session(db, nia)
    with pytest.raises(HTTPException) as blocked:
        auth.get_current_user(_request("/api/jobs", access), db)
    assert blocked.value.status_code == 403 and blocked.value.headers == {"X-Password-Change": "required"}
    assert auth.get_current_user(_request("/api/auth/password", access), db).id == nia.id     # the way out works
    out = auth_routes.change_password(PasswordChangePayload(current="givenpass1", new="mychoice99"),
                                      _request("/api/auth/password", access), user=nia, db=db)
    assert out["user"]["must_change_password"] is False
    assert auth.get_current_user(_request("/api/jobs", access), db).id == nia.id
    assert _logged(db, "account_created") == ["by root: member"]


# ── my own password ─────────────────────────────────────────────────────────

def test_changing_my_password_needs_the_current_one_and_ends_my_other_sign_ins(db):
    mia = _member(db)
    here, _ = start_session(db, mia)
    start_session(db, mia)                                   # the laptop at home
    with pytest.raises(HTTPException) as wrong:
        auth_routes.change_password(PasswordChangePayload(current="nope", new="newpass123"), _request(access=here),
                                    user=mia, db=db)
    assert wrong.value.status_code == 400
    with pytest.raises(HTTPException) as same:
        auth_routes.change_password(PasswordChangePayload(current="firstpass1", new="firstpass1"),
                                    _request(access=here), user=mia, db=db)
    assert same.value.status_code == 400
    out = auth_routes.change_password(PasswordChangePayload(current="firstpass1", new="newpass123"),
                                      _request(access=here), user=mia, db=db)
    assert out["sessions_ended"] == 1
    assert _live_sessions(db, mia) == {auth.decode_token(here)["sid"]}     # this one stays
    assert auth.verify_password("newpass123", mia.password_hash)
    assert "1 other sign-in ended" in _logged(db, "password_changed")[0]


def test_sign_out_everywhere_ends_every_sign_in(db):
    mia = _member(db)
    start_session(db, mia)
    start_session(db, mia)
    assert auth_routes.logout_everywhere(Response(), user=mia, db=db)["sessions_ended"] == 2
    assert _live_sessions(db, mia) == set()


def test_a_sign_in_lasts_at_most_thirty_days_however_often_refreshed(db):
    mia = _member(db)
    _access, refresh = start_session(db, mia)
    row = db.scalar(select(RefreshToken))
    row.session_started_at = utcnow() - SESSION_MAX_AGE + timedelta(hours=1)    # began 29 days 23 h ago
    db.commit()
    _user, _access, refresh = auth.refresh_session(db, refresh)
    newest = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == auth.token_digest(refresh)))
    assert auth.as_utc(newest.expires_at) <= auth.as_utc(row.session_started_at) + SESSION_MAX_AGE


# ── an admin's changes ──────────────────────────────────────────────────────

def test_a_password_reset_by_an_admin_ends_the_sign_ins_and_asks_for_a_new_one(db, admin):
    mia = _member(db)
    start_session(db, mia)
    update_user(mia.id, UserUpdatePayload(password="tempPass99"), _request(), admin=admin, db=db)
    assert _live_sessions(db, mia) == set() and mia.must_change_password
    assert _logged(db, "account_changed") == ["by root: password reset; 1 sign-in ended"]


def test_a_role_change_ends_sign_ins_but_a_team_change_does_not(db, admin):
    mia = _member(db)
    start_session(db, mia)
    update_user(mia.id, UserUpdatePayload(team="IT"), _request(), admin=admin, db=db)
    assert len(_live_sessions(db, mia)) == 1
    update_user(mia.id, UserUpdatePayload(role="admin"), _request(), admin=admin, db=db)
    assert _live_sessions(db, mia) == set()
    assert _logged(db, "account_changed") == ["by root: team — → IT", "by root: role member → admin; 1 sign-in ended"]


def test_nothing_changed_writes_nothing_to_the_log(db, admin):
    mia = _member(db)
    update_user(mia.id, UserUpdatePayload(role="member"), _request(), admin=admin, db=db)
    assert _logged(db, "account_changed") == []


# ── bulk ────────────────────────────────────────────────────────────────────

def test_one_bulk_change_for_several_accounts(db, admin):
    a, b = _member(db, "ana"), _member(db, "bo")
    out = update_users(UserBulkPayload(ids=[a.id, b.id], changes={"team": "marketing", "daily_job_quota": 80}),
                       _request(), admin=admin, db=db)
    assert out == {"changed": 2, "selected": 2}
    assert {(u.team, u.daily_job_quota) for u in (a, b)} == {("marketing", 80)}
    assert len(_logged(db, "account_changed")) == 2


def test_a_bulk_change_that_would_lock_admin_out_changes_nobody(db, admin):
    ana = _member(db, "ana")
    with pytest.raises(HTTPException) as exc:
        update_users(UserBulkPayload(ids=[ana.id, admin.id], changes={"status": "disabled"}), _request(),
                     admin=admin, db=db)
    assert exc.value.status_code == 400
    db.rollback()
    assert db.get(User, ana.id).status == "active"          # all or none


def test_a_password_or_an_email_is_never_set_in_bulk():
    with pytest.raises(ValidationError):
        UserBulkPayload(ids=[1], changes={"password": "samepass123"})
    with pytest.raises(ValidationError):
        UserBulkPayload(ids=[], changes={"team": "x"})


def test_bulk_refuses_accounts_that_are_gone(db, admin):
    with pytest.raises(HTTPException) as exc:
        update_users(UserBulkPayload(ids=[999], changes={"team": "x"}), _request(), admin=admin, db=db)
    assert exc.value.status_code == 404


# ── reset links ─────────────────────────────────────────────────────────────

def test_a_reset_link_sets_a_new_password_once_and_ends_every_sign_in(db, admin):
    mia = _member(db)
    start_session(db, mia)
    token = reset_link(mia.id, admin=admin, db=db)["token"]
    assert auth_routes.reset_info(token, db=db) == {"username": "mia"}
    out = auth_routes.reset_password(PasswordResetPayload(token=token, password="resetpass9"), _request(), db=db)
    assert out["username"] == "mia" and auth.verify_password("resetpass9", mia.password_hash)
    assert _live_sessions(db, mia) == set() and not mia.must_change_password
    with pytest.raises(HTTPException) as again:
        auth_routes.reset_password(PasswordResetPayload(token=token, password="another99"), _request(), db=db)
    assert again.value.status_code == 404
    assert _logged(db, "reset_link") == ["by root, valid 24 h"]
    assert "with a reset link" in _logged(db, "password_changed")[0]


def test_a_new_link_cancels_the_old_one_and_a_weak_password_is_refused(db, admin):
    mia = _member(db)
    old = reset_link(mia.id, admin=admin, db=db)["token"]
    new = reset_link(mia.id, admin=admin, db=db)["token"]
    with pytest.raises(HTTPException):
        auth_routes.reset_info(old, db=db)
    with pytest.raises(HTTPException) as weak:
        auth_routes.reset_password(PasswordResetPayload(token=new, password="short"), _request(), db=db)
    assert weak.value.status_code == 422                                   # the account rule, as everywhere
    assert auth_routes.reset_info(new, db=db) == {"username": "mia"}       # a refused try does not spend it


def test_an_expired_link_does_not_work(db, admin):
    from src.core.models_db import PasswordReset

    mia = _member(db)
    token = reset_link(mia.id, admin=admin, db=db)["token"]
    db.scalar(select(PasswordReset)).expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(HTTPException):
        auth_routes.reset_info(token, db=db)


# ── my sessions on other devices ────────────────────────────────────────────

@pytest.mark.parametrize("ua, name", [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36", "Chrome on Windows"),
    ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 Edg/131.0", "Edge on Windows"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1",
     "Safari on iPhone"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0", "Firefox on Linux"),
    ("curl/8.5.0", "Unknown device"),
    (None, "Unknown device"),
])
def test_a_session_is_named_by_its_browser_and_system(ua, name):
    assert auth.device_name(ua) == name


def test_my_sessions_list_each_device_once_and_one_can_be_ended(db):
    mia = _member(db)
    here, _ = start_session(db, mia, device="Chrome on Windows", address="192.168.88.20")
    _other_access, other_refresh = start_session(db, mia, device="Safari on iPhone", address="192.168.88.31")
    auth.refresh_session(db, other_refresh)                          # renewed: still one session, same device
    listed = auth_routes.my_sessions(_request(access=here), user=mia, db=db)
    assert len(listed) == 2
    assert {(s["device"], s["address"], s["current"]) for s in listed} == {
        ("Chrome on Windows", "192.168.88.20", True), ("Safari on iPhone", "192.168.88.31", False)}
    phone = next(s for s in listed if not s["current"])
    assert auth_routes.end_my_session(phone["id"], user=mia, db=db) == {"ok": True}
    assert [s["device"] for s in auth_routes.my_sessions(_request(access=here), user=mia, db=db)] == ["Chrome on Windows"]


def test_someone_elses_session_cannot_be_ended(db):
    mia, bo = _member(db, "mia"), _member(db, "bo")
    start_session(db, bo, device="Firefox on Linux")
    bos = auth.list_sessions(db, bo.id)[0]["id"]
    with pytest.raises(HTTPException) as exc:
        auth_routes.end_my_session(bos, user=mia, db=db)
    assert exc.value.status_code == 404 and len(auth.list_sessions(db, bo.id)) == 1
