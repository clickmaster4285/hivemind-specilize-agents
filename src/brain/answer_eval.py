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
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

from src.brain.answer import answer_question, cited_numbers, format_sources
from src.brain.db import BrainDBError
from src.brain.embed import EmbedError
from src.brain.evaluate import QUESTIONS_PATH, RESULTS_DIR, Case, load_cases
from src.brain.retrieve import Passage
from src.config import ROOT, Config
from src.llm_client import LLMClient

TARGET_GROUNDED = 0.90
TARGET_LATENCY_S = 20.0
MIN_CLAIM_WORDS = 4
JUDGE_TEMPERATURE = 0.0

JUDGE_SYSTEM = """You check whether an answer is faithful to its sources.
You get numbered sources and numbered claims from the answer; each claim lists the sources it cites.
For each claim decide whether the CITED sources state it or directly imply it:
  yes    — the cited sources support the whole claim
  partly — some of it is supported, some is added or changed
  no     — the cited sources do not say this (or say something different)
Judge meaning, not wording: claims may be in Hinglish (Roman-script Hindi mixed with English).
Ignore style, politeness and formatting. Do not use your own knowledge — only the cited sources.
Reply with exactly one line per claim and nothing else, like:
1: yes
2: no"""

VERDICT_RE = re.compile(r"^\s*(\d{1,3})\s*[:.)-]\s*(yes|partly|partial(?:ly)?|no)\b", re.I | re.M)
CITE_RE = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")


@dataclass
class Claim:
    text: str
    cites: list[int]
    verdict: str | None = None          # yes | partly | no (None = not judged)


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


def _cites(text: str) -> list[int]:
    return sorted({int(n) for group in CITE_RE.findall(text) for n in group.split(",")})


# code brackets are hidden while citations are read, then put back in the claim text
_HIDE_BRACKETS = str.maketrans("[]", "\x01\x02")
_SHOW_BRACKETS = str.maketrans("\x01\x02", "[]")


def _plain(line: str) -> str:
    """Markdown markers off a line: list bullets, numbering, bold, inline code ticks.
    Brackets inside inline code are code, not citations (`[1, 2, 3]` is a list)."""
    line = re.sub(r"`([^`]*)`", lambda m: m.group(1).translate(_HIDE_BRACKETS), line)
    line = re.sub(r"^\s*(?:[-*+>]|\d+[.)])\s+", "", line)
    return line.replace("**", "").strip()


def split_claims(text: str) -> tuple[list[Claim], int]:
    """(cited claims, number of uncited factual-looking sentences).

    Code blocks are skipped (they are copied commands, judged through the sentence that cites them).
    A sentence without its own [n] inherits the citations of its line — models often cite once per bullet —
    and a line that is only citations ("[1][2]" under a code block) backs the uncited sentences above it.
    """
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    claims: list[Claim] = []
    pending: list[str] = []             # uncited sentences since the last heading
    uncited = 0
    for raw in text.splitlines():
        line = _plain(raw)
        if raw.lstrip().startswith("#"):
            uncited += len(pending)
            pending = []
        if not line or raw.lstrip().startswith(("#", "|")):
            continue
        line_cites = _cites(line)
        if line_cites and not CITE_RE.sub("", line).strip(" .,;"):
            claims += [Claim(text=s.translate(_SHOW_BRACKETS), cites=line_cites) for s in pending]
            pending = []
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line):
            sentence = sentence.strip()
            if len(re.findall(r"\w+", CITE_RE.sub("", sentence))) < MIN_CLAIM_WORDS or sentence.endswith(":"):
                continue
            cites = _cites(sentence) or line_cites
            if cites:
                claims.append(Claim(text=CITE_RE.sub("", sentence).strip().translate(_SHOW_BRACKETS), cites=cites))
            else:
                pending.append(sentence)
    return claims, uncited + len(pending)


def has_fact(answer: str, fact: str) -> bool:
    low = answer.lower()
    return any(alt.strip().lower() in low for alt in fact.split("|") if alt.strip())


def judge_prompt(claims: list[Claim], passages: list[Passage]) -> str:
    numbered = "\n".join(
        f"{i}. {c.text}  (cites {', '.join(f'[{n}]' for n in c.cites)})" for i, c in enumerate(claims, 1)
    )
    return f"## Sources\n\n{format_sources(passages)}\n\n## Claims\n\n{numbered}"


def parse_verdicts(reply: str, count: int) -> list[str | None]:
    found: dict[int, str] = {}
    for n, word in VERDICT_RE.findall(reply):
        word = word.lower()
        found.setdefault(int(n), "partly" if word.startswith("part") else word)
    return [found.get(i) for i in range(1, count + 1)]


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
    claims, row.uncited_sentences = split_claims(result.text)
    row.claims = [c for c in claims if all(1 <= n <= len(passages) for n in c.cites)]

    if judge and row.claims:
        reply = llm.chat(system=JUDGE_SYSTEM, user=judge_prompt(row.claims, passages),
                         temperature=JUDGE_TEMPERATURE)
        for claim, verdict in zip(row.claims, parse_verdicts(reply, len(row.claims))):
            claim.verdict = verdict
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
        "unfaithful": [r.id for r in judged if not r.faithful],
        "claims_supported": share(sum(1 for c in claims if c.verdict == "yes"), len(claims)),
        "claims_partly": sum(1 for c in claims if c.verdict == "partly"),
        "claims_unsupported": sum(1 for c in claims if c.verdict == "no"),
        "right_source_cited": share(sum(1 for r in answered if r.right_source_cited), len(answered)),
        "wrong_source": [r.id for r in answered if not r.right_source_cited],
        "bad_citations": [r.id for r in answered if r.bad_citations],
        "facts_missing": {r.id: r.facts_missing for r in answered if r.facts_missing},
        "uncited_sentences": sum(r.uncited_sentences for r in answered),
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

    grounded, oos = s["grounded_answers"], s["out_of_scope"]
    return [
        f"{mark(None if grounded is None else grounded >= TARGET_GROUNDED)}  grounded answers     "
        f"{'-' if grounded is None else f'{grounded:.0%}'}  (target ≥ {TARGET_GROUNDED:.0%})",
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
                          + ("" if row.right_source_cited else " · WRONG SOURCE")
                          + (f" · missing {row.facts_missing}" if row.facts_missing else ""))
            print(f"[{i:>2}/{len(cases)}] {case.id:<26} {row.seconds:5.1f}s  {status}", flush=True)
    except (BrainDBError, EmbedError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = summarize(rows)
    print("\n" + "\n".join(verdict_lines(summary)))
    print(f"\nanswered from sources {summary['answered_from_sources']}  ·  right source cited "
          f"{summary['right_source_cited']}  ·  claims supported {summary['claims_supported']} "
          f"(partly {summary['claims_partly']}, unsupported {summary['claims_unsupported']})  ·  "
          f"uncited sentences {summary['uncited_sentences']}")
    for key in ("unfaithful", "not_answered", "wrong_source", "bad_citations", "slow", "errors"):
        if summary[key]:
            print(f"{key}: {', '.join(summary[key])}")
    if summary["facts_missing"]:
        print(f"facts_missing: {summary['facts_missing']}")
    for r in rows:
        for c in r.claims:
            if c.verdict in ("no", "partly"):
                print(f"  {r.id} [{c.verdict}] {c.text[:140]}  (cites {c.cites})")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"answers-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"summary": summary, "rows": [dataclasses.asdict(r) for r in rows]},
                              indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
