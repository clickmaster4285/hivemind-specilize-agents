"""Scheduled tasks (BACKLOG L26 H1): a question that asks itself again every hour, day or week.

A schedule has its own chat, made with it ("🔁 <question>"): at its time the scheduler thread starts a normal job
with the saved question there (create_job, like a typed message), so the chat is the schedule's history and each run
sees the earlier ones. "Run now" starts one at once. (The first version answered in the chat it was made from, where
the answers got lost among other messages — ISSUES_LOG O99.) The job carries
`scheduled: True`: nobody is watching, so the worker offers no tool that waits for a click (operate_server's
confirm card) and never asks clarifying questions (assistant.decide `unattended`).

Times are wall-clock times in the user's own zone ("every day at 08:00" stays 08:00 across daylight saving).
Each due schedule is claimed (its next run moved on) before its job is made, so a crash or a second process never
runs it twice; when the user's own job is still running (MAX_ACTIVE_JOBS_PER_USER), it tries again shortly.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
import uuid
import threading
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.core.clock import valid_time_zone
from src.core.config import Config
from src.core.db import session_scope
from src.core.loops import start_loop
from src.core.models_db import USER_ACTIVE, ChatThreadRow, Job, ScheduleRow, User, as_utc

log = logging.getLogger("jobs.schedules")

HOUR, DAY, WEEK = "hour", "day", "week"
EVERY = (HOUR, DAY, WEEK)
MINUTES_IN = {HOUR: 60, DAY: 24 * 60, WEEK: 24 * 60}     # at_minute counts from the hour or from local midnight
DAYS_IN_WEEK = 7
# schedules are set to the minute, so the thread looks once a minute
CHECK_SECONDS = 60
STARTUP_DELAY_SECONDS = 90           # after the API's own start-up work and the brain/SEO threads (60 s, 120 s)
# the user's own question still running blocks a new job (MAX_ACTIVE_JOBS_PER_USER = 1): most finish in minutes
RETRY_MINUTES = 5
# each run is a full job on the one shared GPU; a page of schedules per user, not a cron farm
MAX_SCHEDULES_PER_USER = 20
MAX_PROMPT_CHARS = 4000              # what messages_for keeps of one chat turn
# a run is compared with the last one, so a quiet week is not a full report again (ISSUES_LOG O99, step 2)
OK, QUIET, FAILED = "ok", "quiet", "failed"
NOTHING_NEW = "NOTHING NEW"
COMPARE_CHARS = 6000                 # of each report given to the comparison: an audit's findings fit, a long tail not
COMPARE_MAX_TOKENS = 400             # the "what changed" note: a few bullet lines
COMPARE_SYSTEM = f"""You compare two reports of the same scheduled check: the one before and the one now.
Write ONLY what is new or different now, as short bullet lines: new problems or findings, ones that are fixed or
gone, numbers or states that changed (a version, a count, up/down, a score). Use the report's own names and values.
Ignore wording, order, dates and times of the run itself, and anything the same in both.
If nothing that matters changed, reply exactly: {NOTHING_NEW}"""
SCHEDULED_MARK = "🔁"                 # starts a schedule's chat title, so the sidebar shows which chats run themselves
TITLE_CHARS = 200                    # ChatThreadRow.title


class ScheduleError(ValueError):
    """A schedule that cannot be saved (bad time, unknown chat, too many)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def check_timing(every: str, at_minute: int, weekday: int | None, time_zone: str) -> None:
    if every not in EVERY:
        raise ScheduleError(f"every must be one of {', '.join(EVERY)}")
    if not 0 <= at_minute < MINUTES_IN[every]:
        raise ScheduleError(f"at_minute must be 0–{MINUTES_IN[every] - 1} for every {every}")
    if every == WEEK and (weekday is None or not 0 <= weekday < DAYS_IN_WEEK):
        raise ScheduleError("a weekly schedule needs a weekday 0 (Monday) – 6 (Sunday)")
    if not valid_time_zone(time_zone):
        raise ScheduleError(f"unknown time zone {time_zone!r}")


# when the user names no time: a morning check is the usual ask (research for O99: digests, re-checks before work)
DEFAULT_AT_MINUTE = 9 * 60
TIME_RE = re.compile(r"^\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*$", re.I)
WEEKDAY_NAMES = list(calendar.day_name)        # Monday … Sunday, weekday 0 … 6
WEEKDAY_PREFIX = 3                             # "mon", "tues", "Thursday" all name a day by their first letters


