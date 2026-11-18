"""Scheduled tasks (BACKLOG L26 H1): a question that asks itself again every hour, day or week.

A schedule belongs to one chat. At its time the scheduler thread starts a normal job with the saved question in
that chat (create_job, like a typed message), so the answer lands there with the chat's memory. The job carries
`scheduled: True`: nobody is watching, so the worker offers no tool that waits for a click (operate_server's
confirm card) and never asks clarifying questions (assistant.decide `unattended`).

Times are wall-clock times in the user's own zone ("every day at 08:00" stays 08:00 across daylight saving).
Each due schedule is claimed (its next run moved on) before its job is made, so a crash or a second process never
runs it twice; when the user's own job is still running (MAX_ACTIVE_JOBS_PER_USER), it tries again shortly.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.core.clock import valid_time_zone
from src.core.config import Config
from src.core.db import session_scope
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
            "last_job_id": row.last_job_id, "last_error": row.last_error, "created_at": iso(row.created_at)}


def _own_chat(session, user: User, thread_id: str) -> ChatThreadRow:
    chat = session.get(ChatThreadRow, thread_id)
    if chat is None or chat.user_id != user.id or chat.deleted_at is not None:
        raise ScheduleError("that chat was not found")
    return chat


def create(session, user: User, *, thread_id: str, prompt: str, every: str, at_minute: int,
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
    _own_chat(session, user, thread_id)
    if session.query(ScheduleRow).filter(ScheduleRow.user_id == user.id).count() >= MAX_SCHEDULES_PER_USER:
        raise ScheduleError(f"you already have {MAX_SCHEDULES_PER_USER} schedules; delete one first")
    row = ScheduleRow(user_id=user.id, thread_id=thread_id, prompt=text, every=every, at_minute=at_minute,
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
                        payload={"thread_id": row.thread_id, "scheduled": True, "schedule_id": row.id,
                                 "client_time_zone": row.time_zone})
    except ValueError as exc:                     # busy, queue full, daily quota, refused
        row.last_error = str(exc)
        row.next_run_at = min(as_utc(row.next_run_at), now + timedelta(minutes=RETRY_MINUTES))
        log.info("schedule %s: no run yet (%s)", row.id, exc)
        return False
    row.last_run_at, row.last_job_id, row.last_error = now, job.id, ""
    return True


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
    def loop() -> None:
        time.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                started = run_due(config)
                if started:
                    log.info("started %d scheduled tasks", started)
            except Exception:  # noqa: BLE001 — never let the thread die
                log.exception("task scheduler crashed")
            time.sleep(CHECK_SECONDS)

    thread = threading.Thread(target=loop, name="task-scheduler", daemon=True)
    thread.start()
    return thread
