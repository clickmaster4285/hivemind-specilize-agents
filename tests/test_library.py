"""/api/library (BACKLOG L24 U5): the caller's own made files only — no uploads, no web illustrations, no
deleted chats, never another user's."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.auth import hash_password
from src.core.db import Base
from src.core.models_db import Job, JobEvent, User  # noqa: F401 — registers the tables on Base
from web.routers.library import list_library


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.close()


def _user(session: Session, name: str) -> User:
    u = User(username=name, password_hash=hash_password("passphrase1"), role="member", status="active")
    session.add(u)
    session.flush()
    return u


def _job(session: Session, user: User, jid: str, attachments: list, *, deleted: bool = False) -> None:
    payload = {"thread_id": f"t-{jid}", "attachments": attachments}
    session.add(Job(id=jid, user_id=user.id, mode="chat", status="succeeded", query=f"q {jid}",
                    payload_json=json.dumps(payload),
                    deleted_at=datetime.now(timezone.utc) if deleted else None))
    session.flush()


def test_lists_only_made_files_of_the_caller(session):
    alice, bob = _user(session, "alice"), _user(session, "bob")
    _job(session, alice, "a1", [
        {"filename": "01_photo.png", "mime": "image/png", "relpath": "a1/images/01_photo.png"},   # upload
        {"filename": "web1.jpg", "mime": "image/jpeg", "kind": "web"},                          # found online
        {"filename": "report.docx", "mime": "x", "bytes": 10, "kind": "docx"},
    ])
    _job(session, alice, "a2", [{"filename": "gone.pdf", "kind": "pdf"}], deleted=True)
    _job(session, bob, "b1", [{"filename": "bob.xlsx", "kind": "xlsx"}])

    rows = list_library(db=session, user=alice)
    assert [(r["job_id"], r["filename"], r["kind"], r["thread_id"]) for r in rows] == [
        ("a1", "report.docx", "docx", "t-a1")
    ]
    assert [r["filename"] for r in list_library(db=session, user=bob)] == ["bob.xlsx"]
