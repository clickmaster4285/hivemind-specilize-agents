"""O121: a long-lived stream must not keep a database connection. The sign-in is checked in a short session of its
own (auth.stream_user) and the job stream reads in short sessions in a worker thread (web/routers/jobs.py)."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core import auth, db as core_db
from src.core.db import Base
from src.core.models_db import Job, JobEvent, User
from web.routers import jobs as jobs_router

JOB = "5f0c6a1e-0000-4000-8000-000000000001"


@pytest.fixture
def store(monkeypatch):
    """An in-memory database behind session_scope that counts the sessions still open."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    open_now = {"n": 0}

    @contextmanager
    def scope(config=None):
        session = Session(engine)
        open_now["n"] += 1
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            open_now["n"] -= 1

    monkeypatch.setattr(core_db, "session_scope", scope)
    with Session(engine) as s:
        s.add_all([User(id=1, username="ana", password_hash="x", role="member", status="active"),
                   User(id=2, username="bo", password_hash="x", role="member", status="active")])
        s.flush()
        s.add(Job(id=JOB, user_id=1, mode="chat", status="running", query="q"))
        s.flush()
        s.add(JobEvent(job_id=JOB, seq=1, event_json=json.dumps({"type": "status", "message": "Thinking"})))
        s.commit()
    monkeypatch.setattr(auth, "get_current_user", lambda request, db: db.get(User, 1))
    return engine, open_now


def test_the_stream_user_is_checked_in_a_session_that_closes(store):
    _engine, open_now = store
    user = auth.stream_user(request=None)
    assert (user.id, user.username, user.role) == (1, "ana", "member")
    assert open_now["n"] == 0


def test_the_job_stream_holds_no_session_between_reads(store, monkeypatch):
    engine, open_now = store
    monkeypatch.setattr("src.jobs.job_events.wait_job", lambda job_id, timeout=0.35: False)

    async def run():
        user = auth.stream_user(request=None)
        response = await jobs_router.job_events_stream(JOB, user)
        body = response.body_iterator
        first = await anext(body)
        between = open_now["n"]
        with Session(engine) as s:                     # the job finishes: the stream drains and ends
            s.get(Job, JOB).status = "succeeded"
            s.commit()
        rest = [chunk async for chunk in body]
        return first, between, rest

    first, between, rest = asyncio.run(run())
    assert json.loads(first.removeprefix("data: ")) == {"type": "status", "message": "Thinking"}
    assert between == 0 and open_now["n"] == 0
    assert all("fatal" not in chunk for chunk in rest)


def test_someone_elses_job_stream_is_refused_before_it_starts(store):
    async def run():
        other = User(id=2, username="bo", password_hash="x", role="member")
        await jobs_router.job_events_stream(JOB, other)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 403


def test_the_postgres_pool_is_sized_for_a_team():
    assert core_db.DB_POOL_SIZE + core_db.DB_POOL_OVERFLOW >= 40 and core_db.DB_POOL_TIMEOUT <= 10
