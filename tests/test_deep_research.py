"""Deep research helpers — sub-query planning and passage de-duplication (no LLM / network)."""

from __future__ import annotations

from src.tools.deep_research import _dedupe, plan_subqueries
from src.tools.web_search import Passage


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
