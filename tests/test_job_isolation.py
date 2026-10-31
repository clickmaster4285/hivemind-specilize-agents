"""Chat isolation: a signed-in user sees and touches only their own jobs (chat turns).

Every /api/jobs endpoint scopes by the caller's user_id (admins excepted). These tests call the
endpoint functions directly with a real session and two member users, proving one user can neither
list, read, replay, nor delete another user's chats — "the users should only see his chats only".
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.auth import hash_password
from src.core.db import Base
from src.core.models_db import Job, JobEvent, User  # noqa: F401 — registers the tables on Base
from web.routers.jobs import (
    get_job_endpoint,
    job_events_history,
    list_jobs,
    soft_delete_job_endpoint,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.close()


def _user(session: Session, username: str, role: str = "member") -> User:
    u = User(username=username, password_hash=hash_password("passphrase1"), role=role, status="active")
    session.add(u)
    session.flush()
    return u


def _job(session: Session, user: User, jid: str, query: str) -> Job:
    job = Job(id=jid, user_id=user.id, mode="chat", status="succeeded", query=query, payload_json="{}")
    session.add(job)
    session.flush()
    session.add(JobEvent(job_id=jid, seq=1, event_json=json.dumps({"type": "agent_done", "output": query})))
    session.flush()
    return job


@pytest.fixture
def two_users(session: Session) -> tuple[User, User]:
    alice, bob = _user(session, "alice"), _user(session, "bob")
    _job(session, alice, "a1", "alice secret")
    _job(session, bob, "b1", "bob secret")
    return alice, bob


def test_list_jobs_returns_only_the_callers_jobs(session, two_users):
    alice, bob = two_users
    assert {j["id"] for j in list_jobs(db=session, user=alice)} == {"a1"}
    assert {j["id"] for j in list_jobs(db=session, user=bob)} == {"b1"}


def test_reading_another_users_job_is_forbidden(session, two_users):
    alice, _bob = two_users
    with pytest.raises(HTTPException) as exc:
        get_job_endpoint("b1", db=session, user=alice)
    assert exc.value.status_code == 403
    assert get_job_endpoint("a1", db=session, user=alice)["id"] == "a1"   # own job is fine


def test_replaying_another_users_events_is_forbidden(session, two_users):
    alice, _bob = two_users
    with pytest.raises(HTTPException) as exc:
        job_events_history("b1", db=session, user=alice)
    assert exc.value.status_code == 403


def test_deleting_another_users_job_is_forbidden_and_leaves_it(session, two_users):
    alice, _bob = two_users
    with pytest.raises(HTTPException) as exc:
        soft_delete_job_endpoint("b1", db=session, user=alice)
    assert exc.value.status_code == 403
    assert session.get(Job, "b1").deleted_at is None                      # untouched


def test_admin_sees_every_users_jobs(session, two_users):
    admin = _user(session, "root", role="admin")
    assert {"a1", "b1"} <= {j["id"] for j in list_jobs(db=session, user=admin)}
