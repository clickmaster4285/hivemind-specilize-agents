"""Search across all chats (BACKLOG L26 H3): own chats only, questions and written answers, every word, one row
per chat with all its message ids; deleted messages and chats stay out."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.auth import hash_password
from src.core.db import Base
from src.core.models_db import ChatThreadRow, Job, JobEvent, User  # noqa: F401 — registers the tables
from src.jobs.chat_search import search_chats, search_terms, snippet

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.close()


def _user(s: Session, name: str) -> User:
    u = User(username=name, password_hash=hash_password("passphrase1"), role="member", status="active")
    s.add(u)
    s.flush()
    return u


def _msg(s: Session, user: User, jid: str, thread: str | None, query: str, answer: str, minute: int,
         *, deleted: bool = False, extra_event: str | None = None) -> None:
    payload = {"thread_id": thread} if thread else {}
    s.add(Job(id=jid, user_id=user.id, mode="chat", status="succeeded", query=query, payload_json=json.dumps(payload),
              created_at=T0 + timedelta(minutes=minute), deleted_at=T0 if deleted else None))
    s.flush()
    seq = 1
    if extra_event:
        s.add(JobEvent(job_id=jid, seq=seq, event_json=json.dumps({"type": "tool_result", "output": extra_event})))
        seq += 1
    s.add(JobEvent(job_id=jid, seq=seq, event_json=json.dumps(
        {"type": "agent_done", "agent_id": "assistant", "output": answer}, ensure_ascii=False)))
    s.flush()


def test_terms_and_snippet():
    assert search_terms("Who is  Isaac newton? is") == ["who", "is", "isaac", "newton"]
    assert snippet("x " * 200 + "Newton here", "newton").startswith("…")


def test_finds_questions_and_answers_of_own_chats_only(session):
    alice, bob = _user(session, "alice"), _user(session, "bob")
    session.add(ChatThreadRow(id="t1", user_id=alice.id, title="Physics"))
    _msg(session, alice, "a1", "t1", "Who discovered gravity?", "Isaac Newton described gravity.", 1)
    _msg(session, alice, "a2", "t1", "and his birthday?", "He was born in 1643.", 2)
    _msg(session, alice, "a3", "t2", "Restart nginx", "Run systemctl restart nginx.", 3)
    _msg(session, bob, "b1", "t9", "Newton facts", "Newton was English.", 4)

    rows = search_chats(session, alice.id, "newton")
    assert [(r["thread_id"], r["title"], r["job_ids"], r["in"]) for r in rows] == [
        ("t1", "Physics", ["a1", "a2"], "answer")
    ]
    assert "Isaac Newton" in rows[0]["snippet"]
    assert [r["thread_id"] for r in search_chats(session, bob.id, "newton")] == ["t9"]


def test_every_word_must_start_a_word_in_the_visible_text(session):
    alice = _user(session, "alice")
    _msg(session, alice, "a1", "t1", "zip the logs", "Done.", 1)
    _msg(session, alice, "a2", "t2", "check the server", "All fine.", 2, extra_event="secret ip 10.0.0.5")
    assert search_chats(session, alice.id, "ip") == []                 # "zip" and tool output don't count
    assert [r["thread_id"] for r in search_chats(session, alice.id, "zip logs")] == ["t1"]
    assert search_chats(session, alice.id, "zip nginx") == []


def test_deleted_messages_and_chats_and_legacy_messages(session):
    alice = _user(session, "alice")
    session.add(ChatThreadRow(id="gone", user_id=alice.id, title="Old", deleted_at=T0))
    _msg(session, alice, "a1", "gone", "router backup", "ok", 1)
    _msg(session, alice, "a2", "t2", "router backup", "ok", 2, deleted=True)
    _msg(session, alice, "a3", None, "router firmware", "ok", 3)
    rows = search_chats(session, alice.id, "router")
    assert [(r["thread_id"], r["job_ids"]) for r in rows] == [("job-a3", ["a3"])]


def test_newest_match_first_and_limit(session):
    alice = _user(session, "alice")
    for i in range(5):
        _msg(session, alice, f"a{i}", f"t{i}", f"backup plan {i}", "ok", i)
    rows = search_chats(session, alice.id, "backup", limit=2)
    assert [r["thread_id"] for r in rows] == ["t4", "t3"]