def parse_when(every: str, time_text: str, weekday_text: str) -> tuple[int, int | None]:
    """The model's words for when ("9", "9am", "21:30", "Monday") → (at_minute, weekday). Every hour: minutes past
    the hour from the time ("at :15" → 15). No time: DEFAULT_AT_MINUTE; a weekly one with no day: Monday."""
    if every not in EVERY:
        raise ScheduleError(f"how often: every hour, day or week (got {every!r})")
    if every == HOUR:                                # "15", ":15", "10:15" → 15 minutes past each hour
        numbers = re.findall(r"\d+", time_text or "")
        at = int(numbers[-1]) if numbers else 0
        if at >= MINUTES_IN[HOUR]:
            raise ScheduleError(f"{time_text!r} is not a minute past the hour (0–59)")
    elif not (time_text or "").strip():
        at = DEFAULT_AT_MINUTE
    else:
        m = TIME_RE.match(time_text)
        if not m:
            raise ScheduleError(f"the time {time_text!r} is not a time like 09:00 or 9pm")
        hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if half:
            hour = hour % 12 + (12 if half == "pm" else 0)
        if hour > 23 or minute > 59:
            raise ScheduleError(f"the time {time_text!r} is not a time of day")
        at = hour * 60 + minute
    day = None
    if every == WEEK:
        word = (weekday_text or "").strip().lower()
        day = 0 if not word else next((i for i, name in enumerate(WEEKDAY_NAMES)
                                       if name.lower().startswith(word[:WEEKDAY_PREFIX])), None)
        if day is None:
            raise ScheduleError(f"{weekday_text!r} is not a day of the week")
    return at, day


def describe(every: str, at_minute: int, weekday: int | None) -> str:
    """ "Every Monday at 09:00" — as the Scheduled page says it (frontend/src/lib/schedules.ts)."""
    clock = f"{at_minute // 60:02d}:{at_minute % 60:02d}"
    if every == HOUR:
        return f"Every hour at :{at_minute:02d}"
    if every == WEEK:
        return f"Every {WEEKDAY_NAMES[weekday or 0]} at {clock}"
    return f"Every day at {clock}"


