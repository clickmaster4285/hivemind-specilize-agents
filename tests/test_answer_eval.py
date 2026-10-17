"""B6 answer-quality eval: claim splitting, verdict parsing, facts, summary (no LLM, no database)."""

from __future__ import annotations

from src.brain.answer_eval import (
    Claim, Row, has_fact, load_facts, parse_verdicts, split_claims, summarize, verdict_lines,
)


def test_split_claims_cites_per_sentence_and_per_line():
    text = (
        "DNS uses UDP port 53 for most queries [1]. Large answers fall back to TCP.\n"
        "- Responses go to a random port above 49152 [2]\n"
        "Short one [3]."
    )
    claims, uncited = split_claims(text)
    assert [c.cites for c in claims] == [[1], [1], [2]]   # second sentence inherits its line's [1]
    assert claims[0].text == "DNS uses UDP port 53 for most queries ."
    assert uncited == 0                                    # "Short one" is under 4 words: not a claim


def test_citation_line_under_code_block_backs_the_sentence_above():
    text = (
        "### Add an option\n"
        "Use add_argument with a double dash prefix for long options.\n"
        "```python\nparser.add_argument('--verbose')\n```\n"
        "[1][2]\n"
        "### Next\n"
        "This sentence has no citation at all here.\n"
    )
    claims, uncited = split_claims(text)
    assert len(claims) == 1 and claims[0].cites == [1, 2]
    assert uncited == 1


def test_brackets_in_inline_code_are_not_citations():
    claims, _ = split_claims("With auto() the members get the values `[1, 2, 3]` in order [2].")
    assert claims[0].cites == [2] and "[1, 2, 3]" in claims[0].text


def test_split_claims_skips_tables_headings_and_lead_ins():
    text = "## Ports\n| Port | Use |\n|---|---|\n| 53 | DNS [1] |\nFollow these steps to do it [1]:\n"
    claims, uncited = split_claims(text)
    assert claims == [] and uncited == 0


def test_parse_verdicts_tolerates_formats_and_gaps():
    reply = "1: yes\n2. No — not in source\n3) partially\n"
    assert parse_verdicts(reply, 4) == ["yes", "no", "partly", None]


def test_has_fact_alternatives_case_insensitive():
    assert has_fact("Use logging.basicConfig(filename=...)", "basicConfig|FileHandler")
    assert not has_fact("print it", "basicConfig|FileHandler")


def test_eval_questions_load_facts():
    facts = load_facts()
    assert facts["en-ports"] == ["53"]
    assert all(isinstance(f, str) for fs in facts.values() for f in fs)


def _row(id_, *, oos=False, grounded=True, seconds=10.0, verdicts=(), right=True):
    row = Row(id_, "en", oos, grounded, seconds, "answer", right_source_cited=None if oos else right)
    row.claims = [Claim(text=f"claim {i}", cites=[1], verdict=v) for i, v in enumerate(verdicts)]
    return row


def test_partly_is_listed_but_only_no_fails_an_answer():
    assert _row("a", verdicts=("yes", "partly")).faithful is True
    assert _row("b", verdicts=("yes", "no")).faithful is False
    assert _row("c", verdicts=()).faithful is None


def test_summary_and_verdict_lines():
    rows = [
        _row("ok1", verdicts=("yes", "yes")),
        _row("ok2", verdicts=("yes", "partly"), seconds=12),
        _row("bad", verdicts=("no",), right=False, seconds=25),
        _row("miss", grounded=False),
        _row("oos1", oos=True, grounded=False, seconds=3),
        _row("oos2", oos=True, grounded=True, seconds=4),
    ]
    s = summarize(rows)
    assert s["grounded_answers"] == round(2 / 3, 3)
    assert s["unfaithful"] == ["bad"] and s["not_answered"] == ["miss"] and s["wrong_source"] == ["bad"]
    assert s["claims_partly"] == 1 and s["claims_unsupported"] == 1
    assert s["out_of_scope"] == {"n": 2, "correct": 1, "answered": ["oos2"]}
    assert s["slow"] == ["bad"]
    lines = verdict_lines(s)
    assert lines[0].startswith("FAIL") and lines[1].startswith("FAIL") and lines[2].startswith("PASS")
