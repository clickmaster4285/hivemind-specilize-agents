"""Deep research — sub-query planning, de-duplication, and the knowledge base × live web cross-check
(no LLM / network: the model, web search and knowledge base are faked)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from src.brain.retrieve import Passage as BrainPassage
from src.brain.retrieve import SearchResult
from src.core.config import Config
from src.tools import deep_research as dr
from src.tools.deep_research import _dedupe, plan_subqueries
from src.tools.web_search import Hit, Passage


def test_plan_subqueries_keeps_original_first_and_caps():
    raw = "1. GDDR5 vs HBM bandwidth\n- HBM power efficiency\n* \"HBM 3D stacking\"\nGDDR5 vs HBM bandwidth\n"
    out = plan_subqueries(
        "difference between gddr5 and hbm",
        client_chat=lambda **kw: raw,
        model="m",
        n=4,
    )
    assert out[0] == "difference between gddr5 and hbm"      # original always first
    assert out[1] == "GDDR5 vs HBM bandwidth"                # numbering/bullets stripped
    assert "HBM 3D stacking" in out                           # quotes stripped
    assert len(out) == 4                                      # capped to n
    assert len(out) == len(set(q.lower() for q in out))       # no duplicates (the repeated line dropped)


def test_plan_subqueries_falls_back_to_question_on_empty():
    out = plan_subqueries("what is quic", client_chat=lambda **kw: "", model="m", n=4)
    assert out == ["what is quic"]


def test_plan_subqueries_survives_model_error():
    def boom(**kw):
        raise RuntimeError("model down")

    out = plan_subqueries("topic", client_chat=boom, model="m", n=4)
    assert out == ["topic"]


def test_dedupe_drops_same_page_and_text():
    a = Passage("T", "https://x.com/a", "", "Lorem ipsum dolor sit amet, long enough passage one.")
    a2 = Passage("T", "https://x.com/a", "", "Lorem ipsum dolor sit amet, long enough passage one.")  # dup
    b = Passage("T", "https://y.com/b", "", "A different passage from another site entirely here.")
    out = _dedupe([a, a2, b])
    assert len(out) == 2
    assert [p.url for p in out] == ["https://x.com/a", "https://y.com/b"]


# ── knowledge base × live web cross-check (Deep mode) ──────────────────────
CFG = dataclasses.replace(Config.from_env(), web_search_url="http://searx.test", brain_enabled=True,
                          deep_subqueries=1, deep_max_passages=6)


def _kb(n: int) -> list[BrainPassage]:
    return [BrainPassage(chunk_id=i, document_id=i, title=f"Docker docs {i}", url=f"https://docs.docker.com/{i}",
                         heading_path="Engine", content=f"kb text {i}", token_count=10,
                         updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc), source="docker-engine",
                         publisher="Docker", licence="Apache-2.0", score=1.0) for i in range(n)]


def _run(monkeypatch, *, kb_result, web_pages: int = 8):
    monkeypatch.setattr(dr, "search", lambda q, c: [Hit("t", "https://w.test/a", "s", "e")])
    monkeypatch.setattr(dr, "gather", lambda q, hits, c: [Passage(f"Web {i}", f"https://w{i}.test/p", "", f"web text {i}")
                                                          for i in range(web_pages)])
    monkeypatch.setattr(dr, "kb_search", kb_result)
    seen: dict = {}

    def chat(*, system, user, **kw):
        seen.update(system=system, user=user)
        return "Answer [1][4]."

    text, sources = dr.deep_research("docker default network", CFG, client_chat=lambda **kw: "", chat=chat,
                                     model="m", now="now")
    return seen, sources


def _result(passages, enough=True):
    return lambda *a, **kw: SearchResult(query="q", mode="hybrid", passages=passages, enough_evidence=enough,
                                         best_similarity=0.8, exact_terms=[], exact_match=False)


def test_knowledge_base_joins_deep_research_and_is_cross_checked(monkeypatch):
    seen, sources = _run(monkeypatch, kb_result=_result(_kb(5)))
    kb = [s for s in sources if s["source"] == "docker-engine"]
    web = [s for s in sources if s["source"] == "web"]
    assert len(kb) == 3                                  # at most half the passage budget (6 // 2)
    assert len(web) == 3                                 # the web fills the rest — the context never overflows
    assert [s["n"] for s in sources] == [1, 2, 3, 4, 5, 6]
    assert "KNOWLEDGE BASE, last updated 2026-10-01" in seen["user"] and "LIVE WEB" in seen["user"]
    assert "Knowledge base vs live web" in seen["system"]    # the compare rules are on
    assert kb[0]["cited"] and web[0]["cited"]                # [1] and [4] map to one of each


def test_without_knowledge_base_evidence_deep_research_is_web_only(monkeypatch):
    seen, sources = _run(monkeypatch, kb_result=_result(_kb(5), enough=False))
    assert {s["source"] for s in sources} == {"web"} and len(sources) == 6
    assert "Knowledge base vs live web" not in seen["system"]


def test_a_knowledge_base_outage_never_breaks_deep_research(monkeypatch):
    def down(*a, **kw):
        raise ConnectionError("brain down")

    _seen, sources = _run(monkeypatch, kb_result=down)
    assert {s["source"] for s in sources} == {"web"}
