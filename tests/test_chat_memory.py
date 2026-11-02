"""Chat memory (BACKLOG L12): a chat's earlier turns are rebuilt in full from its jobs, kept within the token
budget, and the oldest are folded once into a running summary when the chat grows long."""

from __future__ import annotations

import contextlib
import dataclasses
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.config import Config
from src.core.conversation import SUMMARY_ROLE, format_conversation_block, normalize_conversation
from src.core.db import Base
from src.core.models_db import ChatThreadRow, Job, JobEvent
from src.jobs import chat_memory as cm
from src.llm.assistant import messages_for

T0 = datetime(2026, 11, 2, 9, 0)
CFG = dataclasses.replace(Config.from_env(), llm_num_ctx=8000, context_history_share=0.25)   # budget 2,000


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(bind=engine)

    @contextlib.contextmanager
    def scope(_config=None):
        yield session
        session.flush()

    monkeypatch.setattr("src.core.db.session_scope", scope)
    yield session
    session.close()


@pytest.fixture
def summaries(monkeypatch):
    calls: list[str] = []

    def chat(self, *, system, user, **kw):
        calls.append(user)
        return f"summary #{len(calls)}"

    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat", chat)
    return calls


def add_job(db, job_id, minute, *, query, answer=None, thread="t1", user=1):
    db.add(Job(id=job_id, user_id=user, mode="chat", query=query, payload_json=json.dumps({"thread_id": thread}),
               created_at=T0 + timedelta(minutes=minute)))
    if answer is not None:
        db.add(JobEvent(job_id=job_id, seq=1, event_json=json.dumps(
            {"type": "agent_done", "agent_id": "assistant", "output": answer})))
    db.flush()


def chat_thread(db, **fields):
    db.add(ChatThreadRow(id="t1", user_id=1, title="Docker", **fields))
    db.flush()


def test_a_short_chat_is_remembered_in_full(db, summaries):
    chat_thread(db)
    long_answer = "x" * 7000                                   # past the old 6,000-character cut
    add_job(db, "a", 1, query="what is docker", answer=long_answer)
    add_job(db, "other", 2, query="elsewhere", answer="no", thread="t2")
    add_job(db, "now", 3, query="and podman?")
    turns = cm.chat_memory(CFG, job_id="now", fallback=[])
    assert turns == [{"role": "user", "content": "what is docker"}, {"role": "assistant", "content": long_answer}]
    assert summaries == []


def test_a_long_chat_folds_its_oldest_turns_into_a_saved_summary_once(db, summaries):
    chat_thread(db)
    for n in range(5):                                         # ~800 tokens each → 4,000 > budget 2,000
        add_job(db, f"j{n}", n, query=f"question {n}", answer="y" * 3200)
    add_job(db, "now", 10, query="next")
    events: list[dict] = []
    turns = cm.chat_memory(CFG, job_id="now", fallback=[], on_progress=events.append)
    assert turns[0] == {"role": SUMMARY_ROLE, "content": "summary #1"}
    assert [t["content"] for t in turns[1:] if t["role"] == "user"] == ["question 4"]   # ≤ half the budget left
    assert events == [{"type": "memory_compacted", "folded": 4}]
    assert "question 0" in summaries[0] and "question 3" in summaries[0] and "question 4" not in summaries[0]
    row = db.get(ChatThreadRow, "t1")
    assert row.summary == "summary #1" and row.summary_through == T0 + timedelta(minutes=3)

    add_job(db, "later", 11, query="one more")                  # the next turn reuses the summary: no new call
    turns = cm.chat_memory(CFG, job_id="later", fallback=[])
    assert len(summaries) == 1 and turns[0]["content"] == "summary #1"
    assert [t["content"] for t in turns if t["role"] == "user"] == ["question 4", "next"]


def test_jobs_outside_a_saved_chat_keep_the_browsers_turns(db, summaries):
    add_job(db, "now", 1, query="hi", thread="unknown")
    fallback = [{"role": "user", "content": "earlier"}]
    assert cm.chat_memory(CFG, job_id="now", fallback=fallback) == fallback


def test_a_failed_summary_drops_the_oldest_turns_instead_of_failing(db, monkeypatch):
    def down(self, **kw):
        raise ConnectionError("ollama down")

    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat", down)
    chat_thread(db)
    for n in range(5):
        add_job(db, f"j{n}", n, query=f"question {n}", answer="y" * 3200)
    add_job(db, "now", 10, query="next")
    turns = cm.chat_memory(CFG, job_id="now", fallback=[])
    assert [t["content"] for t in turns if t["role"] == "user"] == ["question 4"]
    assert db.get(ChatThreadRow, "t1").summary is None


def test_summarize_works_through_a_chat_bigger_than_the_model(summaries):
    turns = [{"role": "assistant", "content": "z" * 6000} for _ in range(6)]   # 9,000 tokens > num_ctx 8,000
    assert cm.summarize("", turns, config=CFG) == f"summary #{len(summaries)}"
    assert len(summaries) > 1 and "summary #1" in summaries[1]        # each piece carries the summary forward


def test_summarize_whole_chat_for_continue(db, summaries):
    chat_thread(db, summary="old part", summary_through=T0)
    add_job(db, "a", 1, query="recent question", answer="recent answer")
    assert cm.summarize_chat(CFG, "t1", 1) == "summary #1"
    assert "old part" in summaries[0] and "recent answer" in summaries[0]
    with pytest.raises(LookupError):
        cm.summarize_chat(CFG, "t1", 2)                        # someone else's chat


def test_the_summary_turn_leads_every_prompt():
    turns = [{"role": SUMMARY_ROLE, "content": "the gist"}] + [{"role": "user", "content": f"q{n}"} for n in range(20)]
    kept = normalize_conversation(turns)
    assert kept[0]["role"] == SUMMARY_ROLE and len(kept) == 13           # summary + the last 12 turns
    assert "### Earlier in this chat (summary)\nthe gist" in format_conversation_block(kept)
    msgs = messages_for("now?", CFG, kept, "m")
    assert msgs[1] == {"role": "system", "content": "Summary of the earlier part of this chat:\nthe gist"}
    assert msgs[-1]["content"] == "now?" and all(m["role"] != SUMMARY_ROLE for m in msgs)
