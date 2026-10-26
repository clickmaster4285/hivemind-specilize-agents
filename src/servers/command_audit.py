"""Audit log of commands the agent ran on a server (SERVER_ACCESS_DECISION.md §8).

One row per command that changed, or would have changed, a server. The host and command are stored;
the password never is. Read-only diagnose commands are not logged (they change nothing). Writing the
log never breaks the operation — a logging failure is swallowed with a warning.
"""

from __future__ import annotations

import logging

from src.core.config import Config
from src.core.db import session_scope
from src.core.models_db import CommandLog

logger = logging.getLogger(__name__)

OUTPUT_KEPT = 2000      # characters of a command's output stored in the log


def log_command(
    *,
    config: Config,
    user_id: int | None,
    job_id: str | None,
    server_label: str,
    host: str,
    command: str,
    decision: str,              # ran | confirmed | cancelled | refused | auto
    exit_code: int | None = None,
    output: str | None = None,
) -> None:
    if user_id is None:
        return
    try:
        with session_scope(config) as session:
            session.add(CommandLog(
                user_id=user_id, job_id=job_id, server_label=server_label[:80], host=host[:255],
                command=command, decision=decision, exit_code=exit_code,
                output=(output or "")[:OUTPUT_KEPT] or None,
            ))
    except Exception as exc:  # noqa: BLE001 — the command already ran; the log is best-effort
        logger.warning("command_log write failed: %s", exc)


