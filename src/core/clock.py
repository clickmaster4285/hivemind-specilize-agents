"""The current date and time as the *user* sees it — the model has no clock of its own.

The browser sends its IANA time zone (e.g. "Asia/Karachi") with every message; the worker sets it
for the job, and every prompt asks `current_time_text()`. Someone opening MulteAgent from another
city gets their own local time, without any location permission. Without a zone: the server's.
"""

from __future__ import annotations

import contextvars
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_time_zone: contextvars.ContextVar[str | None] = contextvars.ContextVar("client_time_zone", default=None)


def set_time_zone(name: str | None) -> None:
    """Called by the worker at the start of each job (None = use the server's zone)."""
    _time_zone.set(name if valid_time_zone(name) else None)


def current_time_zone() -> str | None:
    """The user's IANA time zone for this job (e.g. 'Asia/Karachi'), or None."""
    return _time_zone.get()


def valid_time_zone(name: str | None) -> bool:
    if not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _local(now: datetime | None, zone: str | None) -> datetime:
    """`now` (default: this moment) in the user's time zone, else the server's."""
    if now is None:
        return datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone()
    return now.astimezone(ZoneInfo(zone)) if zone else now.astimezone()


def current_date_text(now: datetime | None = None, time_zone: str | None = None) -> str:
    """'Saturday, 17 October 2026': the date alone. It changes once a day, so it may stand near the top of a
    prompt without breaking Ollama's reuse of the prompt's start (ISSUES_LOG O52)."""
    return f"{_local(now, time_zone or _time_zone.get()):%A, %d %B %Y}"


def current_time_text(now: datetime | None = None, time_zone: str | None = None) -> str:
    """'Saturday, 17 October 2026, 11:11 AM — time zone Asia/Karachi (PKT, UTC+05:00)'.
    AM/PM is spelled out: given '11:11' the model once said PM in the morning."""
    zone = time_zone or _time_zone.get()
    now = _local(now, zone)
    offset = now.strftime("%z")
    utc = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    where = f"time zone {zone} ({now.tzname()}, {utc})" if zone else f"time zone {now.tzname()} ({utc})"
    return f"{now:%A, %d %B %Y, %I:%M %p} — {where}"


def date_line(now_text: str | None = None) -> str:
    """A system prompt's date line. It goes LAST (ISSUES_LOG O52): Ollama reuses the cached start of a prompt,
    and a time that changes every minute near the top made it read the whole prompt again (tool choice,
    6,864 tokens: 17.1 s; with the date last: 1.6 s)."""
    return f"Current date and time: {now_text or current_time_text()}."
