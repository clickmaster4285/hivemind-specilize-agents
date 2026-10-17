"""On-demand freshness: sources used by an answer are re-checked when they are getting old.

The answer is never delayed (a full source check takes 6–30 s): it shows when each source was last
checked, and a stale source (older than BRAIN_FRESH_HOURS) is refreshed in the background right away —
only changed pages are downloaded (ETag / Last-Modified / git), so the next question gets them.
One refresh at a time across processes (the scheduler's Postgres advisory lock).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from src.config import Config

logger = logging.getLogger(__name__)

_running: set[str] = set()
_running_lock = threading.Lock()


def last_checked(config: Config, slugs: set[str]) -> dict[str, datetime | None]:
    if not slugs:
        return {}
    from src.brain.db import connect

    with connect(config) as conn:
        rows = conn.execute("SELECT slug, last_checked_at FROM brain.sources WHERE slug = ANY(%s)",
                            (sorted(slugs),)).fetchall()
    return {slug: checked for slug, checked in rows}


def stale(checked: dict[str, datetime | None], hours: float, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    limit = timedelta(hours=hours)
    return sorted(slug for slug, at in checked.items() if at is None or now - at > limit)


def refresh_in_background(config: Config, slugs: list[str]) -> list[str]:
    """Start a background refresh for the given sources (skips ones already running). → started slugs."""
    with _running_lock:
        todo = [s for s in slugs if s not in _running]
        _running.update(todo)
    if not todo:
        return []

    def run() -> None:
        import psycopg

        from src.brain.db import CONNECT_TIMEOUT_SECONDS, app_url
        from src.brain.ingest import ingest_source
        from src.brain.scheduler import LOCK_KEY

        try:
            with psycopg.connect(app_url(config), autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS) as lock:
                if not lock.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
                    logger.info("on-demand refresh skipped: another refresh is running")
                    return
                try:
                    for slug in todo:
                        stats = ingest_source(slug, config, log=lambda _line: None)
                        logger.info("on-demand refresh %s: %s — changed %s, new %s (%ss)",
                                    slug, stats.status, stats.changed, stats.new, stats.seconds)
                finally:
                    lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        except Exception:  # noqa: BLE001 — a failed refresh must never disturb answering
            logger.exception("on-demand refresh failed: %s", todo)
        finally:
            with _running_lock:
                _running.difference_update(todo)

    threading.Thread(target=run, name="brain-on-demand-refresh", daemon=True).start()
    return todo


def check_sources(config: Config, slugs: set[str]) -> dict[str, dict]:
    """For an answer's sources: {slug: {"checked_at": iso|None, "refreshing": bool}}; starts refreshes."""
    try:
        checked = last_checked(config, slugs)
    except Exception as exc:  # noqa: BLE001
        logger.info("freshness check unavailable: %s", exc)
        return {}
    started = set(refresh_in_background(config, stale(checked, config.brain_fresh_hours)))
    with _running_lock:
        running = set(_running)
    return {slug: {"checked_at": at.isoformat() if at else None, "refreshing": slug in started or slug in running}
            for slug, at in checked.items()}
