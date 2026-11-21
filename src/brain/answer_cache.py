"""Whole answers reused for repeat questions (BACKLOG L31 step 2; the user's choice, 2026-11-20: the answer exactly as
it was, labelled "Answered earlier", with a "Fresh answer" button — rewording would cost as much as a new answer and
could change a fact).

A grounded answer (web or knowledge base) to a general first question is kept — `keep`, in the background — with the
question's embedding, its sources and its check line. A later first question that means the same gets it before
any routing (`find`), in a fraction of a second instead of 10–45 s:
  - similar enough: SAME_QUESTION (question to question — far stricter than learned.RECALL_MIN_SIMILARITY, which
    only asks whether pages are useful material for a new answer);
  - both questions hold exactly the same identifiers — every token with a digit (a version, an IP, a port, a KB
    or error code) and the search's strong terms (cmdlets, flags) — so "nginx 1.24" never gets the "nginx 1.26"
    answer, and "upgrade nginx" never gets the one written for a single version;
  - young enough for the new question's kind (learned.freshness: news 1 day, "latest"/versions/prices 7, else 90).
Kept only when every source is public and the check found nothing unsupported; nothing about who asked is stored.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from src.brain.db import connect
from src.brain.embed import embed_query
from src.brain.learned import freshness
from src.brain.retrieve import detect_exact_terms
from src.core.config import Config

log = logging.getLogger(__name__)

SAME_QUESTION = 0.92        # cosine similarity of the two questions' embeddings; tune from the logged values
CANDIDATES = 3              # nearest kept questions looked at (the best that passes every rule wins)
QUESTION_CHARS = 2000       # a longer "question" is a pasted document, not something people ask again
# what the chat shows under an answer and a reused one carries along
KEPT_EXTRA = ("role", "grounded", "sources", "verification")

Embed = Callable[[str], list[float]]
# a token with a digit in it: 1.26, 192.168.10.45, 8080, KB5034441, 0x80070005, python3 (the retrieval's own exact
# terms count a bare version as weak — fine for ranking, too loose for handing a finished answer over)
DIGIT_TOKEN_RE = re.compile(r"[\w.:/-]*\d[\w.:/-]*")


# which way a question asks (checklist 2026-11-20: "how do I disable the firewall on ubuntu" is 0.925 like "... enable
# ..." and would have got its answer): each word names a side, and both questions must ask the same sides
DIRECTION_WORDS = {
    "on": ("enable", "enabled", "enabling", "activate", "start", "turn on", "switch on"),
    "off": ("disable", "disabled", "disabling", "deactivate", "stop", "turn off", "switch off"),
    "add": ("install", "installed", "add", "create", "set up", "setup"),
    "remove": ("uninstall", "remove", "removed", "delete", "purge", "get rid of"),
    "allow": ("allow", "permit", "unblock", "whitelist", "open"),
    "block": ("block", "deny", "blacklist", "close"),
    "up": ("increase", "upgrade", "raise", "extend", "enlarge"),
    "down": ("decrease", "downgrade", "reduce", "shrink", "lower"),
    "lock": ("lock", "encrypt", "mount"),
    "unlock": ("unlock", "decrypt", "unmount", "umount"),
    "not": ("not", "no", "never", "without", "can't", "cannot", "won't", "doesn't", "don't", "isn't"),
}
DIRECTION_RE = {side: re.compile(r"(?<![\w'])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w'])", re.I)
                for side, words in DIRECTION_WORDS.items()}


def identifiers(question: str) -> set[str]:
    """What must be the same in two questions for one answer to serve both: the digit tokens, the strong terms and
    which way it asks ("~on" for enable / turn on, "~not" for a negation)."""
    found = {m.group(0).strip(".:/-").lower() for m in DIGIT_TOKEN_RE.finditer(question)}
    asks = {f"~{side}" for side, pattern in DIRECTION_RE.items() if pattern.search(question.replace("’", "'"))}
    return {t for t in found if t} | {t.lower() for t in detect_exact_terms(question)[0]} | asks


def _vector(values: list[float]):
    from pgvector import Vector

    return Vector(values)


def _embed(question: str, config: Config, embed: Embed | None) -> list[float]:
    return (embed or (lambda q: embed_query(q, config)))(question)


def find(question: str, config: Config, *, news: bool = False, embed: Embed | None = None) -> dict[str, Any] | None:
    """The kept answer to this question, or None. → {answer, extra, asked_at, similarity}."""
    question = question.strip()
    if not question or len(question) > QUESTION_CHARS:
        return None
    vector = _vector(_embed(question, config, embed))
    oldest = datetime.now(timezone.utc) - freshness(question, news=news).keep
    wanted = identifiers(question)
    with connect(config) as conn:
        rows = conn.execute(
            """SELECT id, question, answer, extra, created_at, 1 - (embedding <=> %s) AS similarity
               FROM brain.answer_cache WHERE expires_at > now() AND created_at >= %s
               ORDER BY embedding <=> %s LIMIT %s""",
            (vector, oldest, vector, CANDIDATES)).fetchall()
    for row_id, asked, answer, extra, created, similarity in rows:
        if similarity < SAME_QUESTION:
            break                                   # nearest first: the rest are further away
        if identifiers(asked) != wanted:
            continue
        log.info("answer reuse: yes (similarity %.3f, kept %s)", similarity, created.date())
        with connect(config, system=True) as conn:
            conn.execute("UPDATE brain.answer_cache SET hits = hits + 1 WHERE id = %s", (row_id,))
        return {"answer": answer, "extra": extra, "asked_at": created.isoformat(), "similarity": round(similarity, 3)}
    log.info("answer reuse: no (best similarity %.3f)", rows[0][5] if rows else 0.0)
    return None


def keep(question: str, answer: str, extra: dict[str, Any], config: Config, *, news: bool, job_id: str | None,
         embed: Embed | None = None) -> bool:
    """Keep a grounded answer for later repeats; a kept answer to the same question is replaced. Never raises (it
    runs after the answer is out). False when the answer may not be kept."""
    try:
        return _keep(question.strip(), answer, extra, config, news=news, job_id=job_id, embed=embed)
    except Exception as exc:  # noqa: BLE001
        log.warning("keeping the answer for reuse failed: %s", exc)
        return False


def _keep(question: str, answer: str, extra: dict[str, Any], config: Config, *, news: bool, job_id: str | None,
          embed: Embed | None) -> bool:
    sources = extra.get("sources") or []
    if not (question and answer.strip() and extra.get("grounded") and sources) or len(question) > QUESTION_CHARS:
        return False
    if (extra.get("verification") or {}).get("unsupported"):
        return False                                # a statement its sources do not back is not served again
    if not _all_public(config, sources):
        return False                                # someone's private knowledge stays theirs
    fresh = freshness(question, news=news)
    vector = _vector(_embed(question, config, embed))
    kept = {k: extra[k] for k in KEPT_EXTRA if k in extra}
    with connect(config, system=True) as conn:
        conn.execute("DELETE FROM brain.answer_cache WHERE 1 - (embedding <=> %s) >= %s OR expires_at <= now()",
                     (vector, SAME_QUESTION))
        conn.execute(
            """INSERT INTO brain.answer_cache (question, embedding, answer, extra, freshness, job_id, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, now() + %s)""",
            (question, vector, answer, Jsonb(kept), fresh.name, job_id, fresh.keep))
    return True


def _all_public(config: Config, sources: list[dict[str, Any]]) -> bool:
    """Web pages are public; a knowledge-base source must be a public one (not someone's private upload)."""
    slugs = {s.get("source") for s in sources if s.get("source") and s.get("source") != "web"}
    if not slugs:
        return True
    with connect(config) as conn:            # public rows only: a private source is not even visible here
        public = {slug for (slug,) in conn.execute(
            "SELECT slug FROM brain.sources WHERE slug = ANY(%s) AND scope = 'public'", (list(slugs),)).fetchall()}
    return public == slugs
