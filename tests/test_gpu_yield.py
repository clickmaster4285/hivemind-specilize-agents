"""O52: optional model work after an answer (suggestions, quiz, the chat-summary fold) gives way to the next
question, and the user may send it as soon as the answer is written."""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.config import Config
from src.core.models_db import Base, User
from src.jobs import live
from src.jobs.chat_runner import emit_answer_extras
from src.jobs.jobs import create_job
from src.llm.llm_client import LLMStopped

ANSWER = "DNS turns names into addresses. " * 10           # long enough for extras (> 120 characters)


def test_optional_work_stops_for_a_question_or_stop():
    keep_going = live.yields_to_questions("job-a")
    assert keep_going()
    live.question_asked()
    assert not keep_going()
    assert live.yields_to_questions("job-a")()                 # a new mark: the old question is history
    live.stop("job-b")
    assert not live.yields_to_questions("job-b")()
    live.clear("job-b")


def _session_and_user():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    user = User(username="u", password_hash="x", role="user")
    session.add(user)
    session.flush()
    return session, user


def test_the_next_question_is_accepted_once_the_answer_is_written():
    session, user = _session_and_user()
    cfg = dataclasses.replace(Config.from_env(), prompt_filter_enabled=False, max_active_jobs_per_user=1)
    first = create_job(session, user=user, query="how does DNS work", mode="chat", payload={}, config=cfg)
    with pytest.raises(ValueError, match="active job"):
        create_job(session, user=user, query="and DHCP?", mode="chat", payload={}, config=cfg)
    live.answer_written(first.id)
    try:
        keep_going = live.yields_to_questions(first.id)
        create_job(session, user=user, query="and DHCP?", mode="chat", payload={}, config=cfg)
        assert not keep_going()                                 # the first job's extras stop for it
    finally:
        live.clear(first.id)
    assert first.id not in live.finishing_jobs()


class _Client:
    """A model that writes suggestions slowly: a question arrives while it writes."""

    def __init__(self, *, interrupt: bool):
        self.interrupt = interrupt
        self.calls = 0

    def chat(self, *, should_continue=None, **_kw):
        self.calls += 1
        assert should_continue is not None                      # optional work is always interruptible
        if self.interrupt:
            live.question_asked()
        if not should_continue():
            raise LLMStopped("")
        return "What is a DNS cache?\nWho runs the root servers?\nWhat is a TTL?"


@pytest.mark.parametrize("interrupt", [False, True])
def test_suggestions_give_way_to_a_question(interrupt):
    cfg = dataclasses.replace(Config.from_env(), suggestions_enabled=True, web_images_enabled=False)
    events: list[dict] = []
    client = _Client(interrupt=interrupt)
    emit_answer_extras("how does DNS work", ANSWER, job_id="job-x", config=cfg, model="m", client=client,
                       on_progress=events.append)
    try:
        assert events[0]["type"] == "answer_written"            # the browser may send again from here
        assert live.finishing_jobs() >= {"job-x"}
        got = [e for e in events if e.get("suggestions")]
        assert bool(got) is not interrupt
    finally:
        live.clear("job-x")
