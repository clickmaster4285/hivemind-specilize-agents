"""Orphaned-job reclaim: a job left 'running' by a restart/crash must be failed on startup so it stops
counting as active and blocking the user's per-user active-job limit (the 409 "already has N active jobs")."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.db import Base
from src.core.models_db import Job  # noqa: F401 — registers Job + JobEvent tables on Base
from src.jobs.jobs import count_active_for_user, reclaim_orphaned_jobs


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.close()


def _job(jid: str, status: str, user_id: int = 1) -> Job:
    return Job(id=jid, user_id=user_id, mode="chat", status=status, query="q")


def test_reclaim_fails_running_jobs_and_unblocks_user(session):
    session.add_all([_job("a", "running"), _job("b", "queued"), _job("c", "succeeded")])
    session.flush()
    assert count_active_for_user(session, 1) == 2  # running + queued both count as active

    assert reclaim_orphaned_jobs(session) == 1  # only the running one is orphaned
    assert session.get(Job, "a").status == "failed"
    assert session.get(Job, "a").finished_at is not None
    assert session.get(Job, "a").error
    assert session.get(Job, "b").status == "queued"      # queued is left for the worker to claim
    assert session.get(Job, "c").status == "succeeded"   # finished jobs untouched
    assert count_active_for_user(session, 1) == 1         # user is no longer blocked by the stuck job


def test_reclaim_leaves_already_deleted_jobs_alone(session):
    j = _job("d", "running")
    j.deleted_at = datetime.now(timezone.utc)
    session.add(j)
    session.flush()
    assert reclaim_orphaned_jobs(session) == 0
    assert session.get(Job, "d").status == "running"  # hidden already; not a live blocker


def test_reclaim_noop_when_nothing_running(session):
    session.add_all([_job("e", "queued"), _job("f", "failed")])
    session.flush()
    assert reclaim_orphaned_jobs(session) == 0
