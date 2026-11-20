"""Housekeeping (BACKLOG L30 T5, src/core/maintenance.py): the stored messages of chats deleted long ago and spent reset
links are removed; everything else stays."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core import maintenance
from src.core.db import Base
from src.core.models_db import Job, JobEvent, PasswordReset, User, utcnow


def test_old_deleted_chats_lose_their_stored_messages_and_spent_links_go(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope(config=None):
        with Session(engine) as s:
            yield s
            s.commit()

    monkeypatch.setattr(maintenance, "session_scope", scope)
    now = utcnow()
    with Session(engine) as s:
        s.add(User(id=1, username="ana", password_hash="x"))
        s.flush()
        s.add_all([Job(id="live", user_id=1, mode="chat", status="succeeded", query="q"),
                   Job(id="recent", user_id=1, mode="chat", status="succeeded", query="q", deleted_at=now - timedelta(days=3)),
                   Job(id="old", user_id=1, mode="chat", status="succeeded", query="q", deleted_at=now - timedelta(days=40))])
        s.flush()
        s.add_all([JobEvent(job_id=j, seq=1, event_json="{}") for j in ("live", "recent", "old")])
        s.add_all([PasswordReset(token_hash="a" * 64, user_id=1, expires_at=now + timedelta(hours=5)),
                   PasswordReset(token_hash="b" * 64, user_id=1, expires_at=now - timedelta(hours=1)),
                   PasswordReset(token_hash="c" * 64, user_id=1, expires_at=now + timedelta(hours=5), used_at=now)])
        s.commit()
    assert maintenance.tidy() == {"job_events": 1, "password_resets": 2}
    with Session(engine) as s:
        assert {e.job_id for e in s.scalars(select(JobEvent))} == {"live", "recent"}
        assert s.scalar(select(func.count()).select_from(Job)) == 3                 # the job rows stay
        assert [r.token_hash[0] for r in s.scalars(select(PasswordReset))] == ["a"]
