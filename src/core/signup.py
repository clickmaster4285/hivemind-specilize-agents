"""Self sign-up and invite links (BACKLOG L16 S7, ISSUES_LOG O38; design approved by the user 2026-11-11).

Two ways in, both ending in an account that waits for an admin (status pending — it cannot sign in):
- **open sign-up**, for anyone who can open the page (the LAN), while an admin leaves it switched on;
- an **invite link** an admin made: works once, for INVITE_LIFETIME, also when open sign-up is off; its
  modules are given to the account, and with an email on it, the sign-up must use that email.
At most SIGNUPS_PER_ADDRESS sign-ups come from one address per SIGNUP_WINDOW.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.accounts import AccountError, check_password, clean_email, clean_text, clean_username
from src.core.auth import hash_password, token_digest
from src.core.models_db import USER_PENDING, AppSetting, User, UserInvite, as_utc, utcnow
from src.core.modules import DEFAULT_MODULES_JSON

INVITE_LIFETIME = timedelta(days=7)
INVITE_TOKEN_BYTES = 32                   # as a refresh token: unguessable
SIGNUPS_PER_ADDRESS = 5
SIGNUP_WINDOW = timedelta(days=1)

OPEN_SIGNUP_KEY = "open_signup"
OPEN_SIGNUP_DEFAULT = True                # the user's choice: LAN people may sign up until an admin stops it
_TRUE, _FALSE = "true", "false"


def open_signup(db: Session) -> bool:
    row = db.get(AppSetting, OPEN_SIGNUP_KEY)
    return OPEN_SIGNUP_DEFAULT if row is None else row.value == _TRUE


def set_open_signup(db: Session, on: bool) -> None:
    row = db.get(AppSetting, OPEN_SIGNUP_KEY) or AppSetting(key=OPEN_SIGNUP_KEY)
    row.value = _TRUE if on else _FALSE
    db.add(row)
    db.commit()


def create_invite(db: Session, admin: User, *, email: str | None, modules_json: str | None) -> tuple[str, UserInvite]:
    """(the token — shown to the admin once, never stored —, the invite)."""
    token = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
    invite = UserInvite(token_hash=token_digest(token), created_by=admin.id, email=clean_text(email),
                        modules_json=modules_json or DEFAULT_MODULES_JSON, expires_at=utcnow() + INVITE_LIFETIME)
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return token, invite


def usable_invite(db: Session, token: str | None) -> UserInvite | None:
    """The invite behind a link, when it is unused and not expired."""
    if not token:
        return None
    invite = db.scalar(select(UserInvite).where(UserInvite.token_hash == token_digest(token)))
    if invite is None or invite.used_at is not None or as_utc(invite.expires_at) <= utcnow():
        return None
    return invite


def open_invites(db: Session) -> list[UserInvite]:
    return list(db.scalars(select(UserInvite).where(UserInvite.used_at.is_(None), UserInvite.expires_at > utcnow())
                           .order_by(UserInvite.created_at.desc())))


def sign_up(db: Session, *, username: str, email: str, phone: str | None, password: str,
            invite_token: str | None, address: str) -> User:
    """Create a pending account, or raise AccountError."""
    invite = usable_invite(db, invite_token)
    if invite_token and invite is None:
        raise AccountError(410, "This invite link has expired or was already used — ask an admin for a new one")
    if invite is None and not open_signup(db):
        raise AccountError(403, "Sign-up is closed — ask an admin for an invite link")
    recent = db.scalar(select(func.count(User.id)).where(User.signup_address == address,
                                                         User.created_at > utcnow() - SIGNUP_WINDOW))
    if recent >= SIGNUPS_PER_ADDRESS:
        raise AccountError(429, "Too many sign-ups from this computer today — try again tomorrow")
    name = clean_username(db, username)
    check_password(password)
    stored_email = clean_email(db, email)
    if stored_email is None:
        raise AccountError(422, "An email address is needed")
    if invite is not None and invite.email and invite.email.lower() != stored_email.lower():
        raise AccountError(422, f"This invite is for {invite.email} — sign up with that email")
    user = User(username=name, password_hash=hash_password(password), role="member", email=stored_email,
                phone=clean_text(phone), status=USER_PENDING, signup_address=address,
                modules_json=invite.modules_json if invite else DEFAULT_MODULES_JSON,
                invited_by=invite.created_by if invite else None)
    db.add(user)
    if invite is not None:
        invite.used_at = utcnow()
    db.commit()
    db.refresh(user)
    return user
