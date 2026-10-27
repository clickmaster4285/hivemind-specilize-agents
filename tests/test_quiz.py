"""Quiz (R3) — parsing the model's JSON into valid multiple-choice questions (no LLM)."""

from __future__ import annotations

from src.tools.quiz import generate_quiz


def test_parses_valid_quiz():
    raw = '[{"question":"Which slot holds the GPU?","options":["SATA","PCIe x16","DIMM"],"correct":1}]'
    out = generate_quiz("q", "an answer", client_chat=lambda **kw: raw, model="m")
    assert out == [{"question": "Which slot holds the GPU?", "options": ["SATA", "PCIe x16", "DIMM"], "correct": 1}]


def test_drops_bad_items():
    raw = (
        '[{"question":"ok?","options":["a","b"],"correct":0},'
        '{"question":"bad idx","options":["a","b"],"correct":5},'   # out of range
        '{"question":"one opt","options":["a"],"correct":0},'       # <2 options
        '{"question":"","options":["a","b"],"correct":0}]'          # empty question
    )
    out = generate_quiz("q", "answer", client_chat=lambda **kw: raw, model="m")
    assert len(out) == 1 and out[0]["question"] == "ok?"


def test_empty_on_bad_input_or_no_answer():
    assert generate_quiz("q", "   ", client_chat=lambda **kw: "x", model="m") == []
    assert generate_quiz("q", "answer", client_chat=lambda **kw: "not json", model="m") == []
