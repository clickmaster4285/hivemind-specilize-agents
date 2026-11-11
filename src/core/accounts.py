"""The rules a new or changed account must meet — one place for the admin's user form, the
command-line seeding (src/core/seed_users.py) and self sign-up (src/core/signup.py)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.models_db import User

MIN_USERNAME_LEN = 3
MIN_PASSWORD_LEN = 8


class AccountError(Exception):
    """A rule was not met; `status_code` is the HTTP answer the API gives for it."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise AccountError(422, f"Password must be at least {MIN_PASSWORD_LEN} characters")


def clean_email(db: Session, email: str | None, *, owner_id: int | None = None) -> str | None:
    """The email to store (None when blank). Refused when malformed or used by another account."""
    email = (email or "").strip() or None
    if email is None:
        return None
    if "@" not in email:
        raise AccountError(422, "That does not look like an email address")
    taken = select(User).where(func.lower(User.email) == email.lower())
    if owner_id is not None:
        taken = taken.where(User.id != owner_id)
    if db.scalar(taken) is not None:
        raise AccountError(409, "That email is already in use")
    return email


def clean_username(db: Session, username: str) -> str:
    username = username.strip()
    if len(username) < MIN_USERNAME_LEN:
        raise AccountError(422, f"Username must be at least {MIN_USERNAME_LEN} characters")
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise AccountError(409, "That username is already taken")
    return username


def clean_text(value: str | None) -> str | None:
    """An optional text field: trimmed, None when blank."""
    return (value or "").strip() or None
