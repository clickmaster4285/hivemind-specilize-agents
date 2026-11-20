"""Housekeeping (BACKLOG L30 T5): what grows without end is trimmed once a few hours, in the API.

- The stored messages (job_events) of chats deleted more than KEEP_DELETED ago — the job row stays (usage, history
  counts), its events are what take the room (~19 per question).
- Spent or expired password-reset links.
The security log keeps itself to SECURITY_LOG_DAYS (src/core/security_log.py); sign-in tokens are cleared at each
sign-in (auth.start_session); learned web pages purge themselves (src/brain/learned.py).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select

from src.core.config import Config
from src.core.db import session_scope
from src.core.loops import start_loop
from src.core.models_db import Job, JobEvent, PasswordReset, utcnow

log = logging.getLogger(__name__)

KEEP_DELETED = timedelta(days=30)        # a deleted chat can still be looked into for a month
EVERY_SECONDS = 6 * 3600
FIRST_DELAY_SECONDS = 600                # not while the API is starting


def tidy(config: Config | None = None) -> dict[str, int]:
    """One round; → what was removed."""
    now = utcnow()
    with session_scope(config) as session:
        old_jobs = select(Job.id).where(Job.deleted_at.is_not(None), Job.deleted_at < now - KEEP_DELETED)
        events = session.execute(delete(JobEvent).where(JobEvent.job_id.in_(old_jobs))).rowcount or 0
        resets = session.execute(delete(PasswordReset).where(
            (PasswordReset.used_at.is_not(None)) | (PasswordReset.expires_at < now))).rowcount or 0
    if events or resets:
        log.info("maintenance: %d stored messages of long-deleted chats, %d spent reset links removed", events, resets)
    return {"job_events": events, "password_resets": resets}


def start(config: Config):
    return start_loop("maintenance", lambda: tidy(config), every_seconds=EVERY_SECONDS,
                      first_delay=FIRST_DELAY_SECONDS, log=log)
