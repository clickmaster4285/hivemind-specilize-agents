"""Chat recall (ISSUES_LOG O63): a long chat's earlier messages, word for word, when the new message needs them.

Chat memory (chat_memory.py) folds a long chat's oldest turns into a summary, and the model never saw them
again: an IP, a command or which camera is on channel 7, dropped by the summary, was gone for the rest of the
chat. Now each finished exchange is also cut into pieces and embedded on the CPU after the answer
(`remember`); once a chat has a summary, the pieces of its folded turns that best match the new message come
back next to the summary (`recall`). They live in brain.chat_passages, where row-level security lets only the
chat's owner read them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from pgvector import Vector

from src.brain.chunking import MAX_TOKENS, chunk_markdown
from src.brain.db import connect
from src.brain.embed import embed_query, embed_texts
from src.brain.retrieve import detect_exact_terms
from src.core.config import Config
from src.core.conversation import QUESTION_CHARS, estimate_tokens

log = logging.getLogger(__name__)

RECALL_TASK = "Given a new message in a chat, retrieve the earlier parts of the same chat that it refers to"
# Of the history budget, kept for recalled pieces once a chat has a summary: 0.25 × 4,300 ≈ 1,075 tokens, two
# pieces of the chunker's ~450-token size, so the recent turns keep three quarters of their room.
RECALL_SHARE = 0.25
# Pieces ranked by meaning in SQL, then re-ranked here with the exact words: a chat folds tens of pieces, so this
# covers the ones that matter without reading a whole long chat back.
CANDIDATES = 20
# The meaning score a recalled piece needs, unless it holds the message's exact identifiers (an IP, a version,
# an error code). Measured 2026-11-14 on bench_recall's chat: the piece that answers a question 0.68–0.80, the
# next best 0.40–0.56 (the old internet speed for "what speed is it now"), unrelated messages ("thanks!", a
# haiku, 15% of 2400) at most 0.35. Between 0.56 and 0.68; recheck with `python -m src.llm.bench_recall`.
MIN_SIMILARITY = 0.6


@dataclass(frozen=True)
class Recalled:
    content: str
    asked_at: datetime
    similarity: float


def recall_budget(history_budget: int) -> int:
    """Tokens of the history budget kept for recalled pieces once a chat has a summary."""
    return int(history_budget * RECALL_SHARE)


def pieces(question: str, answer: str) -> list[str]:
    """One exchange as the pieces to embed. A short exchange stays one piece; a long one is cut by the
    knowledge base's chunker, each answer piece naming the question it answers."""
    question, answer = question.strip(), answer.strip()
    whole = f"User: {question}\n\nAssistant: {answer}"
    if estimate_tokens(whole) <= MAX_TOKENS:
        return [whole]
    subject = question[:QUESTION_CHARS]
    asked = [f"User: {c.content}" for c in chunk_markdown(question)]
    answered = [f'Assistant, answering "{subject}":\n'
                + (f"{c.heading_path}\n" if c.heading_path else "") + c.content for c in chunk_markdown(answer)]
    return asked + answered


def remember(config: Config, *, user_id: int, thread_id: str, job_id: str, asked_at: datetime, question: str,
             answer: str, embed: Callable[..., list[list[float]]] = embed_texts) -> int:
    """Store one finished exchange's pieces; a regenerated answer replaces the job's earlier pieces.
    Returns how many were stored."""
    texts = pieces(question, answer)
    vectors = embed(texts, config)
    with connect(config, account_id=str(user_id)) as conn:
        conn.execute("DELETE FROM brain.chat_passages WHERE job_id = %s", (job_id,))
        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
            conn.execute(
                """INSERT INTO brain.chat_passages (owner_id, thread_id, job_id, piece_index, asked_at, content,
                                                    embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (str(user_id), thread_id, job_id, index, asked_at, text, Vector(vector)))
    return len(texts)


def _holds_all(content: str, terms: Sequence[str]) -> bool:
    lowered = content.lower()
    return bool(terms) and all(term in lowered for term in terms)


def recall(config: Config, *, user_id: int, thread_id: str, job_ids: Sequence[str], message: str, budget: int,
           embed: Callable[..., list[float]] = embed_query) -> list[Recalled]:
    """The pieces of these (folded, live) jobs that best match the new message, within `budget` tokens, in
    the chat's order. A piece holding all the message's exact identifiers comes first, whatever its meaning
    score; the others need MIN_SIMILARITY."""
    if not job_ids or not message.strip() or budget <= 0:
        return []
    vector = Vector(embed(message, config, task=RECALL_TASK))
    with connect(config, account_id=str(user_id)) as conn:
        rows = conn.execute(
            """SELECT content, asked_at, 1 - (embedding <=> %s) AS similarity FROM brain.chat_passages
               WHERE owner_id = %s AND thread_id = %s AND job_id = ANY(%s)
               ORDER BY embedding <=> %s LIMIT %s""",
            (vector, str(user_id), thread_id, list(job_ids), vector, CANDIDATES)).fetchall()
    strong, weak = detect_exact_terms(message)
    terms = [t.lower() for t in strong + weak]
    found = [Recalled(content, asked_at, float(similarity)) for content, asked_at, similarity in rows]
    ranked = sorted(found, key=lambda r: (not _holds_all(r.content, terms), -r.similarity))
    chosen, used = [], 0
    for piece in ranked:
        cost = estimate_tokens(piece.content)
        if used + cost > budget or (piece.similarity < MIN_SIMILARITY and not _holds_all(piece.content, terms)):
            continue
        chosen.append(piece)
        used += cost
    log.info("chat recall: %d of %d pieces (best similarity %.2f)", len(chosen), len(found),
             max((r.similarity for r in found), default=0.0))
    return sorted(chosen, key=lambda r: r.asked_at)


def recall_block(recalled: Sequence[Recalled]) -> str:
    """The recalled pieces as they go under the chat's summary."""
    listed = "\n\n".join(r.content for r in recalled)
    return ("**Earlier messages, word for word** (found for the new message, oldest first; the summary and the "
            f"recent messages are newer: where they differ, those are right):\n\n{listed}")
