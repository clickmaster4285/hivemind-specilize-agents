"""Settings → Users → Delete (src/core/account_delete.py, web/routers/users.delete_user): the person can never sign
in again and their name goes; their chats stay, anonymised (user's choice 2026-11-20). Deactivate stays the
undoable one (test_auth_users.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from src.core.auth import hash_password
from src.core.config import Config
from src.core.db import Base
from src.core.models_db import (
    ChatThreadRow,
    CommandLog,
    DeviceRow,
    Job,
    JobEvent,
    LlmUsage,
    ProjectRow,
    RefreshToken,
    ScheduleRow,
    SecurityEvent,
    SkillRow,
    User,
    UserInvite,
)
from web.routers.users import delete_user
from web.schemas import UserDeletePayload

JOB = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
LATER = datetime.now(timezone.utc) + timedelta(days=1)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(conn, _record):        # as strict as Postgres: a row left pointing at the user fails
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def config(monkeypatch, tmp_path):
    cfg = Config.from_env()
    cfg = type(cfg)(**{**cfg.__dict__, "artifacts_dir": tmp_path, "brain_enabled": False})
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: cfg))
    return cfg


@pytest.fixture
def people(session):
    admin = User(username="root", password_hash=hash_password("adminpass1"), role="admin", status="active")
    rita = User(username="rita", password_hash=hash_password("longenough"), role="member", status="active")
    session.add_all([admin, rita])
    session.flush()
    return admin, rita


def _fill(session, rita: User, tmp_path) -> None:
    """One of everything a user can own, plus things that only point at them."""
    session.add(Job(id=JOB, user_id=rita.id, mode="chat", status="done", query="q"))
    session.flush()
    session.add_all([
        JobEvent(job_id=JOB, seq=1, event_json="{}"),
        ChatThreadRow(id="t1", user_id=rita.id),
        ProjectRow(id="p1", user_id=rita.id, name="Project"),
        ScheduleRow(user_id=rita.id, thread_id="t1", prompt="check", every="day", time_zone="UTC"),
        DeviceRow(id="dev1", user_id=rita.id, label="pk", host="10.0.0.2", port=22, username="root", secret_enc="x"),
        RefreshToken(user_id=rita.id, family="f", token_hash="h" * 64, expires_at=LATER),
        UserInvite(token_hash="i" * 64, created_by=rita.id, expires_at=LATER),
        SkillRow(owner_id=rita.id, name="mine"),
        SkillRow(owner_id=None, name="shared", created_by=rita.id),
        User(username="friend", password_hash="x", role="member", status="active", invited_by=rita.id),
        LlmUsage(job_id=JOB, user_id=rita.id, step="chat_runner", model="m"),
        CommandLog(user_id=rita.id, server_label="pk server", host="10.0.0.2", command="systemctl restart nginx",
                   decision="confirmed"),
    ])
    session.flush()
    (tmp_path / JOB / "images").mkdir(parents=True)
    (tmp_path / JOB / "images" / "a.png").write_bytes(b"x")
    (tmp_path / "other").mkdir()


def _count(session, model, *where) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*where))


def test_delete_keeps_the_chats_anonymised_and_removes_what_served_the_person(session, people, config, tmp_path):
    from web.routers.users import list_users

    admin, rita = people
    _fill(session, rita, tmp_path)
    rid = rita.id
    out = delete_user(rid, UserDeletePayload(confirm="rita"), admin=admin, db=session)
    assert out["status"] == "deleted" and out["questions"] == 1 and out["kept_as"] == f"former-user-{rid}"
    assert out["knowledge_base"] == "not used"
    former = session.get(User, rid)
    assert (former.username, former.status, former.email, former.team, former.modules_json) == (
        f"former-user-{rid}", "former", None, None, "[]")
    # kept, under the anonymous account: the chat, its messages, its project, its files, the usage
    for model in (Job, JobEvent, ChatThreadRow, ProjectRow):
        assert _count(session, model) == 1, model.__name__
    assert session.get(Job, JOB).user_id == rid and (tmp_path / JOB / "images" / "a.png").exists()
    assert session.scalar(select(LlmUsage)).user_id == rid
    # gone: what only served the person
    for model in (ScheduleRow, DeviceRow, RefreshToken, UserInvite):
        assert _count(session, model) == 0, model.__name__
    assert _count(session, SkillRow, SkillRow.name == "mine") == 0
    assert session.scalar(select(SkillRow).where(SkillRow.name == "shared")).created_by == rid
    trail = session.scalar(select(CommandLog))                                # O123: the audit trail, real name
    assert trail.username == "rita" and trail.command == "systemctl restart nginx"
    logged = session.scalar(select(SecurityEvent).where(SecurityEvent.kind == "account_deleted"))
    assert logged.account == "rita" and "by root" in logged.detail
    # hidden from the Users list, never editable or deletable again, and the name is free for a new account
    assert "rita" not in {u["username"] for u in list_users(_admin=admin, db=session)}
    assert all(u["id"] != rid for u in list_users(_admin=admin, db=session))
    with pytest.raises(HTTPException) as exc:
        delete_user(rid, UserDeletePayload(confirm=f"former-user-{rid}"), admin=admin, db=session)
    assert exc.value.status_code == 404


def test_a_former_account_cannot_sign_in(session, people, config, tmp_path):
    from tests.test_auth_users import _login

    admin, rita = people
    delete_user(rita.id, UserDeletePayload(confirm="rita"), admin=admin, db=session)
    for name in ("rita", f"former-user-{rita.id}"):
        with pytest.raises(HTTPException):
            _login(session, name, "longenough")


def test_the_username_must_be_typed_exactly(session, people, config):
    admin, rita = people
    with pytest.raises(HTTPException) as exc:
        delete_user(rita.id, UserDeletePayload(confirm="Rita"), admin=admin, db=session)
    assert exc.value.status_code == 400 and session.get(User, rita.id) is not None


def test_an_admin_cannot_delete_themselves(session, people, config):
    admin, _rita = people
    with pytest.raises(HTTPException) as exc:
        delete_user(admin.id, UserDeletePayload(confirm="root"), admin=admin, db=session)
    assert exc.value.status_code == 400


def test_a_running_question_blocks_the_delete(session, people, config):
    admin, rita = people
    session.add(Job(id=JOB, user_id=rita.id, mode="chat", status="running", query="q"))
    session.flush()
    with pytest.raises(HTTPException) as exc:
        delete_user(rita.id, UserDeletePayload(confirm="rita"), admin=admin, db=session)
    assert exc.value.status_code == 409 and session.get(User, rita.id) is not None


def test_the_last_active_admin_cannot_be_deleted(session, people, config):
    admin, _rita = people
    other = User(username="boss", password_hash="x", role="admin", status="active")
    session.add(other)
    session.flush()
    admin.status = "disabled"          # `boss` is the only active admin left; `root` still asks
    session.flush()
    with pytest.raises(HTTPException) as exc:
        delete_user(other.id, UserDeletePayload(confirm="boss"), admin=admin, db=session)
    assert exc.value.status_code == 400


def test_admins_read_the_command_log_with_deleted_users_named(session, people, config, tmp_path):
    from web.routers.users import command_log

    admin, rita = people
    session.add_all([
        CommandLog(user_id=rita.id, username="rita", server_label="pk server", host="10.0.0.2",
                   command="systemctl restart nginx", decision="confirmed", exit_code=0),
        CommandLog(user_id=admin.id, username="root", server_label="uk server", host="10.0.0.3",
                   command="apt-get upgrade", decision="refused"),
    ])
    session.flush()
    delete_user(rita.id, UserDeletePayload(confirm="rita"), admin=admin, db=session)
    rows = command_log(_admin=admin, db=session)["rows"]
    assert {(r["username"], r["deleted"]) for r in rows} == {("rita", True), ("root", False)}
    found = command_log(search="nginx", _admin=admin, db=session)["rows"]
    assert [r["server"] for r in found] == ["pk server"]
