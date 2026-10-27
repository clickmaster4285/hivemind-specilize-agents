"""L5c: a knowledge-base answer is checked against its sources after it is written (no real model)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.jobs import chat_runner
from src.brain.answer import GroundedAnswer
from src.brain.retrieve import Passage, SearchResult
from src.brain.verify import JUDGE_SYSTEM, verify_answer
from src.core.config import Config


def _passage(n: int, content: str) -> Passage:
    return Passage(chunk_id=n, document_id=n, title=f"Doc {n}", url=f"https://learn.example.com/{n}",
                   heading_path=None, content=content, token_count=40,
                   updated_at=datetime(2025, 5, 15, tzinfo=timezone.utc), source="dns", publisher=None,
                   licence=None, score=1.0)


PASSAGES = [
    _passage(1, "DNS uses UDP port 53 for most queries. Large responses are sent over TCP port 53."),
    _passage(2, "Aging and scavenging are disabled by default."),
]
ANSWER = ("DNS uses UDP port 53 for most queries [1].\n"
          "Large responses switch to TCP port 53 instead [1].\n"
          "Scavenging runs automatically every night on every server [2].")
JUDGE_REPLY = ('1: "DNS uses UDP port 53 for most queries" => yes\n'
               '2: "Large responses are sent over TCP port 53" => yes\n'
               '3: - => no')


def test_verify_answer_counts_and_lists_the_unconfirmed_sentences():
    calls = []
    result = verify_answer(ANSWER, PASSAGES, chat=lambda **kw: calls.append(kw) or JUDGE_REPLY)
    assert result["checked"] == 3 and result["supported"] == 2
    assert [u["text"] for u in result["unsupported"]] == ["Scavenging runs automatically every night on every server ."]
    assert result["unclear"] == []
    assert calls[0]["system"] == JUDGE_SYSTEM and calls[0]["temperature"] == 0.0
    assert '<source id="1">' in calls[0]["user"] and '<source id="2">' in calls[0]["user"]


def test_nothing_to_check_without_cited_sentences():
    assert verify_answer("I could not find this in the sources.", PASSAGES, chat=lambda **kw: "x") is None


class JudgeClient:
    """Stands in for LLMClient inside run_chat: only the judge call reaches it."""
    calls: list[dict] = []
    reply = JUDGE_REPLY

    def __init__(self, _config):
        pass

    def chat(self, **kwargs):
        JudgeClient.calls.append(kwargs)
        if isinstance(JudgeClient.reply, Exception):
            raise JudgeClient.reply
        return JudgeClient.reply


@pytest.fixture
def grounded(monkeypatch):
    JudgeClient.calls, JudgeClient.reply = [], JUDGE_REPLY
    search = SearchResult(query="q", mode="hybrid", passages=PASSAGES, enough_evidence=True,
                          best_similarity=0.8, exact_terms=[], exact_match=False)
    answer = GroundedAnswer(text=ANSWER, grounded=True, sources=[], search=search)
    monkeypatch.setattr(chat_runner, "LLMClient", JudgeClient)
    monkeypatch.setattr(chat_runner, "answer_question", lambda *a, **k: answer)


def _run(config: Config):
    events: list[dict] = []
    # these tests isolate the verification step; follow-up suggestions are a separate model call
    result = chat_runner.run_chat("which ports does DNS use", config=replace(config, suggestions_enabled=False),
                                  use_brain=True, on_progress=events.append)
    return result, events, next(e for e in events if e["type"] == "agent_done")


def test_the_check_runs_after_the_answer_and_is_attached_to_it(grounded):
    result, events, done = _run(replace(Config.from_env(), verify_answers=True))
    assert result.output == ANSWER                                   # the answer itself is unchanged
    assert any(e.get("message") == "Checking the answer against its sources…" for e in events)
    assert done["verification"]["supported"] == 2 and len(done["verification"]["unsupported"]) == 1
    assert "claims" not in done["verification"]                     # the chat gets the summary only
    assert JudgeClient.calls[0]["system"] == JUDGE_SYSTEM


def test_switched_off_no_judge_call(grounded):
    _, _, done = _run(replace(Config.from_env(), verify_answers=False))
    assert "verification" not in done and JudgeClient.calls == []


def test_a_failed_check_never_breaks_the_answer(grounded):
    JudgeClient.reply = RuntimeError("Ollama went away")
    result, _, done = _run(replace(Config.from_env(), verify_answers=True))
    assert result.output == ANSWER and result.errors == []
    assert "verification" not in done
