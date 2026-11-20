"""L5c: a deep-research report is checked against its sources, in groups one judge call can read
(src/brain/verify.py claim_groups, src/jobs/chat_runner.py deep branch). No real model, search or page."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.brain import verify
from src.brain.retrieve import Passage as BrainPassage
from src.brain.retrieve import SearchResult
from src.core.config import Config
from src.jobs import chat_runner
from src.tools import deep_research as dr
from src.tools.web_search import Hit
from src.tools.web_search import Passage as WebPassage


def _page(n: int, chars: int = 200) -> WebPassage:
    return WebPassage(f"Page {n}", f"https://p{n}.test/", "", f"Fact number {n} is true. " + "x" * chars)


def _report(claims: int, sources: int) -> str:
    return "\n".join(f"Fact number {i % sources + 1} is true for this report [{i % sources + 1}]." for i in range(claims))


def _all_yes(**kw) -> str:
    """A judge that confirms every claim it is given, quoting the source sentence."""
    numbers = re.findall(r"^(\d+)\. Fact number (\d+)", kw["user"], re.M)
    return "\n".join(f'{i}: "Fact number {n} is true" => yes' for i, n in numbers)


def test_a_short_answer_is_one_call():
    pages = [_page(n) for n in range(1, 4)]
    claims, _ = verify.split_claims(_report(5, 3))
    assert [len(g) for g in verify.claim_groups(claims, pages)] == [5]


def test_many_claims_are_cut_into_groups_of_twelve():
    pages = [_page(n) for n in range(1, 4)]
    claims, _ = verify.split_claims(_report(30, 3))
    assert [len(g) for g in verify.claim_groups(claims, pages)] == [12, 12, 6]


def test_a_group_never_carries_more_source_text_than_one_call_can_read():
    pages = [_page(n, chars=9_000) for n in range(1, 7)]            # 6 long pages, ~54k characters
    claims, _ = verify.split_claims(_report(6, 6))
    groups = verify.claim_groups(claims, pages)
    for group in groups:
        cited = {n for c in group for n in c.cites}
        assert sum(len(pages[n - 1].text) for n in cited) <= verify.MAX_SOURCE_CHARS
    assert sum(len(g) for g in groups) == 6 and len(groups) == 3


def test_a_long_report_is_judged_group_by_group_and_counted_once():
    pages = [_page(n) for n in range(1, 4)]
    calls, told = [], []
    result = verify.verify_answer(_report(30, 3), pages, chat=lambda **kw: calls.append(kw) or _all_yes(**kw),
                                  on_group=lambda i, n: told.append((i, n)))
    assert len(calls) == 3 and told == [(2, 3), (3, 3)]
    assert result["checked"] == result["supported"] == 30 and result["calls"] == 3


def test_a_stopped_job_ends_the_check_between_groups():
    pages = [_page(n) for n in range(1, 4)]
    calls = []
    result = verify.verify_answer(_report(30, 3), pages, chat=lambda **kw: calls.append(kw) or _all_yes(**kw),
                                  should_continue=lambda: False)
    assert result is None and len(calls) == 1


# --- deep_research hands back what its writer read --------------------------------------------------------------

def _kb(n: int) -> list[BrainPassage]:
    return [BrainPassage(chunk_id=i, document_id=i, title=f"Docs {i}", url=f"https://docs.test/{i}",
                         heading_path="Engine", content=f"kb text {i} " + "k" * 3000, token_count=10,
                         updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc), source="docs", publisher="Docs",
                         licence=None, score=1.0) for i in range(n)]


def test_deep_research_hands_back_its_sources_as_the_writer_saw_them(monkeypatch):
    monkeypatch.setattr(dr, "search", lambda q, c: [Hit("t", "https://w.test/a", "s", "e")])
    monkeypatch.setattr(dr, "gather", lambda q, hits, c: [WebPassage(f"Web {i}", f"https://w{i}.test/p", "", f"web {i}")
                                                          for i in range(4)])
    monkeypatch.setattr(dr, "kb_search", lambda *a, **k: SearchResult(
        query="q", mode="hybrid", passages=_kb(2), enough_evidence=True, best_similarity=0.8, exact_terms=[],
        exact_match=False))
    cfg = replace(Config.from_env(), web_search_url="http://searx.test", brain_enabled=True, deep_subqueries=1,
                  deep_max_passages=6)
    read: list = []
    _text, sources = dr.deep_research("q", cfg, client_chat=lambda **kw: "", chat=lambda **kw: "Report [1][3].",
                                      model="m", now="now", read=read)
    assert [getattr(p, "url") for p in read] == [s["url"] for s in sources]
    assert len(read[0].content) == dr.PASSAGE_CHARS                  # cut like the writer's copy
    assert len(_kb(1)[0].content) > dr.PASSAGE_CHARS                  # the original passage is untouched


# --- the chat runs the check after a Deep report ----------------------------------------------------------------

PAGES = [_page(n) for n in range(1, 4)]
REPORT = _report(14, 3)


class JudgeClient:
    calls: list[dict] = []

    def __init__(self, _config):
        pass

    def chat(self, **kwargs):
        JudgeClient.calls.append(kwargs)
        return _all_yes(**kwargs)


@pytest.fixture
def deep(monkeypatch):
    JudgeClient.calls = []

    def fake_deep(*_a, read=None, **_k):
        if read is not None:
            read[:] = PAGES
        return REPORT, [{"n": n, "title": f"Page {n}", "url": f"https://p{n}.test/", "source": "web", "cited": True}
                        for n in range(1, 4)]

    monkeypatch.setattr(chat_runner, "LLMClient", JudgeClient)
    monkeypatch.setattr(dr, "deep_research", fake_deep)


def _run(**changes):
    cfg = replace(Config.from_env(), web_search_url="http://127.0.0.1:8888", suggestions_enabled=False,
                  quiz_enabled=False, web_images_enabled=False, **changes)
    events: list[dict] = []
    chat_runner.run_chat("compare docker network drivers", config=cfg, deep=True, on_progress=events.append)
    return events, next(e for e in events if e["type"] == "agent_done")


def test_a_deep_report_carries_its_check(deep):
    events, done = _run()
    assert done["role"] == "Deep research"
    assert done["verification"]["checked"] == done["verification"]["supported"] == 14
    assert len(JudgeClient.calls) == 2                                   # 14 claims → 12 + 2
    said = [e.get("message", "") for e in events if e["type"] == "status"]
    assert "Checking the report against its sources…" in said
    assert "Checking the report against its sources (2/2)…" in said


def test_the_deep_check_can_be_switched_off(deep):
    _events, done = _run(verify_deep_answers=False)
    assert "verification" not in done and JudgeClient.calls == []