def next_run(every: str, at_minute: int, weekday: int | None, time_zone: str, after: datetime) -> datetime:
    """The first time strictly after `after` (UTC) at which the schedule is due, in UTC."""
    zone = ZoneInfo(time_zone)
    if every == HOUR:
        # the minute past the local hour (India's :00 is :30 in UTC); then hours step in UTC, so a
        # daylight-saving change never skips or repeats a run
        start = after.astimezone(zone).replace(minute=at_minute, second=0, microsecond=0).astimezone(timezone.utc)
        return start if start > after else start + timedelta(hours=1)
    today = after.astimezone(zone).date()
    at = clock_time(at_minute // 60, at_minute % 60)

    def at_day(day: date) -> datetime:
        return datetime.combine(day, at, tzinfo=zone).astimezone(timezone.utc)

    if every == DAY:
        first = at_day(today)
        return first if first > after else at_day(today + timedelta(days=1))
    ahead = (int(weekday or 0) - today.weekday()) % DAYS_IN_WEEK
    first = at_day(today + timedelta(days=ahead))
    return first if first > after else at_day(today + timedelta(days=ahead + DAYS_IN_WEEK))


def to_dict(row: ScheduleRow, chat_title: str = "") -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        return as_utc(value).isoformat() if value else None

    return {"id": row.id, "thread_id": row.thread_id, "chat_title": chat_title, "prompt": row.prompt,
            "every": row.every, "at_minute": row.at_minute, "weekday": row.weekday, "time_zone": row.time_zone,
            "enabled": row.enabled, "next_run_at": iso(row.next_run_at), "last_run_at": iso(row.last_run_at),
            "last_job_id": row.last_job_id, "last_error": row.last_error, "last_status": row.last_status,
            "created_at": iso(row.created_at)}


def create(session, user: User, *, prompt: str, every: str, at_minute: int,
           weekday: int | None, time_zone: str, config: Config | None = None,
           now: datetime | None = None) -> ScheduleRow:
    from src.llm.prompt_filter import BLOCK, check_prompt

    text = prompt.strip()
    if not text:
        raise ScheduleError("the question to repeat is empty")
    if len(text) > MAX_PROMPT_CHARS:
        raise ScheduleError(f"the question is longer than {MAX_PROMPT_CHARS} characters")
    # refused now rather than at 3 a.m. with nobody to read why (create_job checks it again on every run)
    if check_prompt(text, config or Config.from_env()).action == BLOCK:
        raise ScheduleError("that question would be refused by the prompt filter")
    check_timing(every, at_minute, weekday, time_zone)
    if session.query(ScheduleRow).filter(ScheduleRow.user_id == user.id).count() >= MAX_SCHEDULES_PER_USER:
        raise ScheduleError(f"you already have {MAX_SCHEDULES_PER_USER} schedules; delete one first")
    chat = ChatThreadRow(id=str(uuid.uuid4()), user_id=user.id, title=f"{SCHEDULED_MARK} {text}"[:TITLE_CHARS])
    session.add(chat)
    row = ScheduleRow(user_id=user.id, thread_id=chat.id, prompt=text, every=every, at_minute=at_minute,
                      weekday=weekday if every == WEEK else None, time_zone=time_zone,
                      next_run_at=next_run(every, at_minute, weekday, time_zone, now or _now()))
    session.add(row)
    session.flush()
    return row


def own(session, user: User, schedule_id: int) -> ScheduleRow:
    row = session.get(ScheduleRow, schedule_id)
    if row is None or row.user_id != user.id:
        raise KeyError(schedule_id)
    return row


def set_enabled(session, row: ScheduleRow, enabled: bool, now: datetime | None = None) -> ScheduleRow:
    """Switched back on, it waits for its next time: runs missed while off are not made up."""
    if enabled and not row.enabled:
        row.next_run_at = next_run(row.every, row.at_minute, row.weekday, row.time_zone, now or _now())
        row.last_error = ""
    row.enabled = enabled
    return row


def listing(session, user: User) -> list[dict[str, Any]]:
    rows = (session.query(ScheduleRow).filter(ScheduleRow.user_id == user.id)
            .order_by(ScheduleRow.created_at.desc(), ScheduleRow.id.desc()).all())
    titles = {c.id: c.title for c in session.query(ChatThreadRow)
              .filter(ChatThreadRow.id.in_({r.thread_id for r in rows}))} if rows else {}
    return [to_dict(r, titles.get(r.thread_id, "")) for r in rows]


# ── after a run: what changed since the last one (step 2) ──────────────

def _answer(session, job: Job) -> str:
    from src.jobs.chat_memory import _job_turns

    turns = _job_turns(session, job)
    return turns[-1]["content"] if turns and turns[-1]["role"] == "assistant" else ""


def _previous_run(session, row: ScheduleRow, job: Job) -> Job | None:
    """The schedule's last finished run before this one (its own chat's jobs that carry its id)."""
    from src.jobs.chat_memory import _thread_jobs

    earlier = [j for j in _thread_jobs(session, row.thread_id, row.user_id, before=job.created_at, after=None)
               if json.loads(j.payload_json or "{}").get("schedule_id") == row.id and j.status == "succeeded"]
    return earlier[-1] if earlier else None


def compare(before: str, now: str, config: Config) -> str:
    """The model's "what changed" lines, or "" when nothing that matters changed."""
    from src.llm.llm_client import LLMClient

    text = LLMClient(config).chat(
        system=COMPARE_SYSTEM, user=f"BEFORE:\n{before[:COMPARE_CHARS]}\n\nNOW:\n{now[:COMPARE_CHARS]}",
        temperature=0, model=config.model_fast or config.llm_model, max_tokens=COMPARE_MAX_TOKENS).strip()
    return "" if not text or NOTHING_NEW in text.upper() else text


def after_run(config: Config, job_id: str, *, compare_fn: Callable[[str, str, Config], str] | None = None) -> str:
    """A scheduled run has finished: set its schedule's last_status and add a `schedule_result` event to the job —
    failed (always shown), the first run, nothing changed (quiet), or what changed. → the status."""
    from src.jobs.jobs import append_event

    with session_scope(config) as session:
        job = session.get(Job, job_id)
        schedule_id = json.loads(job.payload_json or "{}").get("schedule_id") if job else None
        row = session.get(ScheduleRow, schedule_id) if schedule_id else None
        if job is None or row is None or job.status == "cancelled":
            return ""
        changes, first = "", False
        if job.status != "succeeded":
            status = FAILED
            row.last_error = (job.error or "the run failed")[:500]
        else:
            previous = _previous_run(session, row, job)
            first = previous is None
            if not first:
                changes = (compare_fn or compare)(_answer(session, previous), _answer(session, job), config)
            status = QUIET if not first and not changes else OK
        row.last_status = status
        from src.core import live_events

        live_events.after_commit(session, live_events.SCHEDULES, user_id=row.user_id)
        chat = session.get(ChatThreadRow, row.thread_id)
        if chat is not None and status != QUIET:      # a quiet run tells nobody: that is its point
            chat.unread, chat.updated_at = True, _now()
        append_event(session, job_id, {"type": "schedule_result", "status": status, "first": first,
                                       "changes": changes})
        return status


def unread(session, user: User) -> list[dict[str, Any]]:
    """The user's scheduled chats with a result not opened yet: the browser checks this to show a notification."""
    rows = (session.query(ScheduleRow, ChatThreadRow)
            .join(ChatThreadRow, ChatThreadRow.id == ScheduleRow.thread_id)
            .filter(ScheduleRow.user_id == user.id, ChatThreadRow.unread.is_(True), ChatThreadRow.deleted_at.is_(None))
            .all())
    return [{"schedule_id": s.id, "thread_id": c.id, "title": c.title, "status": s.last_status,
             "job_id": s.last_job_id} for s, c in rows]


# ── the scheduler thread ───────────────────────────────────────────────

def _start(session, row: ScheduleRow, config: Config, now: datetime, start_job: Callable[..., Job]) -> bool:
    """Make the run's job (True), or note why not and stop or retry the schedule (False)."""
    user = session.get(User, row.user_id)
    chat = session.get(ChatThreadRow, row.thread_id)
    if user is None or user.status != USER_ACTIVE or chat is None or chat.deleted_at is not None:
        row.enabled = False                       # nobody left to read the answer: stop, the list says why
        row.last_error = "stopped: the chat was deleted" if user and user.status == USER_ACTIVE \
            else "stopped: the account is not active"
        return False
    try:
        job = start_job(session, user=user, query=row.prompt, mode="auto", config=config,
                        payload=_run_payload(row))
    except ValueError as exc:                     # busy, queue full, daily quota, refused
        row.last_error = str(exc)
        row.next_run_at = min(as_utc(row.next_run_at), now + timedelta(minutes=RETRY_MINUTES))
        log.info("schedule %s: no run yet (%s)", row.id, exc)
        return False
    row.last_run_at, row.last_job_id, row.last_error = now, job.id, ""
    return True


def run_now(session, row: ScheduleRow, config: Config, *, now: datetime | None = None,
            start_job: Callable[..., Job] | None = None) -> Job:
    """"Run now" on the Scheduled page: one run at once; the timetable is not moved."""
    from src.jobs.jobs import create_job

    user, chat = session.get(User, row.user_id), session.get(ChatThreadRow, row.thread_id)
    if chat is None or chat.deleted_at is not None:
        raise ScheduleError("this schedule's chat was deleted")
    try:
        job = (start_job or create_job)(session, user=user, query=row.prompt, mode="auto", config=config,
                                        payload=_run_payload(row))
    except ValueError as exc:                     # busy, queue full, daily quota
        raise ScheduleError(str(exc)) from exc
    row.last_run_at, row.last_job_id, row.last_error = now or _now(), job.id, ""
    return job


def _run_payload(row: ScheduleRow) -> dict[str, Any]:
    return {"thread_id": row.thread_id, "scheduled": True, "schedule_id": row.id, "client_time_zone": row.time_zone}


def run_due(config: Config, *, now: datetime | None = None, start_job: Callable[..., Job] | None = None) -> int:
    """Start every due schedule's job → how many started."""
    from src.jobs.jobs import create_job

    now = now or _now()
    with session_scope(config) as session:        # claim: each due row's next time moves on before anything runs
        due = (session.query(ScheduleRow)
               .filter(ScheduleRow.enabled.is_(True), ScheduleRow.next_run_at <= now)
               .order_by(ScheduleRow.next_run_at).all())
        for row in due:
            row.next_run_at = next_run(row.every, row.at_minute, row.weekday, row.time_zone, now)
        claimed = [row.id for row in due]
    started = 0
    for schedule_id in claimed:
        try:
            with session_scope(config) as session:          # one run's failure never undoes another's job
                row = session.get(ScheduleRow, schedule_id)
                if row is not None and row.enabled:
                    started += _start(session, row, config, now, start_job or create_job)
        except Exception:  # noqa: BLE001 — one schedule failing must not stop the others
            log.exception("schedule %s failed to start", schedule_id)
    return started


def start_scheduler(config: Config) -> threading.Thread:
    def tick() -> None:
        started = run_due(config)
        if started:
            log.info("started %d scheduled tasks", started)

    return start_loop("task-scheduler", tick, every_seconds=CHECK_SECONDS, first_delay=STARTUP_DELAY_SECONDS, log=log)
