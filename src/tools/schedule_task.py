"""schedule_task — make a scheduled task from words in the chat (ISSUES_LOG O99, step 4).

"check the uk server every Monday at 9" → the model fills task / every / time / weekday → a card shows the schedule as
it will be saved ("Every Monday at 09:00 (Asia/Karachi): check the uk server") with Save / Cancel; only Save stores it
(src/jobs/schedules.py create: its own 🔁 chat, results compared with the last run). The card waits for a click, so a
scheduled run, which nobody watches, can never make schedules (Tool.waits_for_user).
"""

from __future__ import annotations

from collections.abc import Callable

from src.core.clock import current_time_zone
from src.core.config import Config
from src.tools.choice import Ask, Option, card_asker
from src.tools.tool_run import ToolRun

SAVE, CANCEL = "save", "cancel"
FALLBACK_ZONE = "UTC"            # a job without the browser's zone (an API client): the times are UTC, and said so


def plan(task: str, every: str, time: str, weekday: str, zone: str) -> tuple[str, int, int | None]:
    """→ (the words shown on the card, at_minute, weekday); ScheduleError when the timing makes no sense."""
    from src.jobs import schedules

    at_minute, day = schedules.parse_when(every, time, weekday)
    return f"{schedules.describe(every, at_minute, day)} ({zone})", at_minute, day


def make_schedule(*, user_id: int | None, task: str, every: str, time: str, weekday: str, zone: str, config: Config,
                  ask: Ask) -> str:
    """Show the card, save on Save → the answer for the chat."""
    from src.core.db import session_scope
    from src.core.models_db import User
    from src.jobs import schedules

    task = (task or "").strip()
    if not task:
        return "What should I repeat? Say the question or check, and how often (every hour, day or week)."
    try:
        when, at_minute, day = plan(task, every, time, weekday, zone)
    except schedules.ScheduleError as exc:
        return f"I could not set that up: {exc}."
    picked = ask(f"Save this schedule? **{when}**: {task}", [
        Option(SAVE, "Save", f"Its answers go to their own chat “{schedules.SCHEDULED_MARK} {task}”. Each run is "
                             "compared with the last: you get a dot and a notification only when something changed "
                             "or a run failed. It only reads and reports — it never changes a server."),
        Option(CANCEL, "Cancel"),
    ], False)
    if picked != [SAVE]:
        return "Not saved." if picked == [CANCEL] else "No answer in time — not saved."
    try:
        with session_scope(config) as session:
            user = session.get(User, user_id) if user_id is not None else None
            if user is None:
                return "Not saved: sign in first."
            schedules.create(session, user, prompt=task, every=every, at_minute=at_minute, weekday=day,
                             time_zone=zone, config=config)
    except schedules.ScheduleError as exc:
        return f"Not saved: {exc}."
    return (f"Saved — **{when}**: {task}.\n\nThe answers go to the chat “{schedules.SCHEDULED_MARK} {task}”. "
            "Pause it, run it now or delete it on the [Scheduled](/schedules) page.")


def run_schedule_task(query: str, *, job_id: str, config: Config, on_progress: Callable[[dict], None] | None = None,
                      user_id: int | None = None, task: str = "", every: str = "", time: str = "", weekday: str = "",
                      **_: object) -> tuple[str, list[str], float]:
    run = ToolRun(on_progress, query=query, job_id=job_id, role="Schedule", model="schedule_task", accent="#c4a35a")
    output = make_schedule(user_id=user_id, task=task, every=(every or "").strip().lower(), time=time,
                           weekday=weekday, zone=current_time_zone() or FALLBACK_ZONE, config=config,
                           ask=card_asker(run.emit, job_id, status=run.status))
    return run.finish(output, [])
