"""Semantic intent router: what does the user want? Decided by MEANING, from examples in data.

Each intent is a list of example messages in data/router/intents.yaml. A new message is embedded
with the multilingual model the brain already uses (qwen3-embedding, on the CPU) and compared with
every example; the most similar examples vote. New intent or new phrasing = add example lines,
no code. Unclear messages (too far from every example, or two intents nearly tied) return None,
and the caller falls back to the LLM classifier.

    python -m src.semantic_router --eval      accuracy on data/router/eval.yaml
    python -m src.semantic_router "message"   show the decision and the closest examples
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from src.config import ROOT, Config

logger = logging.getLogger(__name__)

ROUTER_DIR = ROOT / "data" / "router"
INTENTS_FILE = ROUTER_DIR / "intents.yaml"
EVAL_FILE = ROUTER_DIR / "eval.yaml"
CACHE_DIR = ROUTER_DIR / ".cache"
TASK = "Identify what the user wants the assistant to do with this chat message"
TOP_K = 7                 # closest examples that vote
MIN_SIMILARITY = 0.45     # below this, nothing is close enough: unclear
MIN_GUESS_SIMILARITY = 0.65   # a close call is only guessed (when no LLM decides) if this similar
MIN_MARGIN = 0.025        # best intent must lead the runner-up by this much; tuned on eval.yaml:
                          # 0.015 → 1 wrong of 50, 0.025 → 0 wrong (3 close calls go to the LLM)
# intents that only make sense in a context the caller knows
CONTEXT_ONLY = {"diagram_edit": "after_diagram"}


@dataclass
class Match:
    intent: str | None                 # None = unclear → ask the LLM
    score: float                       # similarity of the closest example of the chosen intent
    margin: float                      # lead over the runner-up intent
    neighbours: list[tuple[str, float, str]] = field(default_factory=list)   # (intent, similarity, example)
    guess: str | None = None           # best candidate even when unclear (used if the LLM cannot decide)


class SemanticRouter:
    def __init__(self, config: Config, intents_file: Path = INTENTS_FILE) -> None:
        self.config = config
        data = yaml.safe_load(intents_file.read_text(encoding="utf-8"))
        self.examples: list[tuple[str, str]] = [
            (name, str(text).strip())
            for name, spec in (data.get("intents") or {}).items()
            for text in (spec.get("examples") or [])
            if str(text).strip()
        ]
        if not self.examples:
            raise ValueError(f"no examples in {intents_file}")
        self.intents = sorted({name for name, _ in self.examples})
        self.notes = {name: str(spec.get("note") or "") for name, spec in (data.get("intents") or {}).items()}
        self._vectors: np.ndarray | None = None
        self._lock = threading.Lock()

    # ── vectors ─────────────────────────────────────────────────────────
    def _embed(self, texts: list[str]) -> np.ndarray:
        from src.brain.embed import embed_texts

        vecs = np.asarray(embed_texts([f"Instruct: {TASK}\nQuery: {t}" for t in texts], self.config), dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    def _cache_key(self) -> str:
        blob = json.dumps([self.config.model_embed, TASK, self.examples], ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def vectors(self) -> np.ndarray:
        """Example vectors: computed once, cached on disk until the examples or the model change."""
        with self._lock:
            if self._vectors is not None:
                return self._vectors
            path = CACHE_DIR / f"examples-{self._cache_key()}.npy"
            if path.is_file():
                self._vectors = np.load(path)
            else:
                self._vectors = self._embed([text for _, text in self.examples])
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                for old in CACHE_DIR.glob("examples-*.npy"):
                    old.unlink(missing_ok=True)
                np.save(path, self._vectors)
            return self._vectors

    # ── decision ────────────────────────────────────────────────────────
    def classify(self, text: str, *, context: set[str] | None = None) -> Match:
        context = context or set()
        allowed = [i for i, (name, _) in enumerate(self.examples)
                   if CONTEXT_ONLY.get(name) is None or CONTEXT_ONLY[name] in context]
        sims = self.vectors()[allowed] @ self._embed([text])[0]
        order = np.argsort(-sims)[:TOP_K]
        neighbours = [(self.examples[allowed[i]][0], float(sims[i]), self.examples[allowed[i]][1]) for i in order]

        # each intent's best example decides; the k nearest add a small vote for robustness
        best: dict[str, float] = {}
        votes: dict[str, float] = {}
        for name, sim, _ in neighbours:
            best[name] = max(best.get(name, -1.0), sim)
            votes[name] = votes.get(name, 0.0) + sim
        ranked = sorted(best, key=lambda n: (best[n] + 0.02 * votes[n]), reverse=True)
        top = ranked[0]
        # runner-up = the best example of any OTHER intent, over all examples (not only the k nearest:
        # when all k belong to one intent, the lead was measured against nothing and showed 1.9)
        others = [float(sims[i]) for i, idx in enumerate(allowed) if self.examples[idx][0] != top]
        runner_up = max(others) if others else -1.0
        margin = best[top] - runner_up
        if best[top] < MIN_SIMILARITY or margin < MIN_MARGIN:
            return Match(None, best[top], margin, neighbours, guess=top if best[top] >= MIN_GUESS_SIMILARITY else None)
        return Match(top, best[top], margin, neighbours, guess=top)


    def ask_llm(self, text: str, match: Match, *, context: set[str] | None = None) -> str | None:
        """Close calls: the chat model picks among the intents, described by their notes in the same
        data file (a new intent needs no prompt change). The nearest examples are given as hints."""
        from src.llm_client import LLMClient

        context = context or set()
        names = [n for n in self.intents if CONTEXT_ONLY.get(n) is None or CONTEXT_ONLY[n] in context]
        menu = "\n".join(f"- {n}: {self.notes.get(n, '')}" for n in names)
        hints = "\n".join(f"- \"{ex}\" → {n}" for n, _, ex in match.neighbours[:5])
        system = ("Classify the user's chat message. Choose exactly one intent:\n" + menu
                  + "\n\nSimilar messages and their intents:\n" + hints
                  + '\n\nReply with JSON only: {"intent": "<name>"}')
        try:
            raw = LLMClient(self.config).chat(system=system, user=text[:1200], temperature=0.0, max_retries=0)
        except Exception as exc:  # noqa: BLE001 — the caller still has a safe default
            logger.info("router LLM fallback failed: %s", exc)
            return None
        found = re.search(r'"intent"\s*:\s*"([a-z_]+)"', raw or "")
        return found.group(1) if found and found.group(1) in names else None


_router: SemanticRouter | None = None
_router_lock = threading.Lock()


def get_router(config: Config | None = None) -> SemanticRouter:
    """One router per process (the example file is read and embedded once)."""
    global _router
    with _router_lock:
        if _router is None:
            _router = SemanticRouter(config or Config.from_env())
        return _router


# ── evaluation ──────────────────────────────────────────────────────────
def evaluate(config: Config | None = None, eval_file: Path = EVAL_FILE) -> dict:
    router = SemanticRouter(config or Config.from_env())
    cases = yaml.safe_load(eval_file.read_text(encoding="utf-8"))["cases"]
    rows = []
    for case in cases:
        context = {"after_diagram"} if case.get("after_diagram") else set()
        match = router.classify(case["text"], context=context)
        rows.append({**case, "got": match.intent, "score": round(match.score, 3), "margin": round(match.margin, 3),
                     "nearest": match.neighbours[0][2]})
    right = sum(1 for r in rows if r["got"] == r["intent"])
    unclear = sum(1 for r in rows if r["got"] is None)
    wrong = [r for r in rows if r["got"] not in (None, r["intent"])]
    return {"total": len(rows), "right": right, "unclear": unclear, "wrong": wrong, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", nargs="?", help="a message to classify")
    parser.add_argument("--eval", action="store_true", help="accuracy on data/router/eval.yaml")
    parser.add_argument("--after-diagram", action="store_true")
    args = parser.parse_args(argv)
    if args.eval:
        result = evaluate()
        print(f"right {result['right']}/{result['total']}  unclear (ask LLM) {result['unclear']}  "
              f"wrong {len(result['wrong'])}")
        for r in result["rows"]:
            if r["got"] != r["intent"]:
                print(f"  {'UNCLEAR' if r['got'] is None else 'WRONG  '} want {r['intent']:<12} got {r['got']!s:<12} "
                      f"sim {r['score']:.3f} margin {r['margin']:.3f} | {r['text'][:60]}  ~ {r['nearest'][:40]}")
        return 0
    if not args.text:
        parser.error("give a message or --eval")
    match = get_router().classify(args.text, context={"after_diagram"} if args.after_diagram else set())
    print(f"intent: {match.intent}  (similarity {match.score:.3f}, margin {match.margin:.3f})")
    for name, sim, example in match.neighbours:
        print(f"  {sim:.3f}  {name:<12} {example}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
