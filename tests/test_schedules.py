"""Scheduled tasks (BACKLOG L26 H1): times in the user's zone, claim-then-run, unattended runs — offline, SQLite."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.config import Config
from src.core.db import Base
from src.core.models_db import USER_DISABLED, ChatThreadRow, Job, ScheduleRow, User, as_utc
from src.jobs import schedules
from src.jobs.schedules import DAY, HOUR, RETRY_MINUTES, WEEK, ScheduleError, next_run
from src.llm import assistant
from src.llm.router import Route

UTC = timezone.utc


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


# ── when a schedule is due ─────────────────────────────────────────────

def test_a_daily_time_is_today_until_it_has_passed_then_tomorrow():
    # Karachi is UTC+5: 08:00 there = 03:00 UTC
    assert next_run(DAY, 8 * 60, None, "Asia/Karachi", _utc(2026, 11, 18, 2, 0)) == _utc(2026, 11, 18, 3, 0)
    assert next_run(DAY, 8 * 60, None, "Asia/Karachi", _utc(2026, 11, 18, 3, 0)) == _utc(2026, 11, 19, 3, 0)


def test_a_daily_time_stays_on_the_wall_clock_across_daylight_saving():
    # New York leaves daylight saving on Sunday 2026-11-01: 08:00 is 12:00 UTC before, 13:00 UTC after
    assert next_run(DAY, 8 * 60, None, "America/New_York", _utc(2026, 10, 31, 13, 0)) == _utc(2026, 11, 1, 13, 0)


def test_hourly_runs_keep_the_local_minute_in_half_hour_zones():
    # India is UTC+5:30: "every hour at :00" is :30 in UTC
    assert next_run(HOUR, 0, None, "Asia/Kolkata", _utc(2026, 11, 18, 10, 31)) == _utc(2026, 11, 18, 11, 30)
    assert next_run(HOUR, 15, None, "UTC", _utc(2026, 11, 18, 10, 15)) == _utc(2026, 11, 18, 11, 15)


def test_weekly_finds_the_named_day():
    wednesday = _utc(2026, 11, 18, 12, 0)                      # 2026-11-18 is a Wednesday
    assert next_run(WEEK, 9 * 60, 0, "UTC", wednesday) == _utc(2026, 11, 23, 9, 0)      # next Monday
    assert next_run(WEEK, 13 * 60, 2, "UTC", wednesday) == _utc(2026, 11, 18, 13, 0)    # later today
    assert next_run(WEEK, 11 * 60, 2, "UTC", wednesday) == _utc(2026, 11, 25, 11, 0)    # today's has passed


@pytest.mark.parametrize("every, at_minute, weekday, zone, error", [
    ("month", 0, None, "UTC", "every must be"),
    (HOUR, 60, None, "UTC", "at_minute must be 0–59"),
    (DAY, 24 * 60, None, "UTC", "at_minute must be 0–1439"),
    (WEEK, 0, None, "UTC", "needs a weekday"),
    (WEEK, 0, 7, "UTC", "needs a weekday"),
    (DAY, 0, None, "Mars/Olympus", "unknown time zone"),
])
def test_impossible_timings_are_refused(every, at_minute, weekday, zone, error):
    with pytest.raises(ScheduleError, match=error):
        schedules.check_timing(every, at_minute, weekday, zone)


# ── saving and running ─────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add_all([User(id=1, username="ana", password_hash="x"), User(id=2, username="bilal", password_hash="x"),
                     ChatThreadRow(id="chat-ana", user_id=1, title="Morning news"),
                     ChatThreadRow(id="chat-bilal", user_id=2, title="Bilal's chat")])
    session.flush()

    @contextmanager
    def scope(_config=None):
        yield session
        session.flush()

    monkeypatch.setattr(schedules, "session_scope", scope)
    yield session
    session.close()


@pytest.fixture
def config():
    return replace(Config.from_env(), prompt_filter_llm=False)      # the filter's rules only: no model call


def _add(db, config, *, user_id=1, thread_id="chat-ana", now=_utc(2026, 11, 18, 2, 0), **timing):
    timing = {"every": DAY, "at_minute": 8 * 60, "weekday": None, "time_zone": "Asia/Karachi", **timing}
    return schedules.create(db, db.get(User, user_id), thread_id=thread_id, prompt="  Top tech news today  ",
                            config=config, now=now, **timing)


def test_a_schedule_belongs_to_one_of_the_users_own_chats(db, config):
    row = _add(db, config)
    assert (row.prompt, row.weekday, as_utc(row.next_run_at)) == ("Top tech news today", None, _utc(2026, 11, 18, 3))
    with pytest.raises(ScheduleError, match="chat was not found"):
        _add(db, config, thread_id="chat-bilal")                # someone else's chat
    with pytest.raises(KeyError):
        schedules.own(db, db.get(User, 2), row.id)              # nor can they see or change mine
    assert [s["chat_title"] for s in schedules.listing(db, db.get(User, 1))] == ["Morning news"]
    assert schedules.listing(db, db.get(User, 2)) == []


def test_a_user_has_a_limited_number_of_schedules(db, config, monkeypatch):
    monkeypatch.setattr(schedules, "MAX_SCHEDULES_PER_USER", 2)
    _add(db, config)
    _add(db, config, every=HOUR, at_minute=0)
    with pytest.raises(ScheduleError, match="already have 2"):
        _add(db, config)


class _Starts:
    """Stands in for create_job: records each call, or refuses like a busy user."""

    def __init__(self, refuse: str = ""):
        self.calls: list[dict] = []
        self.refuse = refuse

    def __call__(self, session, *, user, query, mode, payload, config):
        if self.refuse:
            raise ValueError(self.refuse)
        self.calls.append({"user": user.id, "query": query, "payload": payload})
        job = Job(id=f"job-{len(self.calls)}", user_id=user.id, mode=mode, status="queued", query=query,
                  payload_json=json.dumps(payload))
        session.add(job)
        return job


def test_a_due_schedule_starts_one_job_in_its_chat_and_moves_on(db, config):
    row = _add(db, config)
    starts = _Starts()
    assert schedules.run_due(config, now=_utc(2026, 11, 18, 2, 59), start_job=starts) == 0      # not yet
    at = _utc(2026, 11, 18, 3, 0, 20)
    assert schedules.run_due(config, now=at, start_job=starts) == 1
    assert starts.calls == [{"user": 1, "query": "Top tech news today",
                             "payload": {"thread_id": "chat-ana", "scheduled": True, "schedule_id": row.id,
                                         "client_time_zone": "Asia/Karachi"}}]
    assert (row.last_job_id, as_utc(row.last_run_at), as_utc(row.next_run_at)) == ("job-1", at, _utc(2026, 11, 19, 3))
    assert schedules.run_due(config, now=at + timedelta(seconds=30), start_job=starts) == 0     # never twice


def test_a_busy_user_gets_the_run_a_few_minutes_later(db, config):
    row = _add(db, config)
    at = _utc(2026, 11, 18, 3, 0)
    assert schedules.run_due(config, now=at, start_job=_Starts("User already has 1 active job(s).")) == 0
    assert as_utc(row.next_run_at) == at + timedelta(minutes=RETRY_MINUTES)
    assert row.enabled and "active job" in row.last_error
    starts = _Starts()
    assert schedules.run_due(config, now=at + timedelta(minutes=RETRY_MINUTES), start_job=starts) == 1
    assert row.last_error == "" and as_utc(row.next_run_at) == _utc(2026, 11, 19, 3)


def test_a_retry_never_goes_past_the_next_regular_run(db, config):
    row = _add(db, config, every=HOUR, at_minute=0, time_zone="UTC")
    at = _utc(2026, 11, 18, 2, 58)
    row.next_run_at = at
    schedules.run_due(config, now=at, start_job=_Starts("Queue is full (8)."))
    assert as_utc(row.next_run_at) == _utc(2026, 11, 18, 3, 0)


def test_a_deleted_chat_or_a_disabled_account_stops_the_schedule(db, config):
    first, second = _add(db, config), _add(db, config)
    db.get(ChatThreadRow, "chat-ana").deleted_at = _utc(2026, 11, 18)
    starts = _Starts()
    assert schedules.run_due(config, now=_utc(2026, 11, 18, 3), start_job=starts) == 0
    assert not first.enabled and first.last_error == "stopped: the chat was deleted" and starts.calls == []
    db.get(ChatThreadRow, "chat-ana").deleted_at = None
    db.get(User, 1).status = USER_DISABLED
    schedules.set_enabled(db, second, False)
    schedules.set_enabled(db, second, True, now=_utc(2026, 11, 18, 4))
    assert as_utc(second.next_run_at) == _utc(2026, 11, 19, 3)       # switched back on: waits, no catch-up
    schedules.run_due(config, now=_utc(2026, 11, 19, 3), start_job=starts)
    assert not second.enabled and second.last_error == "stopped: the account is not active"


# ── nobody is watching a scheduled run ─────────────────────────────────

def _names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


def test_an_unattended_run_never_gets_a_tool_that_waits_for_a_click():
    config = replace(Config.from_env(), security_scan_enabled=True)
    waiting = {t.name for t in assistant.TOOLS if t.waits_for_user}
    assert waiting == {"operate_server"}
    assert waiting <= _names(assistant.tool_schemas(config))
    unattended = _names(assistant.tool_schemas(config, unattended=True))
    assert not waiting & unattended and "server_audit" in unattended       # read-only server tools stay


def test_an_unattended_run_refuses_a_click_waiting_tool_even_when_the_model_calls_it(monkeypatch):
    config = replace(Config.from_env(), security_scan_enabled=True)
    offered: list[set[str]] = []

    def fake_model(client, *, messages, tools, model, on_delta, should_continue):
        offered.append(_names(tools))
        return "", [{"name": "operate_server", "arguments": {"task": "restart nginx"}}]

    monkeypatch.setattr(assistant, "route_message", lambda *a, **k: Route())       # the normal tool choice
    monkeypatch.setattr(assistant, "chat_tools_with_calculator", fake_model)
    watched = assistant.decide("restart nginx", job_id="j1", config=config)
    unattended = assistant.decide("restart nginx", job_id="j2", config=config, unattended=True)
    assert watched.tool == "operate_server"
    assert unattended.tool == "search_knowledge_base"            # answered without it
    assert "operate_server" in offered[0] and "operate_server" not in offered[1]
    assert "ask_clarification" not in offered[1]                 # and no questions nobody can answer


def test_the_browsers_time_zone_reaches_the_job(monkeypatch):
    """ISSUES_LOG O92: the field was missing from the request model, so pydantic dropped it."""
    from web.routers import jobs as jobs_router
    from web.schemas import JobCreatePayload

    seen = {}

    def fake_create_job(db, *, user, query, mode, payload, config, priority):
        seen.update(payload)
        raise ValueError("stop here")

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    user = User(id=1, username="ana", password_hash="x", role="member")
    with pytest.raises(jobs_router.HTTPException):
        jobs_router.create_job_endpoint(JobCreatePayload(query="what time is it", client_time_zone="Asia/Karachi"),
                                        db=None, user=user)
    assert seen["client_time_zone"] == "Asia/Karachi"
