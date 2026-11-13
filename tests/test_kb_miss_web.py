"""A knowledge-base miss tries the web before the model's memory (src/jobs/chat_runner.py, ISSUES_LOG O50)."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

from src.brain import answer as brain_answer
from src.brain.answer import GroundedAnswer, answer_question
from src.brain.retrieve import SearchResult
from src.core.config import Config
from src.jobs import chat_runner
from src.llm import llm_client
from src.tools.web_search import WebSearchError

WEB_SOURCE = {"n": 1, "title": "News", "url": "https://news.test/a", "heading_path": None, "publisher": "news.test",
              "updated_at": "2026-11-12", "source": "web", "cited": True}


def _config(web: bool) -> Config:
    return replace(Config.from_env(), web_search_url="http://127.0.0.1:8888" if web else "",
                   suggestions_enabled=False, quiz_enabled=False, web_images_enabled=False)


class FakeClient:
    def __init__(self, _config):
        pass

    def chat(self, **_kwargs):
        return "unused"


def _kb_miss(seen: list):
    """answer_question with nothing found: empty when asked to leave the fallback to the caller."""
    def fake(*_a, general_fallback: bool = True, **_k):
        seen.append(general_fallback)
        return GroundedAnswer(text="from memory" if general_fallback else "", grounded=False)
    return fake


def _run(monkeypatch, *, web: bool, web_fails: bool = False) -> tuple[list, dict]:
    seen: list = []

    def fake_web(*_a, **_k):
        if web_fails:
            raise WebSearchError("the web search service is not running")
        return "From the web [1].", [WEB_SOURCE]

    monkeypatch.setattr(chat_runner, "LLMClient", FakeClient)
    monkeypatch.setattr(chat_runner, "answer_question", _kb_miss(seen))
    monkeypatch.setattr(chat_runner, "answer_from_web", fake_web)
    events: list[dict] = []
    chat_runner.run_chat("latest news about the nvidia ceo", config=_config(web), use_brain=True,
                         on_progress=events.append)
    return seen, next(e for e in events if e["type"] == "agent_done")


def test_a_miss_is_answered_from_the_web_with_sources(monkeypatch):
    seen, done = _run(monkeypatch, web=True)
    assert seen == [False]                                  # no memory answer was written first
    assert done["role"] == "Web search" and done["output"] == "From the web [1]."
    assert done["grounded"] and done["sources"] == [WEB_SOURCE]


def test_without_web_search_the_memory_answer_stays(monkeypatch):
    seen, done = _run(monkeypatch, web=False)
    assert seen == [True] and done["role"] == "General knowledge" and done["output"] == "from memory"


def test_a_failed_web_search_falls_back_to_memory(monkeypatch):
    seen, done = _run(monkeypatch, web=True, web_fails=True)
    assert seen == [False, True] and done["role"] == "General knowledge" and done["output"] == "from memory"


def test_answer_question_without_fallback_skips_the_model(monkeypatch):
    monkeypatch.setattr(brain_answer, "_log_retrieval", lambda *a, **k: None)
    calls: list = []

    def searcher(question, **_k):
        return SearchResult(query=question, mode="hybrid", passages=[], enough_evidence=False, best_similarity=0.2,
                            exact_terms=[], exact_match=False)

    result = answer_question("latest nvidia news", config=Config.from_env(), chat=lambda **kw: calls.append(kw),
                             searcher=searcher, general_fallback=False)
    assert result.text == "" and not result.grounded and calls == []


def test_an_empty_tool_choice_reply_logs_why_it_ended(monkeypatch, caplog):
    lines = [json.dumps({"message": {"content": "", "thinking": "hmm"}, "done": False}).encode(),
             json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 3}).encode()]

    class Stream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def raise_for_status(self):
            pass

        def iter_lines(self):
            return iter(lines)

    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **k: Stream())
    with caplog.at_level(logging.WARNING, logger="src.llm.llm_client"):
        text, calls = llm_client.LLMClient(Config.from_env()).chat_tools(
            messages=[{"role": "user", "content": "q"}], tools=[])
    assert text == "" and calls == []
    assert "done_reason=stop output_tokens=3 thinking_chars=3" in caplog.text
