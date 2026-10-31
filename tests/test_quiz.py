"""Quiz (R3) — parsing the model's JSON into valid multiple-choice questions (no LLM)."""

from __future__ import annotations

from src.tools.quiz import generate_quiz, wants_quiz


def test_wants_quiz_is_answer_driven_not_query_keywords():
    # a long, structured answer (travel) is quizzable — no "educational" query keyword needed
    travel = (
        "South Korea blends cities and nature. Top places to visit:\n\n"
        "## Seoul\nThe capital, home to Gyeongbokgung palace, Myeongdong street food and N Seoul Tower.\n\n"
        "## Busan\nA coastal city famous for Haeundae Beach and the Gamcheon Culture Village.\n\n"
        "## Jeju Island\nOften called the Hawaii of Korea, with Hallasan volcano and black sand beaches.\n"
    )
    assert wants_quiz(travel) is True
    # bullet-dense factual answer also qualifies (≥4 points, enough substance)
    bullets = "Key facts about the solar system you should remember for later reference and study:\n\n" + (
        "- Mercury is closest to the Sun.\n- Venus is the hottest planet.\n"
        "- Earth supports life.\n- Mars is the red planet.\n- Jupiter is the largest.\n"
    )
    assert wants_quiz(bullets) is True
    # not enough to test: greetings, one-liners, mostly-code
    assert wants_quiz("Hi! How can I help you today?") is False
    assert wants_quiz("The capital of France is Paris.") is False
    assert wants_quiz("## A\n```py\n" + "x = 1\n" * 80 + "```\n## B\n```py\n" + "y = 2\n" * 80 + "```\n") is False


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
