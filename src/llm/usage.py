"""Token usage of every model call (BACKLOG L11): recorded per call, summed per answer, per chat and per user.

The worker opens `job_usage(job_id, user_id)` around a job; every LLMClient call inside it (any thread that
carries the context) adds one `llm_usage` row. Which part of the app asked is read from the call site
(chat_runner, doc_writer, suggestions …), so no caller has to label its calls. Calls outside a job (evals,
benchmarks) are not recorded. Counting never breaks a model call.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from src.core.config import Config

logger = logging.getLogger(__name__)

_job: ContextVar[tuple[str, int | None] | None] = ContextVar("llm_usage_job", default=None)
_SKIP_MODULES = {__name__, "src.llm.llm_client"}
_PASS_THROUGH = {"<lambda>", "chat"}
CHARS_PER_TOKEN = 4          # only for a call stopped before Ollama sent its counts


@contextmanager
def job_usage(job_id: str, user_id: int | None) -> Iterator[None]:
    """Record the model calls made inside this block against the job and its user."""
    token = _job.set((job_id, user_id))
    try:
        yield
    finally:
        _job.reset(token)


def _caller() -> str:
    """Short module name of the code that made the model call: the first frame outside the client that is
    not a thin pass-through (a lambda or a `chat` wrapper handed to a feature as `client_chat=`)."""
    frame = sys._getframe(1)
    while frame is not None and (frame.f_globals.get("__name__") in _SKIP_MODULES
                                 or frame.f_code.co_name in _PASS_THROUGH):
        frame = frame.f_back
    name = frame.f_globals.get("__name__", "unknown") if frame is not None else "unknown"
    return name.rsplit(".", 1)[-1][:60]


def record_call(config: Config, *, model: str, done: dict[str, Any] | None, started: float,
                prompt_text: str = "", output_text: str = "") -> None:
    """Store one call. `done` is Ollama's final reply (it carries the counts); None = stopped before it came,
    so the tokens are estimated from the text and the time is the wall clock."""
    job = _job.get()
    if job is None:
        return
    from src.core.db import session_scope
    from src.core.models_db import LlmUsage

    counts = done or {}
    estimated = "eval_count" not in counts
    row = LlmUsage(
        job_id=job[0], user_id=job[1], step=_caller(), model=model[:120], estimated=estimated,
        prompt_tokens=int(counts.get("prompt_eval_count") or (len(prompt_text) // CHARS_PER_TOKEN if estimated else 0)),
        output_tokens=int(counts.get("eval_count") or len(output_text) // CHARS_PER_TOKEN),
        duration_ms=int(counts["total_duration"] / 1e6) if counts.get("total_duration")
        else int((time.monotonic() - started) * 1000),
    )
    try:
        with session_scope(config) as session:
            session.add(row)
    except Exception as exc:  # noqa: BLE001 — usage is bookkeeping, never a reason to fail an answer
        logger.debug("llm usage not recorded: %s", exc)


def _cols() -> tuple[Any, ...]:
    from src.core.models_db import LlmUsage

    return (func.count().label("calls"), func.sum(LlmUsage.prompt_tokens).label("prompt"),
            func.sum(LlmUsage.output_tokens).label("output"), func.sum(LlmUsage.duration_ms).label("ms"))


def _sums(rows: list[Any]) -> dict[str, Any]:
    return {"calls": sum(r.calls for r in rows), "prompt_tokens": sum(r.prompt or 0 for r in rows),
            "output_tokens": sum(r.output or 0 for r in rows),
            "seconds": round(sum(r.ms or 0 for r in rows) / 1000, 1)}


def job_totals(session: Session, job_id: str) -> dict[str, Any] | None:
    """One answer's usage: totals and a per-step split (in call order). None when it made no model calls."""
    from src.core.models_db import LlmUsage

    rows = session.execute(
        select(LlmUsage.step, *_cols(), func.max(cast(LlmUsage.estimated, Integer)).label("est"))
        .where(LlmUsage.job_id == job_id).group_by(LlmUsage.step).order_by(func.min(LlmUsage.id))
    ).all()
    if not rows:
        return None
    return {**_sums(rows), "estimated": any(r.est for r in rows),
            "steps": [{"step": r.step, **_sums([r])} for r in rows]}


def summary(session: Session, since: datetime, user_id: int | None = None) -> dict[str, Any]:
    """Usage since a time, per user (everyone, or only `user_id`) and per step, plus the overall total."""
    from src.core.models_db import LlmUsage, User

    scope = [LlmUsage.created_at >= since] + ([LlmUsage.user_id == user_id] if user_id is not None else [])
    biggest = func.sum(LlmUsage.prompt_tokens + LlmUsage.output_tokens).desc()
    per_user = session.execute(
        select(LlmUsage.user_id, User.username, func.count(func.distinct(LlmUsage.job_id)).label("answers"), *_cols())
        .outerjoin(User, User.id == LlmUsage.user_id).where(*scope)
        .group_by(LlmUsage.user_id, User.username).order_by(biggest)
    ).all()
    per_step = session.execute(
        select(LlmUsage.step, *_cols()).where(*scope).group_by(LlmUsage.step).order_by(biggest)
    ).all()
    return {
        "since": since.isoformat(),
        "total": _sums(per_user),
        "users": [{"user_id": r.user_id, "username": r.username or "—", "answers": r.answers, **_sums([r])}
                  for r in per_user],
        "steps": [{"step": r.step, **_sums([r])} for r in per_step],
    }
