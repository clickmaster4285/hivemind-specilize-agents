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
from src.jobs import chat_recall
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


@pytest.fixture(autouse=True)
def recalls(monkeypatch):
    """Chat recall (O63) without the brain database or the embedding model: records each search, finds nothing
    unless a test sets `found`."""
    calls: list[dict] = []
    found: list = []

    def recall(config, **kw):
        calls.append(kw)
        return list(found)

    monkeypatch.setattr("src.jobs.chat_recall.recall", recall)
    return calls, found


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


# ── chat recall (ISSUES_LOG O63) ───────────────────────────────────────────

def _long_chat(db):
    chat_thread(db)
    for n in range(5):
        add_job(db, f"j{n}", n, query=f"question {n}", answer="y" * 3200)


def test_no_summary_no_recall(db, summaries, recalls):
    chat_thread(db)
    add_job(db, "a", 1, query="what is docker", answer="a tool")
    add_job(db, "now", 2, query="and podman?")
    cm.chat_memory(CFG, job_id="now", fallback=[])
    assert recalls[0] == []


def test_the_folded_turns_are_searched_for_the_new_message(db, summaries, recalls):
    calls, found = recalls
    found.append(chat_recall.Recalled("User: the NVR is at 192.168.10.45", T0, 0.8))
    _long_chat(db)
    add_job(db, "now", 10, query="what was the NVR's IP?")
    turns = cm.chat_memory(CFG, job_id="now", fallback=[])
    [call] = calls
    assert call["job_ids"] == ["j0", "j1", "j2", "j3"]            # just folded; the recent turn is in the prompt
    assert call["message"] == "what was the NVR's IP?" and call["user_id"] == 1 and call["thread_id"] == "t1"
    assert 0 < call["budget"] <= chat_recall.recall_budget(cm.history_budget(CFG))
    assert turns[0]["content"].startswith("summary #1\n\n**Earlier messages, word for word**")
    assert "User: the NVR is at 192.168.10.45" in turns[0]["content"]
    assert db.get(ChatThreadRow, "t1").summary == "summary #1"      # the stored summary stays without recall


def test_a_chat_with_a_summary_keeps_room_for_recall(db, summaries, recalls):
    """Without a summary the turns may fill the budget; with one, RECALL_SHARE of it is kept for recall, so
    the next fold comes that much earlier."""
    chat_thread(db, summary="old part", summary_through=T0)
    add_job(db, "a", 1, query="q1", answer="y" * 3000)               # ~750 tokens each, budget 2,000
    add_job(db, "b", 2, query="q2", answer="y" * 3000)
    add_job(db, "now", 3, query="next")
    turns = cm.chat_memory(CFG, job_id="now", fallback=[])
    assert len(summaries) == 1                                      # 1,500 + summary + 500 kept > 2,000
    assert [t["content"] for t in turns if t["role"] == "user"] == ["q2"]


def test_a_failed_recall_keeps_the_summary(db, summaries, monkeypatch):
    def down(config, **kw):
        raise ConnectionError("embedding model down")

    monkeypatch.setattr("src.jobs.chat_recall.recall", down)
    _long_chat(db)
    add_job(db, "now", 10, query="next")
    assert cm.chat_memory(CFG, job_id="now", fallback=[])[0]["content"] == "summary #1"


def test_a_deleted_job_is_not_recalled(db, summaries, recalls):
    calls, _ = recalls
    chat_thread(db, summary="old part", summary_through=T0 + timedelta(minutes=2))
    add_job(db, "kept", 1, query="q1", answer="a1")
    add_job(db, "gone", 2, query="q2", answer="a2")
    db.get(Job, "gone").deleted_at = T0
    add_job(db, "now", 3, query="next")
    cm.chat_memory(CFG, job_id="now", fallback=[])
    assert calls[0]["job_ids"] == ["kept"]


def test_remember_job_stores_a_saved_chats_answer(db, monkeypatch):
    stored: list[dict] = []
    monkeypatch.setattr("src.jobs.chat_recall.remember", lambda config, **kw: stored.append(kw) or 1)
    add_job(db, "a", 1, query="what is docker", answer="a tool")
    add_job(db, "no-answer", 2, query="still writing")
    db.add(Job(id="no-thread", user_id=1, mode="chat", query="hi", payload_json="{}", created_at=T0))
    assert cm.remember_job(CFG, "a") == 1
    assert cm.remember_job(CFG, "no-answer") == 0 and cm.remember_job(CFG, "no-thread") == 0
    assert stored == [{"user_id": 1, "thread_id": "t1", "job_id": "a", "asked_at": T0 + timedelta(minutes=1),
                       "question": "what is docker", "answer": "a tool"}]
