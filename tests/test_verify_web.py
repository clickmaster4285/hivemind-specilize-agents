"""L5c: a web-search answer is checked against the pages it was written from, like a knowledge-base answer
(src/jobs/chat_runner.py checked_web, src/brain/verify.py). No real model, search or page."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.brain.answer import GroundedAnswer
from src.brain.verify import verify_answer
from src.core.config import Config
from src.jobs import chat_runner
from src.tools.web_search import Passage as WebPassage

PAGES = [
    WebPassage("Nvidia news", "https://news.test/a", "Results", "Nvidia reported revenue of 57 billion dollars "
               "for the quarter, up 62 percent from a year earlier.", "2026-11-19"),
    WebPassage("Chip blog", "https://blog.test/b", "", "The company said demand for its Blackwell chips was "
               "far above supply.", None),
]
ANSWER = ("Nvidia reported revenue of 57 billion dollars for the quarter [1].\n"
          "The company plans to build a new factory in Texas next year [2].")
JUDGE_REPLY = ('1: "Nvidia reported revenue of 57 billion dollars for the quarter" => yes\n'
               '2: - => no')
SOURCES = [{"n": 1, "title": "Nvidia news", "url": "https://news.test/a", "source": "web", "cited": True},
           {"n": 2, "title": "Chip blog", "url": "https://blog.test/b", "source": "web", "cited": True}]


def test_a_web_page_is_judged_by_its_text_and_section():
    calls: list[dict] = []
    result = verify_answer(ANSWER, PAGES, chat=lambda **kw: calls.append(kw) or JUDGE_REPLY)
    assert result["checked"] == 2 and result["supported"] == 1
    assert [u["text"] for u in result["unsupported"]] == [
        "The company plans to build a new factory in Texas next year ."]
    prompt = calls[0]["user"]
    assert "Section: Results" in prompt and "57 billion dollars" in prompt and "Blackwell" in prompt


def test_a_quote_not_on_the_page_does_not_count_as_support():
    reply = '1: "Nvidia earned a record 90 billion in one quarter" => yes\n2: - => no'
    result = verify_answer(ANSWER, PAGES, chat=lambda **kw: reply)
    assert result["supported"] == 0 and result["unclear"][0]["verdict"] == "partly"


class JudgeClient:
    """Stands in for LLMClient inside run_chat: only the judge call reaches it."""
    calls: list[dict] = []

    def __init__(self, _config):
        pass

    def chat(self, **kwargs):
        JudgeClient.calls.append(kwargs)
        return JUDGE_REPLY


def _fake_web(*_a, read=None, **_k):
    if read is not None:
        read[:] = PAGES
    return ANSWER, SOURCES


@pytest.fixture
def web(monkeypatch):
    JudgeClient.calls = []
    monkeypatch.setattr(chat_runner, "LLMClient", JudgeClient)
    monkeypatch.setattr(chat_runner, "answer_from_web", _fake_web)


def _config(**changes) -> Config:
    return replace(Config.from_env(), web_search_url="http://127.0.0.1:8888", suggestions_enabled=False,
                   quiz_enabled=False, web_images_enabled=False, **changes)


def _done(config: Config, **kwargs) -> dict:
    events: list[dict] = []
    chat_runner.run_chat("nvidia results this quarter", config=config, on_progress=events.append, **kwargs)
    return next(e for e in events if e["type"] == "agent_done")


def test_a_web_answer_carries_its_check(web):
    done = _done(_config(), web_search=True)
    assert done["role"] == "Web search" and done["output"] == ANSWER
    v = done["verification"]
    assert v["checked"] == 2 and v["supported"] == 1 and len(v["unsupported"]) == 1
    assert "claims" not in v                       # the chat gets counts and the unconfirmed sentences only
    assert len(JudgeClient.calls) == 1 and JudgeClient.calls[0]["temperature"] == 0.0


def test_a_knowledge_base_miss_answered_from_the_web_is_checked_too(web, monkeypatch):
    monkeypatch.setattr(chat_runner, "answer_question",
                        lambda *_a, general_fallback=True, **_k: GroundedAnswer(text="", grounded=False))
    done = _done(_config(), use_brain=True)
    assert done["role"] == "Web search" and done["verification"]["supported"] == 1


def test_the_web_check_can_be_switched_off(web):
    done = _done(_config(verify_web_answers=False), web_search=True)
    assert "verification" not in done and JudgeClient.calls == []


def test_a_web_answer_without_sources_is_not_checked(web, monkeypatch):
    monkeypatch.setattr(chat_runner, "answer_from_web", lambda *_a, **_k: ("The web search found nothing.", []))
    done = _done(_config(), web_search=True)
    assert "verification" not in done and JudgeClient.calls == []


def test_answer_from_web_hands_back_the_pages_it_numbered(monkeypatch):
    from types import SimpleNamespace

    from src.tools import web_search as ws

    monkeypatch.setattr(ws, "search", lambda q, c, news=False: [ws.Hit("Page", "https://news.test/a", "s", "bing", None)])
    monkeypatch.setattr(ws, "gather", lambda q, hits, c: list(PAGES))
    read: list = []
    text, sources = ws.answer_from_web("q", SimpleNamespace(web_search_url="http://x"), model="m", now="now",
                                       chat=lambda **kw: ANSWER, read=read)
    assert [p.url for p in read] == [s["url"] for s in sources] == ["https://news.test/a", "https://blog.test/b"]
