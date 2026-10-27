"""Auto-clarify — validating the model's ask_clarification tool args into usable questions."""

from __future__ import annotations

from src.llm.assistant import _might_need_clarify, _parse_clarify


def test_might_need_clarify_fires_on_decision_requests():
    assert _might_need_clarify("ask some questions and then tell me what suits")
    assert _might_need_clarify("i want to buy a system for my ai development task")
    assert _might_need_clarify("which GPU should I get")
    assert _might_need_clarify("recommend a laptop")
    assert _might_need_clarify("how do I get into cybersecurity")


def test_might_need_clarify_skips_clear_or_trivial():
    assert not _might_need_clarify("what is the capital of France")
    assert not _might_need_clarify("hi")
    assert not _might_need_clarify("write a poem about the sea")


def test_parses_json_string_args():
    out = _parse_clarify({"questions": '[{"question": "Budget?", "options": ["low", "high"]}]'})
    assert out == [{"question": "Budget?", "options": ["low", "high"]}]


def test_accepts_already_parsed_list():
    out = _parse_clarify({"questions": [{"question": "Scale?", "options": ["hobby", "prod"]}]})
    assert out[0]["question"] == "Scale?"


def test_drops_questions_with_fewer_than_two_options():
    out = _parse_clarify({"questions": [{"question": "Q", "options": ["only one"]}]})
    assert out == []


def test_caps_questions_and_options():
    many = [{"question": f"Q{i}", "options": [f"o{j}" for j in range(8)]} for i in range(6)]
    out = _parse_clarify({"questions": many})
    assert len(out) == 3                 # at most 3 questions
    assert all(len(q["options"]) <= 5 for q in out)   # at most 5 options each


def test_bad_input_is_empty():
    assert _parse_clarify({"questions": "not json"}) == []
    assert _parse_clarify({"questions": 42}) == []
    assert _parse_clarify("nope") == []
    assert _parse_clarify({"questions": [1, "x", {"question": "", "options": ["a", "b"]}]}) == []
