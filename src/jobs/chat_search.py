"""Search across all chats (BACKLOG L26 H3, ISSUES_LOG O88): the caller's questions and the assistant's answers.

The sidebar's own filter only sees the titles of the latest chats the browser loaded; this searches every message
the user ever sent and every answer they got, in the database, owner only. A message matches when every word of
the search starts a word in its question or its written answer (the `agent_done` text the user saw — not tool output or
half-streamed text). Each chat appears once, at its newest matching message, with a snippet around the first hit,
and carries its full list of message ids, so the browser can open a chat older than the ones it has loaded.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from src.core.models_db import ChatThreadRow, Job, JobEvent
from src.tools.answer_export import answer_in_events

# a word shorter than this matches almost everything ("a", "i") and is left out of the search
MIN_TERM_CHARS = 2
# a search is a few words; more only slows the query down without narrowing what a person meant
MAX_TERMS = 6
# matching messages read per search (newest first): far more than a results list shows, bounded per request
MAX_CANDIDATE_JOBS = 300
# characters shown around the first hit in a result
SNIPPET_CHARS = 160
DEFAULT_RESULTS = 20
MAX_RESULTS = 50
# what the browser calls a message that has no chat id (jobs from before chats existed) — lib/threads.ts
LEGACY_THREAD_PREFIX = "job-"

_WORD = re.compile(r"\w[\w.\-]*\w|\w", re.UNICODE)
_LIKE_SPECIAL = re.compile(r"([\\%_])")
_ANSWER_EVENT = '%"type": "agent_done"%'      # how json.dumps writes the event type (jobs.append_event)


def search_terms(query: str) -> list[str]:
    """The distinct words of a search, lower-cased, in the order typed."""
    out: list[str] = []
    for word in _WORD.findall(query.lower()):
        if len(word) >= MIN_TERM_CHARS and word not in out:
            out.append(word)
    return out[:MAX_TERMS]


def _like(term: str) -> str:
    """A LIKE pattern for `term` anywhere, its own % _ \\ taken literally (used with escape="\\")."""
    return "%" + _LIKE_SPECIAL.sub(r"\\\1", term) + "%"


def _word_start(term: str) -> re.Pattern[str]:
    """`term` where a word starts ("ip" finds "IP" and "ipconfig", not "zip")."""
    return re.compile(rf"(?<!\w){re.escape(term)}", re.IGNORECASE)


def has_all(text: str, terms: list[str]) -> bool:
    return all(_word_start(t).search(text) for t in terms)


def snippet(text: str, term: str) -> str:
    """One line of `text` around the first `term`, with "…" where it was cut."""
    flat = " ".join(text.split())
    m = _word_start(term).search(flat)
    at = m.start() if m else -1
    if at < 0:
        return flat[:SNIPPET_CHARS] + ("…" if len(flat) > SNIPPET_CHARS else "")
    start = max(0, at - SNIPPET_CHARS // 3)
    end = min(len(flat), start + SNIPPET_CHARS)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def _answer(session: Session, job_id: str) -> str:
    rows = session.scalars(
        select(JobEvent.event_json).where(JobEvent.job_id == job_id, JobEvent.event_json.like(_ANSWER_EVENT))
        .order_by(JobEvent.seq)
    ).all()
    events = []
    for raw in rows:
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    found = answer_in_events(events)
    return found[0] if found else ""


def _thread_of(job: Job) -> str:
    try:
        tid = json.loads(job.payload_json or "{}").get("thread_id")
    except json.JSONDecodeError:
        tid = None
    return str(tid) if tid else f"{LEGACY_THREAD_PREFIX}{job.id}"


def _thread_jobs(session: Session, user_id: int, thread_id: str) -> list[Job]:
    """Every live message of one chat, oldest first."""
    if thread_id.startswith(LEGACY_THREAD_PREFIX):
        job = session.get(Job, thread_id[len(LEGACY_THREAD_PREFIX):])
        return [job] if job is not None and job.user_id == user_id and job.deleted_at is None else []
    rows = session.scalars(
        select(Job)
        .where(Job.user_id == user_id, Job.deleted_at.is_(None), Job.payload_json.like(_like(thread_id), escape="\\"))
        .order_by(Job.created_at)
    ).all()
    return [j for j in rows if _thread_of(j) == thread_id]       # the LIKE only narrows; the payload decides


def search_chats(session: Session, user_id: int, query: str, limit: int = DEFAULT_RESULTS) -> list[dict[str, Any]]:
    """The caller's chats whose messages contain every word of `query`, newest match first (module docstring)."""
    terms = search_terms(query)
    if not terms:
        return []
    limit = max(1, min(limit, MAX_RESULTS))
    # a narrowing pass in SQL (each word somewhere in the question or the message's events), then the exact check
    each_word = [
        or_(Job.query.ilike(_like(t), escape="\\"),
            exists().where(JobEvent.job_id == Job.id, JobEvent.event_json.ilike(_like(t), escape="\\")))
        for t in terms
    ]
    candidates = session.scalars(
        select(Job).where(Job.user_id == user_id, Job.deleted_at.is_(None), *each_word)
        .order_by(Job.created_at.desc()).limit(MAX_CANDIDATE_JOBS)
    ).all()

    meta = {row.id: row for row in session.scalars(select(ChatThreadRow).where(ChatThreadRow.user_id == user_id))}
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in candidates:
        thread_id = _thread_of(job)
        if thread_id in seen:
            continue
        row = meta.get(thread_id)
        if row is not None and row.deleted_at is not None:
            continue
        answer = _answer(session, job.id)
        question = job.query or ""
        if not has_all(f"{question}\n{answer}", terms):
            continue
        seen.add(thread_id)
        jobs = _thread_jobs(session, user_id, thread_id)
        hit_in_question = has_all(question, terms[:1])
        results.append({
            "thread_id": thread_id,
            "title": row.title if row is not None else question.strip()[:200],
            "project_id": row.project_id if row is not None else None,
            "pinned": bool(row.pinned) if row is not None else False,
            "job_ids": [j.id for j in jobs],
            "job_id": job.id,
            "snippet": snippet(question if hit_in_question else answer, terms[0]),
            "in": "question" if hit_in_question else "answer",
            "updated_at": (jobs[-1].created_at if jobs else job.created_at).isoformat(),
        })
        if len(results) >= limit:
            break
    return results
