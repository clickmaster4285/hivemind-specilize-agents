"""Delete an account for good (Settings → Users → Delete; the user's choice 2026-11-20).

"Deactivate" (web/routers/users.py) blocks sign-in and keeps everything; this removes the person and what is
theirs: chats and their messages, projects, schedules, device logins, own skills, feedback, SEO
projects and audits, sign-in tokens, the invites they made, and each chat's files under artifacts/<job id>/.
In the knowledge base: the questions they asked (retrieval_log) and their private sources.

Kept on purpose: the security log and the server command log (audit trails must outlive the account they are
about; the command log keeps the username), and the model usage rows without their name (the totals stay right). Shared skills they wrote and accounts they invited stay,
with the link to them cleared.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.core.abuse import account_key
from src.core.models_db import (
    ChatThreadRow,
    CommandLog,
    DeviceRow,
    Feedback,
    Job,
    JobEvent,
    LlmUsage,
    LoginFailure,
    ProjectRow,
    RefreshToken,
    ScheduleRow,
    SeoAudit,
    SeoProject,
    SkillRow,
    User,
    UserInvite,
)

log = logging.getLogger(__name__)

BUSY_JOB_STATES = ("queued", "running")
# a job id is a uuid: only such a folder name is ever removed under artifacts/
JOB_FOLDER_RE = re.compile(r"^[0-9a-fA-F-]{8,36}$")


class AccountBusy(Exception):
    """The account has a job queued or running: deleting now would pull its rows from under the worker."""


@dataclass
class Deleted:
    jobs: int = 0
    rows: int = 0                       # every other row removed
    files: int = 0                      # chat folders removed from artifacts/
    knowledge_base: str = "not used"    # "removed" | "not used" | why it could not be done
    job_ids: list[str] = field(default_factory=list)
    device_ids: set[str] = field(default_factory=set)


def _rows(db: Session, model, column, user_id: int) -> int:
    return db.execute(delete(model).where(column == user_id)).rowcount or 0


def delete_account(db: Session, user: User) -> Deleted:
    """Remove the account's rows in this session (the caller commits, then calls after_commit). Raises
    AccountBusy."""
    uid = user.id
    if db.scalar(select(Job.id).where(Job.user_id == uid, Job.status.in_(BUSY_JOB_STATES)).limit(1)):
        raise AccountBusy(f"{user.username} has a question still running — try again when it has finished")

    done = Deleted()
    job_ids = done.job_ids = list(db.scalars(select(Job.id).where(Job.user_id == uid)))
    if job_ids:
        done.rows += db.execute(delete(JobEvent).where(JobEvent.job_id.in_(job_ids))).rowcount or 0
    done.jobs = _rows(db, Job, Job.user_id, uid)
    done.device_ids = set(db.scalars(select(DeviceRow.id).where(DeviceRow.user_id == uid)))
    for model, column in (
        (ChatThreadRow, ChatThreadRow.user_id), (ProjectRow, ProjectRow.user_id), (ScheduleRow, ScheduleRow.user_id),
        (DeviceRow, DeviceRow.user_id), (Feedback, Feedback.user_id),
        (SeoAudit, SeoAudit.user_id), (SeoProject, SeoProject.user_id), (RefreshToken, RefreshToken.user_id),
        (UserInvite, UserInvite.created_by), (SkillRow, SkillRow.owner_id),
    ):
        done.rows += _rows(db, model, column, uid)
    done.rows += db.execute(
        delete(LoginFailure).where(LoginFailure.key == account_key(uid, user.username))).rowcount or 0
    # links from what stays: shared skills they wrote, accounts they invited, the usage totals
    db.execute(update(SkillRow).where(SkillRow.created_by == uid).values(created_by=None))
    db.execute(update(User).where(User.invited_by == uid).values(invited_by=None))
    db.execute(update(LlmUsage).where(LlmUsage.user_id == uid).values(user_id=None))
    # the server command log is the audit trail: kept, with the name of who it was (ISSUES_LOG O123)
    db.execute(update(CommandLog).where(CommandLog.user_id == uid, CommandLog.username == "")
               .values(username=user.username))
    db.execute(update(CommandLog).where(CommandLog.user_id == uid).values(user_id=None))
    db.delete(user)
    db.flush()

    return done


def after_commit(done: Deleted, *, artifacts_dir: Path | None) -> None:
    """What lives outside the database, once the delete is committed: kept SSH connections to their devices
    close (as when a device is removed), and each chat's folder under artifacts/ goes."""
    if done.device_ids:
        from src.servers.server_ops import forget

        forget(done.device_ids)
    if artifacts_dir is not None:
        done.files = _remove_job_folders(Path(artifacts_dir), done.job_ids)


def _remove_job_folders(root: Path, job_ids: list[str]) -> int:
    """artifacts/<job id>/ for each of their jobs — only uuid-named folders directly under the root."""
    removed = 0
    base = root.resolve()
    for job_id in job_ids:
        if not JOB_FOLDER_RE.match(job_id):
            continue
        folder = (base / job_id).resolve()
        if folder.parent != base or not folder.is_dir():
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    return removed


def delete_knowledge_base_rows(user_id: int, config) -> str:
    """The questions they asked and their private sources, in the brain database. Runs as that account under
    row-level security, so it can only reach their own rows. "removed", "not used", or why it failed — the
    account is deleted either way (the brain may be off)."""
    if not getattr(config, "brain_enabled", False):
        return "not used"
    try:
        from src.brain.db import connect

        with connect(config, account_id=str(user_id)) as conn:
            conn.execute("DELETE FROM brain.retrieval_log WHERE account_id = %s", (str(user_id),))
            # documents and chunks go with their source (ON DELETE CASCADE)
            conn.execute("DELETE FROM brain.sources WHERE scope = 'private' AND owner_id = %s", (str(user_id),))
    except Exception as exc:  # noqa: BLE001 — the brain database may be down; say so, do not undo the delete
        log.warning("knowledge-base rows of deleted user %s not removed: %s", user_id, exc)
        return f"not removed ({type(exc).__name__}) — the knowledge base was unreachable"
    return "removed"
