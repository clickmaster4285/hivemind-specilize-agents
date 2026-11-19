"""What a chat is working on: its server, project folder, branch, the last deploy (ISSUES_LOG O103, O104, O107).

Every server message used to start from nothing: the tools saw only the words of that message, so "check again"
asked "Which server?" and diagnose searched the whole disk for a folder the chat had deployed minutes before.
Each server job now leaves what it worked on in the chat (`chat_threads.work`, JSON); a later message that names
no server or project uses it, and a "Which server?" question waits for the reply (`pending`).

Fields: server (the login's id), project (a folder path), branch, deploy (one line: what the last deploy did),
pending ({tool, args, query}: a server tool that asked "Which server?").
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.config import Config

logger = logging.getLogger(__name__)

PENDING_REPLY_WORDS = 8         # a longer reply to "Which server?" is a new message, not the answer


def _thread_of(session: Any, job_id: str) -> str:
    from src.core.models_db import Job

    job = session.get(Job, job_id)
    try:
        return str(json.loads(job.payload_json or "{}").get("thread_id") or "") if job else ""
    except (TypeError, ValueError):
        return ""


def get(config: Config, job_id: str) -> dict[str, Any]:
    """The chat's working context for this job's chat; {} when none (or on any problem: it is a hint)."""
    from src.core.db import session_scope
    from src.core.models_db import ChatThreadRow

    try:
        with session_scope(config) as session:
            thread = _thread_of(session, job_id)
            row = session.get(ChatThreadRow, thread) if thread else None
            work = json.loads(row.work or "{}") if row is not None else {}
            return work if isinstance(work, dict) else {}
    except Exception as exc:  # noqa: BLE001 — the chat goes on without it
        logger.info("chat work: read failed: %s", exc)
        return {}


def remember(config: Config, job_id: str, **fields: Any) -> None:
    """Merge fields into the chat's working context; a None value removes the field."""
    from src.core.db import session_scope
    from src.core.models_db import ChatThreadRow

    try:
        with session_scope(config) as session:
            thread = _thread_of(session, job_id)
            row = session.get(ChatThreadRow, thread) if thread else None
            if row is None:
                return
            work = json.loads(row.work or "{}")
            work = work if isinstance(work, dict) else {}
            for key, value in fields.items():
                if value is None:
                    work.pop(key, None)
                else:
                    work[key] = value
            row.work = json.dumps(work)
    except Exception as exc:  # noqa: BLE001
        logger.info("chat work: save failed: %s", exc)


def describe(work: dict[str, Any], server_label: str = "") -> str:
    """One line for the prompts: "This chat is working on uk server, project /var/www/app (branch dev)." """
    parts = []
    if server_label:
        parts.append(server_label)
    if work.get("project"):
        parts.append(f"project {work['project']}" + (f" (branch {work['branch']})" if work.get("branch") else ""))
    if not parts:
        return ""
    line = "This chat is working on " + ", ".join(parts) + "."
    if work.get("deploy"):
        line += f" Last deploy here: {work['deploy']}"
    return line


def is_reply(query: str) -> bool:
    return len((query or "").split()) <= PENDING_REPLY_WORDS
