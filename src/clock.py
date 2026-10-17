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


def valid_time_zone(name: str | None) -> bool:
    if not name or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def current_time_text(now: datetime | None = None, time_zone: str | None = None) -> str:
    """'Saturday, 17 October 2026, 11:11 AM — time zone Asia/Karachi (PKT, UTC+05:00)'.
    AM/PM is spelled out: given '11:11' the model once said PM in the morning."""
    zone = time_zone or _time_zone.get()
    if now is None:
        now = datetime.now(ZoneInfo(zone)) if zone else datetime.now().astimezone()
    elif zone:
        now = now.astimezone(ZoneInfo(zone))
    else:
        now = now.astimezone()
    offset = now.strftime("%z")
    utc = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    where = f"time zone {zone} ({now.tzname()}, {utc})" if zone else f"time zone {now.tzname()} ({utc})"
    return f"{now:%A, %d %B %Y, %I:%M %p} — {where}"
