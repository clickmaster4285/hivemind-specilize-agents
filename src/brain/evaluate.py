"""Measure retrieval quality on the evaluation set (doc/BRAIN_DESIGN.md §9).

Usage (from the repo root):
    python -m src.brain.evaluate                 compare hybrid vs vector-only vs keyword-only
    python -m src.brain.evaluate --mode hybrid   one mode, with per-question detail

Metrics (in-scope questions):  Recall@8 = right page among the passages handed to the model
                               MRR      = 1 / rank of the first right passage (higher = earlier)
Out-of-scope questions:        correct when search reports "not enough evidence".
Results are saved to data/brain_eval/results/ (not tracked by Git).
Run before and after every change to models, chunking, search or prompts.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.brain.db import BrainDBError
from src.brain.embed import EmbedError, embed_query
from src.brain.retrieve import MAX_PASSAGES, MIN_SIMILARITY, SearchResult, search
from src.core.config import ROOT, Config

QUESTIONS_PATH = ROOT / "data" / "brain_eval" / "questions.yaml"
RESULTS_DIR = ROOT / "data" / "brain_eval" / "results"
MODES = ("hybrid", "vector", "keyword")


@dataclass
class Case:
    id: str
    lang: str
    question: str
    docs: list[str]
    out_of_scope: bool


def load_cases(path: Path = QUESTIONS_PATH) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        Case(
            id=str(q["id"]), lang=str(q.get("lang", "en")), question=str(q["question"]),
            docs=[str(d) for d in q.get("docs") or []], out_of_scope=bool(q.get("out_of_scope")),
        )
        for q in data.get("questions") or []
    ]


def first_hit_rank(result: SearchResult, docs: list[str]) -> int | None:
    """1-based rank of the first passage from an expected page."""
    for rank, passage in enumerate(result.passages, 1):
        url = (passage.url or "").rstrip("/").lower()
        if any(url.endswith("/" + d.lower()) for d in docs):
            return rank
    return None


def run(mode: str, cases: list[Case], config: Config, vectors: dict[str, list[float]]) -> dict[str, Any]:
    rows = []
    for case in cases:
        result = search(
            case.question, config=config, mode=mode,  # type: ignore[arg-type]
            embed=lambda q, _c=case: vectors[_c.id],
        )
        rank = None if case.out_of_scope else first_hit_rank(result, case.docs)
        rows.append({
            "id": case.id, "lang": case.lang, "out_of_scope": case.out_of_scope,
            "rank": rank, "enough_evidence": result.enough_evidence,
            "best_similarity": result.best_similarity, "exact_terms": result.exact_terms,
            "ms": result.timings_ms.get("total_ms"),
            "top": [f"{p.title} › {p.heading_path or ''}" for p in result.passages[:3]],
        })

    def summary(subset: list[dict]) -> dict[str, Any]:
        if not subset:
            return {}
        hits = [r for r in subset if r["rank"]]
        return {
            "n": len(subset),
            "recall_at_8": round(len(hits) / len(subset), 3),
            "mrr": round(sum(1 / r["rank"] for r in hits) / len(subset), 3),
            "missed": [r["id"] for r in subset if not r["rank"]],
            "refused_but_answerable": [r["id"] for r in subset if not r["enough_evidence"]],
        }

    in_scope = [r for r in rows if not r["out_of_scope"]]
    oos = [r for r in rows if r["out_of_scope"]]
    return {
        "mode": mode,
        "all": summary(in_scope),
        "en": summary([r for r in in_scope if r["lang"] == "en"]),
        "hinglish": summary([r for r in in_scope if r["lang"] != "en"]),
        "out_of_scope": {
            "n": len(oos),
            "correctly_refused": sum(1 for r in oos if not r["enough_evidence"]),
            "wrongly_answered": [r["id"] for r in oos if r["enough_evidence"]],
        },
        "median_ms": statistics.median([r["ms"] for r in rows if r["ms"] is not None]),
        "rows": rows,
    }


def _threshold_report(report: dict[str, Any]) -> str:
    """Where in-scope and out-of-scope best similarities fall, to tune MIN_SIMILARITY."""
    rows = report["rows"]
    inside = sorted(r["best_similarity"] for r in rows if not r["out_of_scope"] and r["best_similarity"] is not None)
    outside = sorted(r["best_similarity"] for r in rows if r["out_of_scope"] and r["best_similarity"] is not None)
    if not inside or not outside:
        return ""
    return (
        f"best similarity — in-scope: min {inside[0]:.3f} / median {statistics.median(inside):.3f}   "
        f"out-of-scope: max {outside[-1]:.3f}   (threshold now {MIN_SIMILARITY})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODES, help="run only this mode (shows per-question detail)")
    args = parser.parse_args(argv)
    config = Config.from_env()
    cases = load_cases()
    modes = [args.mode] if args.mode else list(MODES)
    try:
        # embed each question once; every mode reuses the same vector
        vectors = {c.id: embed_query(c.question, config) for c in cases} if modes != ["keyword"] else {}
        reports = [run(mode, cases, config, vectors) for mode in modes]
    except (BrainDBError, EmbedError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"\n{len(cases)} questions · passages per question: {MAX_PASSAGES}\n")
    print("mode      Recall@8   MRR    EN R@8   Hinglish R@8   out-of-scope refused   median ms")
    for r in reports:
        oos = r["out_of_scope"]
        print(
            f"{r['mode']:<9} {r['all']['recall_at_8']:>7.0%}  {r['all']['mrr']:>5.2f}  "
            f"{r['en']['recall_at_8']:>7.0%}  {r['hinglish']['recall_at_8']:>12.0%}   "
            f"{oos['correctly_refused']:>11}/{oos['n']:<10} {r['median_ms']:>9.0f}"
        )
    for r in reports:
        if r["all"]["missed"]:
            print(f"\n[{r['mode']}] missed: {', '.join(r['all']['missed'])}")
        if r["out_of_scope"]["wrongly_answered"]:
            print(f"[{r['mode']}] answered out-of-scope: {', '.join(r['out_of_scope']['wrongly_answered'])}")
        if r["all"]["refused_but_answerable"]:
            print(f"[{r['mode']}] refused although answerable: {', '.join(r['all']['refused_but_answerable'])}")
    hybrid = next((r for r in reports if r["mode"] == "hybrid"), None)
    if hybrid:
        print("\n" + _threshold_report(hybrid))
    if args.mode:
        print()
        for row in reports[0]["rows"]:
            status = "OOS " + ("refused" if not row["enough_evidence"] else "ANSWERED") if row["out_of_scope"] \
                else (f"rank {row['rank']}" if row["rank"] else "MISSED")
            print(f"  {row['id']:<26} {status:<14} sim {row['best_similarity']}  → {row['top'][0] if row['top'] else '-'}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(reports, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
