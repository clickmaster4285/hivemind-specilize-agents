"""Sharing chats and projects read-only by link (BACKLOG L30 T4; the user's choice: "just share and send the link"):
anyone signed in who opens it reads the chat and may continue in a chat of their own; the owner stops sharing at any
time, and a new link replaces the old one."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.db import Base
from src.core.models_db import ChatShare, ChatThreadRow, Job, ProjectRow, User, utcnow
from web.routers import history as history_routes
from web.routers import jobs as jobs_routes
from web.schemas import SharedLinkPayload, ThreadContinuePayload

OWNER, ANA, BO = 1, 2, 3


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([User(id=OWNER, username="olga", password_hash="x"), User(id=ANA, username="ana", password_hash="x"),
                   User(id=BO, username="bo", password_hash="x")])
        s.add(ProjectRow(id="proj", user_id=OWNER, name="Migration"))
        s.add_all([ChatThreadRow(id="t1", user_id=OWNER, title="DNS plan"),
                   ChatThreadRow(id="t2", user_id=OWNER, title="Backups", project_id="proj")])
        s.flush()
        s.add_all([Job(id="j1", user_id=OWNER, mode="chat", status="succeeded", query="how is dns set up",
                       payload_json=json.dumps({"thread_id": "t1", "conversation": [{"role": "user", "content": "x"}]})),
                   Job(id="j2", user_id=OWNER, mode="chat", status="succeeded", query="backup times",
                       payload_json=json.dumps({"thread_id": "t2"}))])
        s.commit()
        yield s


def _u(db, uid) -> User:
    return db.get(User, uid)


def _open(db, uid, token):
    return history_routes.open_shared_link(SharedLinkPayload(token=token), db=db, user=_u(db, uid))


def test_whoever_opens_the_link_can_read_the_chat_but_not_change_it(db):
    token = history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"]
    with pytest.raises(HTTPException):
        jobs_routes.job_events_history("j1", db=db, user=_u(db, ANA))             # not before opening it
    assert _open(db, ANA, token) == {"thread_id": "t1", "own": False}
    opened = history_routes.shared_chat("t1", db=db, user=_u(db, ANA))
    assert (opened["title"], opened["owner"], [j["id"] for j in opened["jobs"]]) == ("DNS plan", "olga", ["j1"])
    assert "conversation" not in opened["jobs"][0]["payload"]
    assert jobs_routes.job_events_history("j1", db=db, user=_u(db, ANA))["job"]["id"] == "j1"
    with pytest.raises(HTTPException) as no:
        jobs_routes.soft_delete_job_endpoint("j1", db=db, user=_u(db, ANA))
    assert no.value.status_code == 403
    assert [c["title"] for c in history_routes.shared_with_me(db=db, user=_u(db, ANA))] == ["DNS plan"]
    assert history_routes.thread_link("t1", db=db, user=_u(db, OWNER)) == {"shared": True, "readers": ["ana"]}


def test_a_project_link_covers_its_chats_also_later_ones(db):
    token = history_routes.make_project_link("proj", db=db, user=_u(db, OWNER))["token"]
    assert _open(db, BO, token)["thread_id"] == "t2"
    db.add(ChatThreadRow(id="t3", user_id=OWNER, title="Later", project_id="proj"))
    db.commit()
    assert {c["thread_id"] for c in history_routes.shared_with_me(db=db, user=_u(db, BO))} == {"t2", "t3"}
    with pytest.raises(HTTPException):
        history_routes.shared_chat("t1", db=db, user=_u(db, BO))                 # not in the project


def test_stop_sharing_ends_the_link_and_everyones_access(db):
    token = history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"]
    _open(db, ANA, token)
    history_routes.stop_thread_link("t1", db=db, user=_u(db, OWNER))
    with pytest.raises(HTTPException):
        history_routes.shared_chat("t1", db=db, user=_u(db, ANA))
    with pytest.raises(HTTPException):
        _open(db, BO, token)
    assert db.query(ChatShare).count() == 0


def test_a_new_link_replaces_the_old_one_and_readers_keep_access(db):
    old = history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"]
    _open(db, ANA, old)
    new = history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"]
    with pytest.raises(HTTPException):
        _open(db, BO, old)
    assert _open(db, BO, new)["thread_id"] == "t1"
    assert history_routes.shared_chat("t1", db=db, user=_u(db, ANA))["title"] == "DNS plan"


def test_only_the_owner_makes_a_link_and_a_deleted_chat_is_no_longer_readable(db):
    with pytest.raises(HTTPException) as exc:
        history_routes.make_thread_link("t1", db=db, user=_u(db, ANA))
    assert exc.value.status_code == 404
    _open(db, ANA, history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"])
    db.get(ChatThreadRow, "t1").deleted_at = utcnow()
    db.commit()
    assert history_routes.shared_with_me(db=db, user=_u(db, ANA)) == []
    with pytest.raises(HTTPException):
        jobs_routes.job_events_history("j1", db=db, user=_u(db, ANA))


def test_the_owner_opening_their_own_link_is_just_their_chat(db):
    token = history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"]
    assert _open(db, OWNER, token) == {"thread_id": "t1", "own": True}
    assert history_routes.thread_link("t1", db=db, user=_u(db, OWNER))["readers"] == []


def test_only_the_tokens_fingerprint_is_stored(db):
    token = history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"]
    stored = db.query(ChatShare).one().token_hash
    assert stored and token not in stored and len(stored) == 64


def test_continue_in_my_own_chat_starts_from_the_shared_chats_summary(db, monkeypatch):
    monkeypatch.setattr(history_routes, "summarize_chat", lambda config, tid, uid: f"summary of {tid} by {uid}")
    _open(db, ANA, history_routes.make_thread_link("t1", db=db, user=_u(db, OWNER))["token"])
    out = history_routes.continue_shared("t1", ThreadContinuePayload(new_thread_id="mine"), db=db, user=_u(db, ANA))
    mine = db.get(ChatThreadRow, "mine")
    assert (mine.user_id, mine.title, mine.summary) == (ANA, "DNS plan (continued)", "summary of t1 by 1")
    assert out["summary"] == "summary of t1 by 1" and db.get(ChatThreadRow, "t1").user_id == OWNER


def test_the_people_list_shows_only_names_and_teams_of_active_colleagues(db):
    from web.routers.auth import people

    db.get(User, BO).status = "former"
    db.get(User, ANA).team = "IT"
    db.commit()
    assert people(user=_u(db, BO), db=db) == [{"id": ANA, "username": "ana", "team": "IT"},
                                              {"id": OWNER, "username": "olga", "team": None}]
