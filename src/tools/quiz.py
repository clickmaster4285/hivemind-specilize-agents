"""A short multiple-choice self-check under an educational answer. One dedicated model call → JSON quiz.

Best-effort: returns [] on any problem, so the answer is never blocked.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+\S", re.M)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.M)


def wants_quiz(answer: str) -> bool:
    """Decide — from the ANSWER itself, not a fixed list of query keywords — whether a self-check suits it.
    A quiz needs enough discrete, factual, learnable points to test (several headings or bullet points);
    it doesn't suit a greeting, a one-liner, a refusal, or a mostly-code reply. So the tool self-selects
    for explanations, travel, roadmaps, histories… without a hand-kept list of 'educational' phrases."""
    text = answer or ""
    if len(text.strip()) < 200:                          # too little substance to question
        return False
    body = re.sub(r"```[\s\S]*?```", "", text)            # drop fenced code
    if len(body) < 0.6 * max(1, len(text)):              # mostly code → skip
        return False
    headings = len(_HEADING_RE.findall(body))
    bullets = len(_BULLET_RE.findall(body))
    return headings >= 2 or bullets >= 4                 # enough distinct points to make a few questions


QUIZ_SYSTEM = """Write {n} short multiple-choice questions that test understanding of the KEY points in
the answer below. Each question has 3 options, with the index of the correct one.

Output ONLY a JSON array, nothing else:
[{{"question": "Which slot holds the GPU?", "options": ["SATA", "PCIe x16", "DIMM"], "correct": 1}}]
{n} questions; exactly 3 plausible options each; "correct" is the 0-based index of the right option."""


def generate_quiz(
    question: str,
    answer: str,
    *,
    client_chat: Callable[..., str],
    model: str,
    n: int = 4,
) -> list[dict]:
    """→ [{"question", "options": [...], "correct": int}] (up to n). Empty on any failure."""
    if not answer.strip():
        return []
    try:
        raw = client_chat(
            system=QUIZ_SYSTEM.format(n=n),
            user=f"Question: {question}\n\nAnswer: {answer[:2000]}",
            model=model,
            max_tokens=500,
            max_retries=0,
        )
    except Exception as exc:  # noqa: BLE001 — the quiz is optional
        logger.info("quiz generation failed: %s", exc)
        return []
    match = re.search(r"\[.*\]", raw, re.S)
    try:
        data = json.loads(match.group(0) if match else raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data[:n]:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()
        opts = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()][:4]
        try:
            correct = int(item.get("correct"))
        except (TypeError, ValueError):
            correct = -1
        if q and len(opts) >= 2 and 0 <= correct < len(opts):
            out.append({"question": q[:200], "options": opts, "correct": correct})
    return out
