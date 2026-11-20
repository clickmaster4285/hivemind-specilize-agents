"""Passwords a person sets themselves (BACKLOG L30 T2): in My account, or with a one-time reset link an admin made.

No email is sent: the admin copies the link to the person, like an invite (src/core/signup.py). A link works once,
for RESET_LIFETIME; only the token's SHA-256 is stored; a new link cancels the person's earlier ones. Setting a
password always ends the account's other sign-ins (all of them for a reset link — whoever had the old password
is out).
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core.auth import hash_password, revoke_user_sessions, token_digest
from src.core.models_db import USER_ACTIVE, PasswordReset, User, as_utc, utcnow

RESET_LIFETIME = timedelta(hours=24)    # long enough to reach the person on another day's shift, short if leaked
RESET_TOKEN_BYTES = 32


def create_link(db: Session, user: User, admin: User) -> tuple[str, PasswordReset]:
    """(the token — shown to the admin once, never stored —, the reset row). The caller commits."""
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)))
    token = secrets.token_urlsafe(RESET_TOKEN_BYTES)
    reset = PasswordReset(token_hash=token_digest(token), user_id=user.id, created_by=admin.id,
                          expires_at=utcnow() + RESET_LIFETIME)
    db.add(reset)
    db.flush()
    return token, reset


def usable_link(db: Session, token: str | None) -> tuple[PasswordReset, User] | None:
    """The reset behind a link and its account, when the link is unused, not expired and the account active."""
    if not token:
        return None
    reset = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == token_digest(token)))
    if reset is None or reset.used_at is not None or as_utc(reset.expires_at) <= utcnow():
        return None
    user = db.get(User, reset.user_id)
    return (reset, user) if user is not None and user.status == USER_ACTIVE else None


def set_password(db: Session, user: User, password: str, *, keep_session: str | None = None) -> int:
    """A password the person chose (check it first with accounts.check_password): stored hashed, the must-change
    flag cleared, every other sign-in ended. → how many sign-ins were ended. The caller commits."""
    user.password_hash = hash_password(password)
    user.must_change_password = False
    return revoke_user_sessions(db, user.id, keep_family=keep_session)
