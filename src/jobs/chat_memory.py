"""What the model remembers of a chat (BACKLOG L12).

The browser used to send the last few turns, each cut to 6,000 characters, so a long chat silently forgot
its start and a long answer arrived truncated (ISSUES_LOG O9). Now the server rebuilds the memory from the
chat's own jobs, in full, and budgets it in tokens: `history_budget` = CONTEXT_HISTORY_SHARE of LLM_NUM_CTX
(the rest is for instructions, sources and the answer).

When the earlier turns no longer fit, the oldest are folded ONCE into a running summary stored on the chat
(`chat_threads.summary` + `summary_through`), until the verbatim part is down to half the room the summary
leaves (`fold_count`) — so it is not re-summarised every turn. The prompt then gets [summary turn] + the recent turns word for word.
"Summarize & continue in a new chat" summarises the whole chat and starts a new one from that summary.

Once a chat has a summary, the folded turns' original words that best match the new message come back under it
(chat recall, ISSUES_LOG O63, `chat_recall.py`): RECALL_SHARE of the budget is kept free for them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import Config
from src.core.conversation import SUMMARY_ROLE, CHARS_PER_TOKEN, estimate_tokens
from src.jobs import chat_recall
from src.llm.llm_client import LLMClient

log = logging.getLogger(__name__)

SUMMARY_SYSTEM = """You keep the memory of a long chat between a user and an AI assistant. Update the
summary so the chat can continue without the full text. Keep: the user's goals and constraints, decisions
made, key facts, numbers, names, versions, commands, file names, and open questions. Drop small talk and
anything superseded. Write short bullet points under: **Goal**, **Key facts and decisions**, **Open questions**
(leave a heading out when empty). Output only the summary, in the language of the chat."""


def history_budget(config: Config) -> int:
    """Tokens of chat history the prompt may carry."""
    return int(config.llm_num_ctx * config.context_history_share)


def _clip(text: str, tokens: int) -> str:
    """Keep a turn within `tokens`: its start and its end (where conclusions usually are)."""
    limit = tokens * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half].rstrip()}\n…\n{text[-half:].lstrip()}"


def _job_turns(session: Session, job: Any) -> list[dict[str, str]]:
    """One earlier job as chat turns: the question and the assistant's answer (full text, from its events)."""
    from src.core.models_db import JobEvent
    from src.tools.answer_export import answer_in_events

    rows = session.scalars(select(JobEvent.event_json).where(JobEvent.job_id == job.id).order_by(JobEvent.seq)).all()
    found = answer_in_events([json.loads(r) for r in rows])
    turns = [{"role": "user", "content": job.query}] if (job.query or "").strip() else []
    if found:
        turns.append({"role": "assistant", "content": found[0]})
    return turns


def _thread_jobs(session: Session, thread_id: str, user_id: int, *, before: datetime | None,
                 after: datetime | None) -> list[Any]:
    """The chat's live jobs, oldest first, between two times (the summary's end and the current job)."""
    from src.core.models_db import Job

    query = (select(Job).where(Job.user_id == user_id, Job.deleted_at.is_(None),
                               Job.payload_json.contains(thread_id))    # narrows in SQL; checked exactly below
             .order_by(Job.created_at))
    if before is not None:
        query = query.where(Job.created_at < before)
    if after is not None:
        query = query.where(Job.created_at > after)
    return [j for j in session.scalars(query).all()
            if json.loads(j.payload_json or "{}").get("thread_id") == thread_id]


