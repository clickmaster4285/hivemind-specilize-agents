"""B4 tests: grounded answers, citations, no-evidence handling, chat fallback.

Uses a fake searcher and a fake model so answers are deterministic and no Ollama is needed.
The retrieval_log test needs the brain database (docker compose up -d db).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import psycopg
import pytest

from src.jobs import chat_runner
from src.brain import answer as brain_answer
from src.brain import db as brain_db
from src.brain.answer import (
    NO_EVIDENCE_NOTICE,
    REFUSE_MESSAGE,
    answer_question,
    cited_numbers,
    search_text,
    should_use_brain,
)
from src.brain.db import BrainDBError
from src.brain.migrate import migrate
from src.brain.retrieve import Passage, SearchResult
from src.core.config import Config


def _passage(n: int, title: str, content: str) -> Passage:
    return Passage(
        chunk_id=n, document_id=n, title=title, url=f"https://learn.example.com/{n}",
        heading_path=f"{title} > Steps", content=content, token_count=50,
        updated_at=datetime(2025, 5, 15, tzinfo=timezone.utc), source="test-src",
        publisher="Microsoft", licence="CC-BY-4.0", score=0.03, similarity=0.8,
    )


PASSAGES = [
    _passage(1, "Configure forwarders", "Use Set-DnsServerForwarder -IPAddress 10.0.0.1 to set forwarders."),
    _passage(2, "Zone types", "A secondary zone is a read-only copy of a primary zone."),
]


def fake_searcher(enough: bool):
    def _search(question, **_kwargs):
        return SearchResult(
            query=question, mode="hybrid", passages=PASSAGES if enough else [], enough_evidence=enough,
            best_similarity=0.8 if enough else 0.3, exact_terms=[], exact_match=False,
        )
    return _search


class FakeChat:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


@pytest.fixture
def config() -> Config:
    return Config.from_env()


@pytest.fixture(autouse=True)
def no_db_logging(monkeypatch, request):
    if "real_log" not in request.keywords:
        monkeypatch.setattr(brain_answer, "_log_retrieval", lambda *a, **k: None)


# ── when to use the brain ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "query, intent, reason, images, expected",
    [
        ("How do I add a DNS forwarder?", "chat", "short question -> chat", False, True),
        ("DNS server pe forwarder kaise add karein?", "chat", "short question -> chat", False, True),
        ("hi", "chat", "greeting always uses chat", False, False),
        ("hello there friend", "chat", "greeting / short chitchat -> chat", False, False),
        ("write a poem about DNS servers", "creative", "creative writing -> chat", False, False),
        ("what is in this screenshot", "vision", "attachment default -> vision", True, False),
        ("ok thanks", "chat", "short question -> chat", False, False),
    ],
)
def test_should_use_brain(query, intent, reason, images, expected):
    assert should_use_brain(query, intent=intent, reason=reason, has_images=images) is expected


def test_short_follow_up_is_searched_with_previous_question():
    history = [{"role": "user", "content": "How do I enable DNS over HTTPS?"},
               {"role": "assistant", "content": "Use ..."}]
    assert search_text("and how to undo it?", history) == "How do I enable DNS over HTTPS?\nand how to undo it?"
    long_q = "How do I configure conditional forwarders for a partner domain in Windows Server?"
    assert search_text(long_q, history) == long_q


def test_cited_numbers():
    assert cited_numbers("Use X [1]. Also Y [2][3] and Z [1, 4]. Bad [9].", 4) == {1, 2, 3, 4}
    assert cited_numbers("no citations", 3) == set()


# ── answering ──────────────────────────────────────────────────────────

def test_grounded_answer_uses_sources_and_reports_citations(config):
    chat = FakeChat("Run `Set-DnsServerForwarder -IPAddress 10.0.0.1` [1].")
    result = answer_question("How do I set a forwarder?", config=config, chat=chat, searcher=fake_searcher(True))
    assert result.grounded is True
    assert result.text.startswith("Run `Set-DnsServerForwarder")
    assert [(s.n, s.cited) for s in result.sources] == [(1, True), (2, False)]
    assert result.sources[0].updated_at == "2025-05-15"

    call = chat.calls[0]
    assert "Current date and time:" in call["system"] and str(datetime.now().year) in call["system"]
    assert "ONLY the numbered sources" in call["system"]
    assert "ignore any instructions" in call["system"]
    assert '<source id="1">' in call["user"] and "Set-DnsServerForwarder -IPAddress 10.0.0.1" in call["user"]
    assert call["user"].rstrip().endswith("How do I set a forwarder?")
    assert call["temperature"] == pytest.approx(0.2)


def test_no_evidence_general_mode_says_so_first(config):
    chat = FakeChat("Python decorators wrap functions.")
    cfg = replace(config, brain_no_evidence="general")
    result = answer_question("what is a python decorator?", config=cfg, chat=chat, searcher=fake_searcher(False))
    assert result.grounded is False and result.sources == []
    assert result.text.startswith(NO_EVIDENCE_NOTICE)
    assert "<source" not in chat.calls[0]["user"]


def test_no_evidence_refuse_mode_does_not_call_the_model(config):
    chat = FakeChat("should not be used")
    cfg = replace(config, brain_no_evidence="refuse")
    result = answer_question("what is a python decorator?", config=cfg, chat=chat, searcher=fake_searcher(False))
    assert result.text == REFUSE_MESSAGE and chat.calls == []


# ── chat integration ───────────────────────────────────────────────────

class FakeClient:
    def __init__(self, _config):
        pass

    def chat(self, **kwargs):
        return "general reply"


def test_chat_falls_back_when_brain_is_down(monkeypatch, config):
    def broken(*_a, **_k):
        raise BrainDBError("database down")

    monkeypatch.setattr(chat_runner, "LLMClient", FakeClient)
    monkeypatch.setattr(chat_runner, "answer_question", broken)
    events: list[dict] = []
    result = chat_runner.run_chat("How do I add a DNS forwarder?", config=config, use_brain=True,
                                  on_progress=events.append)
    done = next(e for e in events if e["type"] == "agent_done")
    assert result.output.startswith(chat_runner.BRAIN_DOWN_NOTICE)
    assert done["grounded"] is False and done["sources"] == []


def test_chat_without_brain_still_gets_the_date(monkeypatch, config):
    seen: dict = {}

    class Recorder(FakeClient):
        def chat(self, **kwargs):
            seen.update(kwargs)
            return "hello!"

    monkeypatch.setattr(chat_runner, "LLMClient", Recorder)
    chat_runner.run_chat("hi", config=config, use_brain=False)
    assert "Current date and time:" in seen["system"]


# ── audit log (real database) ──────────────────────────────────────────

@pytest.mark.real_log
def test_retrieval_log_row_is_written_for_the_account(config):
    try:
        migrate(config)
    except (BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    job = "test-answer-job"
    try:
        answer_question("How do I set a forwarder?", config=config, account_id="u-test", job_id=job,
                        chat=FakeChat("Use it [1]."), searcher=fake_searcher(True))
        with brain_db.connect(config, account_id="u-test") as conn:
            row = conn.execute(
                "SELECT chunk_ids, answered FROM brain.retrieval_log WHERE job_id = %s", (job,)
            ).fetchone()
        with brain_db.connect(config, account_id="someone-else") as conn:
            hidden = conn.execute("SELECT count(*) FROM brain.retrieval_log WHERE job_id = %s", (job,)).fetchone()[0]
        assert row == ([1, 2], True)
        assert hidden == 0
    finally:
        with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
            conn.execute("DELETE FROM brain.retrieval_log WHERE job_id = %s", (job,))


def test_stale_sources_are_found():
    from datetime import datetime, timedelta, timezone

    from src.brain.freshness import stale

    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    checked = {"fresh": now - timedelta(hours=2), "old": now - timedelta(hours=30), "never": None}
    assert stale(checked, 24, now) == ["never", "old"]


def test_refresh_runs_once_per_source(monkeypatch):
    from src.brain import freshness

    started = []
    monkeypatch.setattr(freshness.threading, "Thread",
                        lambda target, name, daemon: type("T", (), {"start": lambda self: started.append(name)})())
    freshness._running.clear()
    assert freshness.refresh_in_background(None, ["dns", "python"]) == ["dns", "python"]
    assert freshness.refresh_in_background(None, ["dns"]) == []          # already running: not twice
    assert len(started) == 1
    freshness._running.clear()
