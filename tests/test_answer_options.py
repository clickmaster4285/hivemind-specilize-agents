"""Options toggle — parsing the model's 'Title | hint' lines into approach cards (no LLM)."""

from __future__ import annotations

from src.tools.answer_options import generate_options


def test_parses_titles_and_hints_strips_noise_and_caps():
    raw = (
        "Beginner view | simple basics with analogies\n"
        "2. Technical deep-dive | specs and trade-offs\n"
        '"Buying guide" | which to choose for which use\n'
        "Beginner view | duplicate title dropped\n"
        "no pipe here so skipped\n"
    )
    cards = generate_options("q", client_chat=lambda **kw: raw, model="m", n=3)
    assert [c["title"] for c in cards] == ["Beginner view", "Technical deep-dive", "Buying guide"]
    assert cards[0]["hint"] == "simple basics with analogies"       # numbering stripped on line 2
    assert len(cards) == 3                                           # capped to n; dup + no-pipe excluded


def test_empty_on_unparseable_or_error():
    assert generate_options("q", client_chat=lambda **kw: "sorry, no idea", model="m", n=3) == []

    def boom(**kw):
        raise RuntimeError("model down")

    assert generate_options("q", client_chat=boom, model="m", n=3) == []