def summarize(previous: str, turns: list[dict[str, str]], *, config: Config, model: str | None = None,
              chat: Callable[..., str] | None = None) -> str:
    """Fold turns into the running summary. Works through them in pieces that fit the model, so a chat of
    any length can be summarised; each piece's result carries into the next. chat: the model call (default the
    app's client; bench_small_models passes its own to compare models)."""
    chat = chat or LLMClient(config).chat
    piece_budget = config.llm_num_ctx // 2                       # the other half: instructions + the summary
    summary, piece, size = previous.strip(), [], 0

    def fold() -> None:
        nonlocal summary, piece, size
        text = "\n\n".join(f"### {t['role'].title()}\n{t['content']}" for t in piece)
        user = (f"## Summary so far\n{summary or '(none yet)'}\n\n## New part of the chat\n{text}\n\n"
                "Write the updated summary.")
        summary = chat(system=SUMMARY_SYSTEM, user=user, model=model, temperature=0.2).strip()
        piece, size = [], 0

    for turn in turns:
        content = _clip(turn["content"], piece_budget // 2)
        cost = estimate_tokens(content)
        if piece and size + cost > piece_budget:
            fold()
        piece.append({"role": turn["role"], "content": content})
        size += cost
    if piece:
        fold()
    return summary


def fold_count(summary_tokens: int, sizes: list[int], budget: int) -> int:
    """How many of the oldest exchanges (`sizes`: tokens each, oldest first) to fold into the summary now: none
    while they fit in the room the summary leaves; else until they fill half that room, always keeping the last
    exchange. Once a chat has a summary, RECALL_SHARE of the budget is also kept for recalled turns (O63).
    Half the ROOM, not half the budget: with an 800-token summary and the recall share, half the budget left
    less than one exchange of slack, and the chat was summarised again after almost every answer (eval v2)."""
    room = budget - summary_tokens - (chat_recall.recall_budget(budget) if summary_tokens else 0)
    if sum(sizes) <= room or len(sizes) < 2:
        return 0
    fold = 0
    while fold < len(sizes) - 1 and sum(sizes[fold:]) > room // 2:
        fold += 1
    return fold


def chat_memory(config: Config, *, job_id: str, fallback: list[dict[str, str]], model: str | None = None,
                on_progress: Callable[[dict], None] | None = None) -> list[dict[str, str]]:
    """The turns to give the model for this job: [summary] + recent turns in full, within the history budget.
    Jobs outside a saved chat (no thread) keep the browser's turns (`fallback`)."""
    from src.core.db import session_scope
    from src.core.models_db import ChatThreadRow, Job

    budget = history_budget(config)
    with session_scope(config) as session:
        job = session.get(Job, job_id)
        thread_id = json.loads(job.payload_json or "{}").get("thread_id") if job else None
        thread = session.get(ChatThreadRow, thread_id) if thread_id else None
        if job is None or thread is None or thread.user_id != job.user_id:
            return fallback
        user_id, message = job.user_id, job.query or ""
        summary = (thread.summary or "").strip()
        earlier = _thread_jobs(session, thread_id, user_id, before=job.created_at, after=None)
        through = thread.summary_through
        folded_ids = [j.id for j in earlier if through is not None and j.created_at <= through]
        jobs = [(j.created_at, j.id, _job_turns(session, j)) for j in earlier
                if through is None or j.created_at > through]

    sizes = [sum(estimate_tokens(t["content"]) for t in turns) for _, _, turns in jobs]
    fold = fold_count(estimate_tokens(summary), sizes, budget)
    if fold:
        folded = [t for _, _, turns in jobs[:fold] for t in turns]
        try:
            summary = summarize(summary, folded, config=config, model=model)
            with session_scope(config) as session:
                row = session.get(ChatThreadRow, thread_id)
                row.summary, row.summary_through = summary, jobs[fold - 1][0]
            if on_progress:
                on_progress({"type": "memory_compacted", "folded": fold})
            folded_ids += [folded_id for _, folded_id, _ in jobs[:fold]]
        except Exception as exc:  # noqa: BLE001 — a failed summary drops the oldest turns instead
            log.warning("chat memory: summary failed, dropping %d old exchanges: %s", fold, exc)
        jobs = jobs[fold:]

    # one turn larger than the whole budget (a huge pasted log, a long document) keeps its start and end
    recent = [{"role": t["role"], "content": _clip(t["content"], budget)} for _, _, turns in jobs for t in turns]
    if summary and folded_ids:
        room = budget - estimate_tokens(summary) - sum(estimate_tokens(t["content"]) for t in recent)
        summary = _with_recall(config, summary, user_id=user_id, thread_id=thread_id, job_ids=folded_ids,
                               message=message, budget=min(chat_recall.recall_budget(budget), room))
    return ([{"role": SUMMARY_ROLE, "content": summary}] if summary else []) + recent


def _with_recall(config: Config, summary: str, *, user_id: int, thread_id: str, job_ids: list[str], message: str,
                 budget: int) -> str:
    """The summary with the folded turns' original words that match the new message under it (O63). Never
    raises: without the brain database or the embedding model, the summary alone."""
    if not config.brain_enabled:
        return summary
    try:
        recalled = chat_recall.recall(config, user_id=user_id, thread_id=thread_id, job_ids=job_ids,
                                      message=message, budget=budget)
    except Exception as exc:  # noqa: BLE001 — recall adds to the memory; the summary still stands
        log.warning("chat recall failed, the summary alone: %s", exc)
        return summary
    return f"{summary}\n\n{chat_recall.recall_block(recalled)}" if recalled else summary


def remember_job(config: Config, job_id: str) -> int:
    """After a job: store its exchange for chat recall (O63). Only a saved chat's job with an answer; returns
    how many pieces were stored."""
    from src.core.db import session_scope
    from src.core.models_db import Job

    if not config.brain_enabled:
        return 0
    with session_scope(config) as session:
        job = session.get(Job, job_id)
        thread_id = json.loads(job.payload_json or "{}").get("thread_id") if job else None
        if job is None or not thread_id:
            return 0
        turns = _job_turns(session, job)
        user_id, asked_at, question = job.user_id, job.created_at, job.query or ""
    answer = next((t["content"] for t in turns if t["role"] == "assistant"), "")
    if not answer.strip():
        return 0
    return chat_recall.remember(config, user_id=user_id, thread_id=thread_id, job_id=job_id, asked_at=asked_at,
                                question=question, answer=answer)


def summarize_chat(config: Config, thread_id: str, user_id: int, *, model: str | None = None) -> str:
    """The whole chat as one summary (its running summary + everything after it) — for "Save summary" and
    "Summarize & continue in a new chat"."""
    from src.core.db import session_scope
    from src.core.models_db import ChatThreadRow

    with session_scope(config) as session:
        thread = session.get(ChatThreadRow, thread_id)
        if thread is None or thread.user_id != user_id:
            raise LookupError("chat not found")
        previous = thread.summary or ""
        turns = [t for j in _thread_jobs(session, thread_id, user_id, before=None, after=thread.summary_through)
                 for t in _job_turns(session, j)]
    if not turns and not previous:
        raise LookupError("this chat has no messages yet")
    return summarize(previous, turns, config=config, model=model) if turns else previous
