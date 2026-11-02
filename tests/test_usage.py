"""Token usage (BACKLOG L11): every model call of a job is recorded with Ollama's own counts, credited to
the feature that asked, and summed per answer and per user — against a fake Ollama, no real model."""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.config import Config
from src.core.db import Base
from src.core.models_db import LlmUsage, User
from src.llm import usage
from src.llm.llm_client import LLMClient, LLMStopped

COUNTS = {"prompt_eval_count": 120, "eval_count": 30, "total_duration": 2_500_000_000}


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200)
        self.end_headers()
        if not body.get("stream"):
            self.wfile.write(json.dumps({"message": {"content": "plain answer"}, **COUNTS}).encode())
            return
        for word in ["streamed", " answer"]:
            self.wfile.write((json.dumps({"message": {"content": word}, "done": False}) + "\n").encode())
        self.wfile.write((json.dumps({"message": {"content": ""}, "done": True, **COUNTS}) + "\n").encode())


@pytest.fixture
def cfg():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield dataclasses.replace(Config.from_env(), llm_base_url=f"http://127.0.0.1:{server.server_port}",
                              llm_model="qwen3.5:9b", llm_think=False, timeout_seconds=5)
    server.shutdown()


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    lock = threading.Lock()

    @contextlib.contextmanager
    def scope(_config=None):
        with lock:
            yield session
            session.flush()

    monkeypatch.setattr("src.core.db.session_scope", scope)
    yield session
    session.close()


def rows(db) -> list[LlmUsage]:
    return list(db.scalars(select(LlmUsage).order_by(LlmUsage.id)))


def generate_suggestions(client_chat):            # stands in for a feature module that is handed a wrapper
    return client_chat(system="s", user="u")


def test_calls_outside_a_job_are_not_recorded(cfg, db):
    LLMClient(cfg).chat(system="s", user="u")
    assert rows(db) == []


def test_each_call_is_recorded_with_ollamas_counts_and_its_caller(cfg, db):
    client = LLMClient(cfg)
    with usage.job_usage("job1", 7):
        client.chat(system="s", user="u")                                         # plain
        client.chat(system="s", user="u", on_delta=lambda text: None)             # streamed
        client.chat_tools(messages=[{"role": "user", "content": "u"}], tools=[])  # tool calling
        generate_suggestions(lambda **kw: client.chat(**kw))                      # through a lambda wrapper
    got = rows(db)
    assert len(got) == 4
    assert all((r.job_id, r.user_id, r.prompt_tokens, r.output_tokens, r.duration_ms, r.estimated)
               == ("job1", 7, 120, 30, 2500, False) for r in got)
    assert {r.step for r in got} == {"test_usage"}           # the caller, not the client or the lambda


def test_a_stopped_answer_is_estimated(cfg, db):
    with usage.job_usage("job1", 7), pytest.raises(LLMStopped):
        LLMClient(cfg).chat(system="s" * 40, user="u" * 40, on_delta=lambda text: None,
                            should_continue=iter([True, False, False, False]).__next__)
    (row,) = rows(db)
    assert row.estimated and row.prompt_tokens == 20


def test_extras_in_worker_threads_count_toward_the_job(cfg, db):
    client = LLMClient(cfg)
    with usage.job_usage("job1", 7), ThreadPoolExecutor(2) as pool:
        for future in [pool.submit(contextvars.copy_context().run, lambda: client.chat(system="s", user="u"))
                       for _ in range(2)]:
            future.result()
    assert [r.job_id for r in rows(db)] == ["job1", "job1"]


def test_totals_per_answer_and_per_user(db):
    db.add_all([User(id=1, username="ana", password_hash="x"), User(id=2, username="bo", password_hash="x")])
    for job, user, step, p, o in [("a", 1, "assistant", 100, 10), ("a", 1, "suggestions", 50, 5),
                                  ("b", 2, "doc_writer", 400, 300)]:
        db.add(LlmUsage(job_id=job, user_id=user, step=step, model="m", prompt_tokens=p, output_tokens=o,
                        duration_ms=1000))
    db.flush()
    answer = usage.job_totals(db, "a")
    assert (answer["calls"], answer["prompt_tokens"], answer["output_tokens"], answer["seconds"]) == (2, 150, 15, 2.0)
    assert [s["step"] for s in answer["steps"]] == ["assistant", "suggestions"]
    assert usage.job_totals(db, "none") is None
    since = datetime.now(timezone.utc) - timedelta(days=1)
    everyone = usage.summary(db, since)
    assert [u["username"] for u in everyone["users"]] == ["bo", "ana"]           # biggest first
    assert everyone["total"]["prompt_tokens"] == 550
    mine = usage.summary(db, since, user_id=1)
    assert [u["answers"] for u in mine["users"]] == [1] and mine["total"]["output_tokens"] == 15
