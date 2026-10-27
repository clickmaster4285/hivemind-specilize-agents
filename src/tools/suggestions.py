"""Follow-up suggestions: after an answer, offer a few short "ask this next" questions as clickable chips.

One small dedicated model call (the reliable pattern — we don't ask the answer model to tack them on).
Best-effort: returns [] on any problem, so the answer is never blocked.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

SUGGEST_SYSTEM = """Given the user's question and the assistant's answer, suggest {n} short follow-up
questions the user is likely to ask NEXT — natural next steps, a related angle, or a deeper dive.
Each from the user's point of view, SHORT (3-9 words), and genuinely different from each other.

Output ONLY {n} lines, one question per line. No numbering, no preamble, nothing else."""


def generate_suggestions(
    question: str,
    answer: str,
    *,
    client_chat: Callable[..., str],
    model: str,
    n: int = 3,
) -> list[str]:
    """→ up to n short follow-up questions. Empty list on failure (caller just shows no chips)."""
    if not answer.strip():
        return []
    try:
        raw = client_chat(
            system=SUGGEST_SYSTEM.format(n=n),
            user=f"Question: {question}\n\nAnswer: {answer[:1500]}",
            model=model,
            max_tokens=120,
            max_retries=0,
        )
    except Exception as exc:  # noqa: BLE001 — suggestions are optional
        logger.info("suggestion generation failed: %s", exc)
        return []
    out: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        s = line.strip().lstrip("-*•0123456789. ").strip().strip('"')
        if not s or s.lower() in seen or len(s) < 4:
            continue
        seen.add(s.lower())
        out.append(s[:120])
        if len(out) >= n:
            break
    return out
