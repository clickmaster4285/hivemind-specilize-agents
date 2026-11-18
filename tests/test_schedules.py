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


def _add(db, config, *, user_id=1, now=_utc(2026, 11, 18, 2, 0), **timing):
    timing = {"every": DAY, "at_minute": 8 * 60, "weekday": None, "time_zone": "Asia/Karachi", **timing}
    return schedules.create(db, db.get(User, user_id), prompt="  Top tech news today  ", config=config, now=now,
                            **timing)


def test_a_schedule_gets_its_own_chat_and_only_its_owner_sees_it(db, config):
    """ISSUES_LOG O99: answers in the chat it was made from got lost among other messages."""
    row, other = _add(db, config), _add(db, config)
    assert (row.prompt, row.weekday, as_utc(row.next_run_at)) == ("Top tech news today", None, _utc(2026, 11, 18, 3))
    chat = db.get(ChatThreadRow, row.thread_id)
    assert chat.user_id == 1 and chat.title == "🔁 Top tech news today" and row.thread_id != other.thread_id
    with pytest.raises(KeyError):
        schedules.own(db, db.get(User, 2), row.id)              # nobody else can see or change it
    assert [s["chat_title"] for s in schedules.listing(db, db.get(User, 1))] == ["🔁 Top tech news today"] * 2
    assert schedules.listing(db, db.get(User, 2)) == []


