"""Options toggle: before writing a full answer, offer a few genuinely different ways to answer the
question (different angle / depth / framing). The user picks one card and that answer is then written.

One small model call; parsed into {title, hint} cards. Best-effort — if parsing yields nothing the
caller falls back to a normal answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

OPTIONS_SYSTEM = """The user asked a question. Offer {n} genuinely DIFFERENT ways to answer it — different
angles, depths, audiences or framings (not the same answer reworded). For each option give a short Title
(2–5 words) and a one-line description of what that answer would cover.

Output ONLY {n} lines, each exactly: Title | one-line description
No numbering, no preamble, no blank lines, nothing else."""


def generate_options(
    question: str,
    *,
    client_chat: Callable[..., str],
    model: str,
    n: int = 3,
) -> list[dict[str, str]]:
    """→ [{"title": ..., "hint": ...}, ...] (up to n). Empty list on failure (caller falls back)."""
    try:
        raw = client_chat(system=OPTIONS_SYSTEM.format(n=n), user=question, model=model,
                          max_tokens=300, max_retries=0)
    except Exception as exc:  # noqa: BLE001 — options are optional; fall back to a normal answer
        logger.info("answer options generation failed: %s", exc)
        return []
    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip().lstrip("-*•0123456789. ").strip()
        if "|" not in line:
            continue
        title, hint = line.split("|", 1)
        title, hint = title.strip().strip('"'), hint.strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        cards.append({"title": title[:60], "hint": hint[:160]})
        if len(cards) >= n:
            break
    return cards
