"""Automatic knowledge refresh (doc/BRAIN_DESIGN.md §4, B5).

Every BRAIN_SCHEDULER_MINUTES the API process: syncs data/brain/sources.yaml, finds enabled public
sources whose `last_checked_at + refresh_every` has passed, and ingests them (unchanged web pages
cost an HTTP 304, unchanged git files a hash compare). Optional local-hours window
(BRAIN_REFRESH_WINDOW=01-06). A Postgres advisory lock guarantees one refresh at a time, even with
several API/worker processes.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime

import psycopg

from src.brain.db import CONNECT_TIMEOUT_SECONDS, BrainDBError, app_url, connect
from src.brain.ingest import IngestStats, ingest_source
from src.brain.migrate import MigrationError, migrate
from src.brain.sources import RegistryError, sync_sources
from src.config import Config

log = logging.getLogger("brain.scheduler")

LOCK_KEY = 718_224_001          # any fixed number shared by every process
STARTUP_DELAY_SECONDS = 60      # let the API finish starting first


def in_window(window: str, now: datetime | None = None) -> bool:
    """'01-06' = from 01:00 until 05:59; '22-06' wraps midnight; '' = always."""
    if not window.strip():
        return True
    match = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", window)
    if not match:
        raise ValueError(f"BRAIN_REFRESH_WINDOW must look like 01-06 (got {window!r})")
    start, end = int(match.group(1)) % 24, int(match.group(2)) % 24
    hour = (now or datetime.now()).hour
    if start == end:
        return True
    return start <= hour < end if start < end else (hour >= start or hour < end)


def due_sources(config: Config) -> list[str]:
    """Enabled public sources whose refresh interval has passed (never-checked first)."""
    with connect(config, system=True) as conn:
        rows = conn.execute(
            """SELECT slug FROM brain.sources
               WHERE enabled AND scope = 'public'
                 AND (last_checked_at IS NULL OR last_checked_at + refresh_every <= now())
               ORDER BY last_checked_at NULLS FIRST, slug"""
        ).fetchall()
    return [slug for (slug,) in rows]


def run_due(config: Config | None = None, *, log_fn: Callable[[str], None] = log.info) -> list[IngestStats]:
    """Sync the registry, then ingest every due source. Returns [] if another refresh is running."""
    config = config or Config.from_env()
    with psycopg.connect(app_url(config), autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS) as lock_conn:
        if not lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            log_fn("brain refresh: another refresh is already running — skipped")
            return []
        try:
            migrate(config)
            sync_sources(config)
            results: list[IngestStats] = []
            for slug in due_sources(config):
                try:
                    stats = ingest_source(slug, config, log=lambda _line: None)
                except ValueError as exc:  # disabled / removed between listing and ingest
                    log_fn(f"brain refresh: {slug} skipped: {exc}")
                    continue
                results.append(stats)
                log_fn(
                    f"brain refresh: {slug} {stats.status} — new {stats.new}, changed {stats.changed}, "
                    f"same {stats.same}, removed {stats.removed}, failed {stats.failed} ({stats.seconds}s)"
                )
            return results
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def start_scheduler(config: Config | None = None) -> threading.Thread | None:
    """Background thread for the API process. Returns None when disabled."""
    config = config or Config.from_env()
    if not (config.brain_enabled and config.brain_scheduler):
        return None
    in_window(config.brain_refresh_window)  # fail fast on a malformed window

    def loop() -> None:
        time.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                if in_window(config.brain_refresh_window):
                    run_due(config)
            except (BrainDBError, MigrationError, RegistryError, psycopg.Error) as exc:
                log.warning("brain refresh failed: %s", exc)
            except Exception:  # noqa: BLE001 — never let the thread die
                log.exception("brain refresh crashed")
            time.sleep(config.brain_scheduler_minutes * 60)

    thread = threading.Thread(target=loop, name="brain-scheduler", daemon=True)
    thread.start()
    return thread
