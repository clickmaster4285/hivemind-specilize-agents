"""Follow-up suggestions — parsing the model's lines into clean chip labels (no LLM)."""

from __future__ import annotations

from src.tools.suggestions import generate_suggestions


def test_parses_strips_noise_dedupes_and_caps():
    raw = '1. How do I start?\n- Which GPU is best?\n"Compare the prices"\nHow do I start?\n\n'
    out = generate_suggestions("q", "a sufficiently long answer", client_chat=lambda **kw: raw, model="m", n=3)
    assert out == ["How do I start?", "Which GPU is best?", "Compare the prices"]


def test_empty_when_no_answer_or_error():
    # no answer → no call, no chips
    assert generate_suggestions("q", "   ", client_chat=lambda **kw: "x\ny\nz", model="m") == []

    def boom(**kw):
        raise RuntimeError("model down")

    assert generate_suggestions("q", "an answer", client_chat=boom, model="m") == []
