"""B6 answer-quality eval: claim splitting, verdict parsing, facts, summary (no LLM, no database)."""

from __future__ import annotations

from src.brain.answer_eval import (
    Claim, Row, apply_judgement, has_fact, judge_prompt, load_facts, parse_judgement, parse_verdicts,
    quote_in, split_claims, summarize, verdict_lines,
)
from src.brain.retrieve import Passage


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


def test_parse_judgement_reads_quote_then_verdict():
    reply = '1: "uses UDP port 53" => yes\n2: - => no\n3: “random port above 49152” => partly\n'
    assert parse_judgement(reply, 3) == [("yes", "uses UDP port 53"), ("no", ""),
                                         ("partly", "random port above 49152")]


def _passage(content: str) -> Passage:
    return Passage(chunk_id=1, document_id=1, title="DNS", url=None, heading_path=None, content=content,
                   token_count=10, updated_at=None, source="s", publisher=None, licence=None, score=1.0)


def test_quote_must_really_be_in_the_cited_passage():
    text = "DNS uses **UDP port 53** for most queries; large responses use TCP."
    assert quote_in("uses UDP port 53 for most queries", [text])           # markdown ignored
    assert quote_in("uses UDP port 53 for most querys", [text])            # one typo tolerated
    assert not quote_in("DNS over HTTPS uses port 443", [text])
    assert not quote_in("ok", [text])                                       # too short to prove anything


def test_quote_rules_from_the_2026_10_19_run():
    source = ("DNS also provides a reverse lookup process, enabling clients to use a known IP address during "
              "a name query and to look up a computer name based on its address. Messages are sent over UDP "
              "and DNS servers bind to UDP port 53. The following table lists the ports.")
    # one word changed ("in which" for "enabling") — still the source's sentence
    assert quote_in("DNS also provides a reverse lookup process, enabling clients to use a known IP address "
                    "during a name query and look up a computer name based on its address", [source])
    # pieces joined with "..." are checked one by one
    assert quote_in("Messages are sent over UDP ... DNS servers bind to UDP port 53", [source])
    assert not quote_in("Messages are sent over UDP ... queries use TCP port 853 always", [source])
    # a sentence the judge made up (the claim, reworded) is not evidence
    assert not quote_in("You can use the NRPT to enforce DNSSEC validation for specific namespaces", [source])


def test_no_with_real_supporting_quote_is_unsure_not_a_failure():
    passages = [_passage("Set-DnsServerForwarder -Timeout sets how long to wait for each forwarder.")]
    claims = [Claim("The cmdlet accepts a -Timeout parameter for each forwarder", [1])]
    apply_judgement(claims, '1: "Set-DnsServerForwarder -Timeout sets how long" => no', passages)
    assert claims[0].verdict == "unsure" and claims[0].quote_found is True
    row = Row("x", "en", False, True, 1.0, "a", right_source_cited=True)
    row.claims = claims
    assert row.faithful is True                    # only a real "no" fails an answer
    assert summarize([row])["check_by_hand"] == ["x"]


def test_yes_without_a_real_quote_becomes_partly():
    passages = [_passage("Run python prog.py -v to turn verbosity on."), _passage("Other text.")]
    claims = [Claim("You can run python prog.py -v", [1]), Claim("It uses port 443", [2]),
              Claim("Verbosity can be turned on", [1])]
    reply = '1: "python prog.py -v" => yes\n2: "port 443 is used" => yes\n3: - => no'
    apply_judgement(claims, reply, passages)
    assert [(c.verdict, c.quote_found) for c in claims] == [("yes", True), ("partly", False), ("no", None)]


def test_judge_prompt_shows_only_cited_passages_with_their_numbers():
    passages = [_passage(f"text {i}") for i in range(1, 6)]
    prompt = judge_prompt([Claim("claim about four", [4]), Claim("claim about two", [2, 4])], passages)
    assert '<source id="2">' in prompt and '<source id="4">' in prompt
    assert '<source id="1">' not in prompt and "text 5" not in prompt


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
