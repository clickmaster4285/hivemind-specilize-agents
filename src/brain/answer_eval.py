"""Measure ANSWER quality on the evaluation set — B6 (doc/BRAIN_DESIGN.md §9).

src/brain/evaluate.py checks retrieval (is the right page among the passages?). This runs the real
answer path (answer_question → the chat model) on the same questions and checks what the user sees:

  Grounded answers   every cited claim is backed by the passage it cites — a local judge (the same
                     chat model, temperature 0) reads each claim next to its passages      target ≥ 90%
  Right source cited the answer cites at least one passage from an expected page
  Key facts          optional `facts:` per question; each must appear in the answer ("a|b" = either)
  No evidence        out-of-scope questions get "not found", not a guess                   target 5 / 5
  Latency            question → full answer                                    target ≤ 20 s typical

Usage (from the repo root; Ollama and the brain database must be running):
    python -m src.brain.answer_eval                  all questions (~10-15 min)
    python -m src.brain.answer_eval --limit 10       first 10 questions
    python -m src.brain.answer_eval --ids en-ports py-argparse
    python -m src.brain.answer_eval --no-judge       skip the claim check (faster)
Results are saved to data/brain_eval/results/answers-*.json (not tracked by Git).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

from src.brain.answer import answer_question, cited_numbers
from src.brain.command_check import NOTE_PREFIX
from src.brain.db import BrainDBError
from src.brain.embed import EmbedError
from src.brain.evaluate import QUESTIONS_PATH, RESULTS_DIR, Case, load_cases
from src.brain.retrieve import Passage
# the claim check itself lives in src/brain/verify.py — the chat runs the same code after each answer
from src.brain.verify import (  # noqa: F401 — re-exported for tests/test_answer_eval.py
    CITE_RE, JUDGE_SYSTEM, JUDGE_TEMPERATURE, Claim, _cites, apply_judgement, judge_prompt,
    parse_judgement, parse_verdicts, quote_in, split_claims,
)
from src.config import ROOT, Config
from src.llm_client import LLMClient

TARGET_GROUNDED = 0.90
TARGET_LATENCY_S = 20.0


@dataclass
class Row:
    id: str
    lang: str
    out_of_scope: bool
    grounded: bool                       # answer_question found enough evidence
    seconds: float
    answer: str
    right_source_cited: bool | None = None
    bad_citations: list[int] = field(default_factory=list)   # [n] beyond the passages given
    facts_missing: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    uncited_sentences: int = 0
    unverified_commands: list[str] = field(default_factory=list)   # flagged by src/brain/command_check.py
    error: str | None = None

    @property
    def faithful(self) -> bool | None:
        """True = no judged claim is 'no'; None = nothing judged.

        'partly' is listed for spot-checking but does not fail the answer: on the 2026-10-17 run half of
        the 'partly' verdicts were fully supported when checked by hand (the 9B judge is strict)."""
        judged = [c for c in self.claims if c.verdict]
        if not judged:
            return None
        return all(c.verdict != "no" for c in judged)


def load_facts(path=QUESTIONS_PATH) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(q["id"]): [str(f) for f in q.get("facts") or []] for q in data.get("questions") or []}


def has_fact(answer: str, fact: str) -> bool:
    low = answer.lower()
    return any(alt.strip().lower() in low for alt in fact.split("|") if alt.strip())


def _from_expected_page(passage: Passage, docs: list[str]) -> bool:
    url = (passage.url or "").rstrip("/").lower()
    return any(url.endswith("/" + d.lower()) for d in docs)


def evaluate_case(case: Case, facts: list[str], *, config: Config, llm: LLMClient, judge: bool) -> Row:
    started = time.perf_counter()
    try:
        result = answer_question(case.question, config=config, chat=llm.chat)
    except (BrainDBError, EmbedError):
        raise
    except Exception as exc:  # noqa: BLE001 — one failed question must not end the run
        return Row(case.id, case.lang, case.out_of_scope, False, time.perf_counter() - started, "", error=str(exc))
    row = Row(case.id, case.lang, case.out_of_scope, result.grounded, time.perf_counter() - started, result.text)
    if case.out_of_scope or not result.grounded:
        return row

    passages = result.search.passages if result.search else []
    row.bad_citations = [n for n in _cites(result.text) if not 1 <= n <= len(passages)]
    row.right_source_cited = any(
        _from_expected_page(passages[n - 1], case.docs) for n in cited_numbers(result.text, len(passages))
    )
    row.facts_missing = [f for f in facts if not has_fact(result.text, f)]
    row.unverified_commands = list(result.unverified)
    # the "check before use" note is the command check's output, not a claim of the answer
    body = "\n".join(line for line in result.text.splitlines() if not line.startswith(NOTE_PREFIX))
    claims, row.uncited_sentences = split_claims(body)
    row.claims = [c for c in claims if all(1 <= n <= len(passages) for n in c.cites)]

    if judge and row.claims:
        reply = llm.chat(system=JUDGE_SYSTEM, user=judge_prompt(row.claims, passages),
                         temperature=JUDGE_TEMPERATURE)
        apply_judgement(row.claims, reply, passages)
    return row


def summarize(rows: list[Row]) -> dict[str, Any]:
    inside = [r for r in rows if not r.out_of_scope and not r.error]
    answered = [r for r in inside if r.grounded]
    judged = [r for r in answered if r.faithful is not None]
    claims = [c for r in answered for c in r.claims if c.verdict]
    oos = [r for r in rows if r.out_of_scope and not r.error]
    seconds = sorted(r.seconds for r in rows if not r.error)

    def share(part: int, whole: int) -> float | None:
        return round(part / whole, 3) if whole else None

    return {
        "questions": len(rows),
        "errors": [r.id for r in rows if r.error],
        "in_scope": len(inside),
        "answered_from_sources": share(len(answered), len(inside)),
        "not_answered": [r.id for r in inside if not r.grounded],
        "grounded_answers": share(sum(1 for r in judged if r.faithful), len(judged)),
        # the same, counting every UNSURE claim as wrong (2026-10-19: all 4 unsure claims were real mistakes)
        "grounded_strict": share(sum(1 for r in judged if r.faithful and not any(
            c.verdict == "unsure" for c in r.claims)), len(judged)),
        "unfaithful": [r.id for r in judged if not r.faithful],
        "claims_supported": share(sum(1 for c in claims if c.verdict == "yes"), len(claims)),
        "claims_partly": sum(1 for c in claims if c.verdict == "partly"),
        "claims_unsupported": sum(1 for c in claims if c.verdict == "no"),
        # the judge said "no" but quoted real supporting text: a person decides (not counted as a failure)
        "claims_unsure": sum(1 for c in claims if c.verdict == "unsure"),
        "check_by_hand": sorted({r.id for r in answered for c in r.claims if c.verdict == "unsure"}),
        "right_source_cited": share(sum(1 for r in answered if r.right_source_cited), len(answered)),
        "wrong_source": [r.id for r in answered if not r.right_source_cited],
        "bad_citations": [r.id for r in answered if r.bad_citations],
        "facts_missing": {r.id: r.facts_missing for r in answered if r.facts_missing},
        "uncited_sentences": sum(r.uncited_sentences for r in answered),
        # the judge's evidence really is in the cited passage (share of judged claims)
        "quotes_verified": share(sum(1 for c in claims if c.quote_found), len(claims)),
        "unverified_commands": {r.id: r.unverified_commands for r in answered if r.unverified_commands},
        "out_of_scope": {"n": len(oos), "correct": sum(1 for r in oos if not r.grounded),
                         "answered": [r.id for r in oos if r.grounded]},
        "median_s": round(statistics.median(seconds), 1) if seconds else None,
        "p90_s": round(seconds[int(0.9 * (len(seconds) - 1))], 1) if seconds else None,
        "slow": [r.id for r in rows if r.seconds > TARGET_LATENCY_S],
    }


def verdict_lines(s: dict[str, Any]) -> list[str]:
    """The §9 targets, pass/fail — the B6 report."""
    def mark(ok: bool | None) -> str:
        return "n/a " if ok is None else ("PASS" if ok else "FAIL")

    grounded, strict, oos = s["grounded_answers"], s.get("grounded_strict"), s["out_of_scope"]
    strict_note = f", strict {strict:.0%} if every UNSURE is wrong" if strict is not None and strict != grounded else ""
    return [
        f"{mark(None if grounded is None else grounded >= TARGET_GROUNDED)}  grounded answers     "
        f"{'-' if grounded is None else f'{grounded:.0%}'}  (target ≥ {TARGET_GROUNDED:.0%}{strict_note})",
        f"{mark(None if not oos['n'] else oos['correct'] == oos['n'])}  no-evidence refusals "
        f"{oos['correct']}/{oos['n']}",
        f"{mark(None if s['median_s'] is None else s['median_s'] <= TARGET_LATENCY_S)}  latency (median)     "
        f"{s['median_s']} s  (p90 {s['p90_s']} s, target ≤ {TARGET_LATENCY_S:.0f} s)",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument("--ids", nargs="+", help="only these question ids")
    parser.add_argument("--no-judge", action="store_true", help="skip the claim-by-claim check")
    parser.add_argument("--all", action="store_true", help="also list every 'partly' claim at the end")
    args = parser.parse_args(argv)

    # no background source refreshes during the run: they would compete for the CPU and skew latency
    config = dataclasses.replace(Config.from_env(), brain_fresh_hours=1e9)
    llm = LLMClient(config)
    cases = load_cases()
    if args.ids:
        cases = [c for c in cases if c.id in set(args.ids)]
    if args.limit:
        cases = cases[: args.limit]
    facts = load_facts()

    rows: list[Row] = []
    try:
        for i, case in enumerate(cases, 1):
            row = evaluate_case(case, facts.get(case.id, []), config=config, llm=llm, judge=not args.no_judge)
            rows.append(row)
            if row.error:
                status = f"ERROR {row.error[:60]}"
            elif case.out_of_scope:
                status = "refused" if not row.grounded else "ANSWERED (should refuse)"
            elif not row.grounded:
                status = "NOT ANSWERED"
            else:
                verdicts = [c.verdict or "?" for c in row.claims]
                status = (f"claims {verdicts.count('yes')}/{len(verdicts)} ok"
                          + (f" · {verdicts.count('no')} NO" if "no" in verdicts else "")
                          + (f" · {verdicts.count('unsure')} unsure" if "unsure" in verdicts else "")
                          + ("" if row.right_source_cited else " · WRONG SOURCE")
                          + (f" · missing {row.facts_missing}" if row.facts_missing else "")
                          + (f" · flagged {row.unverified_commands}" if row.unverified_commands else ""))
            print(f"[{i:>2}/{len(cases)}] {case.id:<26} {row.seconds:5.1f}s  {status}", flush=True)
    except (BrainDBError, EmbedError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = summarize(rows)
    print("\n" + "\n".join(verdict_lines(summary)))
    print(f"\nanswered from sources {summary['answered_from_sources']}  ·  right source cited "
          f"{summary['right_source_cited']}  ·  claims supported {summary['claims_supported']} "
          f"(partly {summary['claims_partly']}, unsupported {summary['claims_unsupported']}, "
          f"unsure {summary['claims_unsure']})  ·  "
          f"uncited sentences {summary['uncited_sentences']}  ·  judge quotes verified "
          f"{summary['quotes_verified']}")
    if summary["unverified_commands"]:
        print(f"commands flagged under answers (not in the sources): {summary['unverified_commands']}")
    for key in ("unfaithful", "check_by_hand", "not_answered", "wrong_source", "bad_citations", "slow", "errors"):
        if summary[key]:
            print(f"{key}: {', '.join(summary[key])}")
    if summary["facts_missing"]:
        print(f"facts_missing: {summary['facts_missing']}")
    # the claims to read, with the judge's evidence: NO = failed, UNSURE = judge contradicted itself
    titles = {"no": "NO — not supported by the cited source (these fail the answer)",
              "unsure": "UNSURE — the judge said no but quoted supporting text: decide by hand",
              "partly": "PARTLY — part supported, or the judge's quote was not found in the source"}
    for verdict in ("no", "unsure", "partly") if args.all else ("no", "unsure"):
        listed = [(r, c) for r in rows for c in r.claims if c.verdict == verdict]
        if not listed:
            continue
        print(f"\n{titles[verdict]}  ({len(listed)})")
        for r, c in listed:
            print(f"  {r.id}  {c.text[:150]}  (cites {c.cites})")
            if c.quote:
                found = "" if c.quote_found else "  [quote NOT in source]"
                print(f"      judge's quote: \"{c.quote[:170]}\"{found}")
    if not args.all and summary["claims_partly"]:
        print(f"\n({summary['claims_partly']} 'partly' claims not listed — add --all to see them)")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"answers-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"summary": summary, "rows": [dataclasses.asdict(r) for r in rows]},
                              indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