def test_run_now_starts_one_run_and_keeps_the_timetable(db, config):
    row = _add(db, config)
    starts = _Starts()
    job = schedules.run_now(db, row, config, now=_utc(2026, 11, 18, 2, 30), start_job=starts)
    assert starts.calls[0]["payload"]["thread_id"] == row.thread_id and row.last_job_id == job.id
    assert as_utc(row.next_run_at) == _utc(2026, 11, 18, 3)                        # still at 08:00 local
    with pytest.raises(ScheduleError, match="active job"):
        schedules.run_now(db, row, config, start_job=_Starts("User already has 1 active job(s)."))


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
                             "payload": {"thread_id": row.thread_id, "scheduled": True, "schedule_id": row.id,
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
    db.get(ChatThreadRow, first.thread_id).deleted_at = _utc(2026, 11, 18)
    starts = _Starts()
    assert schedules.run_due(config, now=_utc(2026, 11, 18, 3), start_job=starts) == 1      # only the second
    assert not first.enabled and first.last_error == "stopped: the chat was deleted"
    assert starts.calls[0]["payload"]["thread_id"] == second.thread_id
    db.get(User, 1).status = USER_DISABLED
    schedules.set_enabled(db, second, False)
    schedules.set_enabled(db, second, True, now=_utc(2026, 11, 18, 4))
    assert as_utc(second.next_run_at) == _utc(2026, 11, 19, 3)       # switched back on: waits, no catch-up
    schedules.run_due(config, now=_utc(2026, 11, 19, 3), start_job=starts)
    assert len(starts.calls) == 1
    assert not second.enabled and second.last_error == "stopped: the account is not active"


# ── nobody is watching a scheduled run ─────────────────────────────────

def _names(tools: list[dict]) -> set[str]:
    return {t["function"]["name"] for t in tools}


def test_an_unattended_run_never_gets_a_tool_that_waits_for_a_click():
    config = replace(Config.from_env(), security_scan_enabled=True)
    waiting = {t.name for t in assistant.TOOLS if t.waits_for_user}
    assert waiting == {"operate_server", "deploy_project", "schedule_task"}       # a scheduled run never makes schedules
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


# ── after a run: what changed since the last one (O99 step 2) ──────────

def _run(db, row, n, answer, status="succeeded"):
    """A finished run of the schedule: its job and its answer event."""
    from src.jobs.jobs import append_event

    job = Job(id=f"run-{n}", user_id=row.user_id, mode="chat", status=status, query=row.prompt,
              payload_json=json.dumps({"thread_id": row.thread_id, "scheduled": True, "schedule_id": row.id}),
              created_at=_utc(2026, 11, 18, n), error=None if status == "succeeded" else "TimeoutError")
    db.add(job)
    db.flush()
    if answer:
        append_event(db, job.id, {"type": "agent_done", "agent_id": "assistant", "output": answer})
    return job


def _result(db, job_id):
    from src.core.models_db import JobEvent

    rows = db.query(JobEvent).filter(JobEvent.job_id == job_id).order_by(JobEvent.seq).all()
    return [json.loads(r.event_json) for r in rows][-1]


def test_each_run_says_only_what_changed_and_a_quiet_one_says_so(db, config):
    row = _add(db, config)
    seen = []

    def fake_compare(before, now, cfg):
        seen.append((before, now))
        return "" if before == now else "- nginx 1.24 → 1.26 (CVE-2024-7347 fixed)"

    _run(db, row, 1, "nginx 1.24, 3 findings")
    assert schedules.after_run(config, "run-1", compare_fn=fake_compare) == schedules.OK
    assert _result(db, "run-1") == {"type": "schedule_result", "status": "ok", "first": True, "changes": ""}
    assert seen == []                                                           # nothing to compare with yet

    _run(db, row, 2, "nginx 1.24, 3 findings")
    assert schedules.after_run(config, "run-2", compare_fn=fake_compare) == schedules.QUIET
    assert row.last_status == "quiet" and seen[-1] == ("nginx 1.24, 3 findings", "nginx 1.24, 3 findings")

    _run(db, row, 3, "nginx 1.26, 2 findings")
    assert schedules.after_run(config, "run-3", compare_fn=fake_compare) == schedules.OK
    assert _result(db, "run-3")["changes"].startswith("- nginx 1.24 → 1.26")
    assert seen[-1][0] == "nginx 1.24, 3 findings"                               # the run just before


def test_a_failed_run_is_always_reported_and_never_compared(db, config):
    row = _add(db, config)
    _run(db, row, 1, "all good")
    schedules.after_run(config, "run-1", compare_fn=lambda *a: "")
    _run(db, row, 2, "", status="failed")
    assert schedules.after_run(config, "run-2", compare_fn=lambda *a: pytest.fail("compared a failed run")) == "failed"
    assert row.last_status == "failed" and row.last_error == "TimeoutError"
    _run(db, row, 3, "all good")              # the failed run is skipped: compared with run 1
    assert schedules.after_run(config, "run-3", compare_fn=lambda before, now, cfg: "" if before == now else "x") == "quiet"


def test_the_compare_prompt_turns_nothing_new_into_quiet(monkeypatch, config):
    class FakeLLM:
        def __init__(self, cfg):
            pass

        def chat(self, **kw):
            assert "BEFORE:" in kw["user"] and kw["temperature"] == 0
            return "Nothing new."

    monkeypatch.setattr("src.llm.llm_client.LLMClient", FakeLLM)
    assert schedules.compare("a", "a", config) == ""


# ── results find the user (O99 step 3) ─────────────────────────────────

def test_a_new_result_marks_the_chat_unread_a_quiet_one_does_not(db, config):
    from src.jobs.history_store import _thread_dict

    row = _add(db, config)
    chat = db.get(ChatThreadRow, row.thread_id)
    _run(db, row, 1, "3 findings")
    schedules.after_run(config, "run-1", compare_fn=lambda *a: "")
    assert chat.unread and _thread_dict(chat)["unread"] is True                 # the first run is news
    assert schedules.unread(db, db.get(User, 1)) == [{"schedule_id": row.id, "thread_id": row.thread_id,
                                                      "title": "🔁 Top tech news today", "status": "ok",
                                                      "job_id": row.last_job_id}]
    assert schedules.unread(db, db.get(User, 2)) == []
    chat.unread = False                                                          # opened (POST …/seen)
    _run(db, row, 2, "3 findings")
    schedules.after_run(config, "run-2", compare_fn=lambda *a: "")
    assert not chat.unread and schedules.unread(db, db.get(User, 1)) == []      # quiet: nobody is told
    _run(db, row, 3, "", status="failed")
    schedules.after_run(config, "run-3", compare_fn=lambda *a: "")
    assert chat.unread                                                           # a failure always is


# ── made in words (O99 step 4) ─────────────────────────────────────────

@pytest.mark.parametrize("every, time, weekday, expected", [
    ("day", "9", "", (540, None)), ("day", "9pm", "", (1260, None)), ("day", "", "", (schedules.DEFAULT_AT_MINUTE, None)),
    ("week", "08:00", "friday", (480, 4)), ("week", "", "tues", (540, 1)), ("week", "", "", (540, 0)),
    ("hour", ":45", "", (45, None)), ("hour", "", "", (0, None)),
])
def test_the_models_words_for_when(every, time, weekday, expected):
    assert schedules.parse_when(every, time, weekday) == expected


@pytest.mark.parametrize("every, time, weekday, error", [
    ("month", "", "", "every hour, day or week"), ("day", "25:00", "", "not a time of day"),
    ("day", "noonish", "", "not a time like"), ("week", "9", "someday", "not a day of the week"),
    ("hour", "75", "", "minute past the hour"),
])
def test_impossible_words_are_explained(every, time, weekday, error):
    with pytest.raises(ScheduleError, match=error):
        schedules.parse_when(every, time, weekday)


def test_a_schedule_made_in_words_is_saved_only_on_save(db, config, monkeypatch):
    from src.tools import schedule_task as st

    @contextmanager
    def scope(_config=None):
        yield db
        db.flush()

    monkeypatch.setattr("src.core.db.session_scope", scope)
    asked = []

    def click(answer):
        def ask(question, options, multi):
            asked.append((question, [o.id for o in options], multi))
            return answer
        return ask

    words = {"user_id": 1, "task": "check the uk server", "every": "week", "time": "9", "weekday": "Monday",
             "zone": "Asia/Karachi", "config": config}
    assert st.make_schedule(**words, ask=click(["cancel"])) == "Not saved."
    assert st.make_schedule(**words, ask=click(None)) == "No answer in time — not saved."
    assert db.query(ScheduleRow).count() == 0
    out = st.make_schedule(**words, ask=click(["save"]))
    assert asked[-1] == ("Save this schedule? **Every Monday at 09:00 (Asia/Karachi)**: check the uk server",
                         ["save", "cancel"], False)
    row = db.query(ScheduleRow).one()
    assert (row.prompt, row.every, row.at_minute, row.weekday, row.time_zone) == (
        "check the uk server", "week", 540, 0, "Asia/Karachi")
    assert out.startswith("Saved — **Every Monday at 09:00 (Asia/Karachi)**") and "🔁 check the uk server" in out
    assert st.make_schedule(**{**words, "time": "25:00"}, ask=click(["save"])).startswith("I could not set that up")
