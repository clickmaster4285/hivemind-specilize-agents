"""Delete an account (Settings → Users → Delete), keeping the person's chats, anonymised (the user's choice,
2026-11-20: "we should not delete users' chats" — they are history and what the knowledge base learns from).

"Deactivate" (web/routers/users.py) blocks sign-in and keeps everything, undoably. Delete is final for the
*person*: the account becomes `former-user-<id>` with status `former` — no name, email, phone, team, password or
modules, never able to sign in again, hidden from the Users list. What only served that person goes: sign-in
tokens, saved device logins (their SSH connections close), schedules, invites they made, their own skills, the
websites they tracked (SEO projects), their failed-sign-in count, and in the knowledge base their private sources
and chat-recall pieces.

Kept, under the anonymous name: chats with their messages and files, projects, feedback, SEO audit results, model
usage (the totals stay right), the questions asked of the knowledge base. Kept with the real name, as audit trails:
the server command log and the security log.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from src.core.abuse import account_key
from src.core.models_db import (
    USER_FORMER,
    CommandLog,
    DeviceRow,
    DeviceShare,
    Job,
    LoginFailure,
    PasswordReset,
    RefreshToken,
    ScheduleRow,
    SeoProject,
    SkillRow,
    User,
    UserInvite,
)

log = logging.getLogger(__name__)

BUSY_JOB_STATES = ("queued", "running")
FORMER_NAME = "former-user-{id}"


class AccountBusy(Exception):
    """The account has a job queued or running: wait until it has finished."""


@dataclass
class Deleted:
    jobs: int = 0                       # questions kept, anonymised
    rows: int = 0                       # rows removed (tokens, devices, schedules, invites, own skills…)
    knowledge_base: str = "not used"    # "removed" | "not used" | why it could not be done
    device_ids: set[str] = field(default_factory=set)


def _rows(db: Session, model, column, user_id: int) -> int:
    return db.execute(delete(model).where(column == user_id)).rowcount or 0


def former_name(user_id: int) -> str:
    return FORMER_NAME.format(id=user_id)


def delete_account(db: Session, user: User) -> Deleted:
    """Anonymise the account and remove what only served the person, in this session (the caller commits, then
    calls after_commit). Raises AccountBusy."""
    from src.core.auth import hash_password

    uid = user.id
    if db.scalar(select(Job.id).where(Job.user_id == uid, Job.status.in_(BUSY_JOB_STATES)).limit(1)):
        raise AccountBusy(f"{user.username} has a question still running — try again when it has finished")

    done = Deleted()
    done.jobs = db.scalar(select(func.count()).select_from(Job).where(Job.user_id == uid)) or 0
    done.device_ids = set(db.scalars(select(DeviceRow.id).where(DeviceRow.user_id == uid)))
    # shares of their devices, and the devices shared with them (BACKLOG L30 T4)
    done.rows += db.execute(delete(DeviceShare).where(DeviceShare.user_id == uid)).rowcount or 0
    if done.device_ids:
        done.rows += db.execute(delete(DeviceShare).where(DeviceShare.device_id.in_(done.device_ids))).rowcount or 0
    for model, column in (
        (RefreshToken, RefreshToken.user_id), (PasswordReset, PasswordReset.user_id),
        (DeviceRow, DeviceRow.user_id), (ScheduleRow, ScheduleRow.user_id),
        (UserInvite, UserInvite.created_by), (SkillRow, SkillRow.owner_id), (SeoProject, SeoProject.user_id),
    ):
        done.rows += _rows(db, model, column, uid)
    done.rows += db.execute(
        delete(LoginFailure).where(LoginFailure.key == account_key(uid, user.username))).rowcount or 0
    # the server command log is the audit trail: it keeps the real name (ISSUES_LOG O123)
    db.execute(update(CommandLog).where(CommandLog.user_id == uid, CommandLog.username == "")
               .values(username=user.username))
    # the person goes; the account stays as the anonymous owner of their chats
    user.username = former_name(uid)
    user.email = user.phone = user.team = None
    user.password_hash = hash_password(secrets.token_urlsafe(32))   # no one knows it; the status refuses it anyway
    user.status = USER_FORMER
    user.must_change_password = False
    user.role = "member"
    user.modules_json = "[]"
    user.signup_address = None
    db.flush()
    return done


def after_commit(done: Deleted) -> None:
    """Once the delete is committed: kept SSH connections to their devices close (as when a device is removed)."""
    if done.device_ids:
        from src.servers.server_ops import forget

        forget(done.device_ids)


def delete_knowledge_base_rows(user_id: int, config) -> str:
    """Their private sources and chat-recall pieces in the brain database (the questions they asked stay, under the
    account id). Runs as that account under row-level security, so it can only reach their own rows. "removed",
    "not used", or why it failed — the account is deleted either way (the brain may be off)."""
    if not getattr(config, "brain_enabled", False):
        return "not used"
    try:
        from src.brain.db import connect

        with connect(config, account_id=str(user_id)) as conn:
            conn.execute("DELETE FROM brain.chat_passages WHERE owner_id = %s", (str(user_id),))
            # documents and chunks go with their source (ON DELETE CASCADE)
            conn.execute("DELETE FROM brain.sources WHERE scope = 'private' AND owner_id = %s", (str(user_id),))
    except Exception as exc:  # noqa: BLE001 — the brain database may be down; say so, do not undo the delete
        log.warning("knowledge-base rows of deleted user %s not removed: %s", user_id, exc)
        return f"not removed ({type(exc).__name__}) — the knowledge base was unreachable"
    return "removed"
