"""Admin-only account management: list, create, edit and deactivate users (query analytics per user)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.auth import get_db, hash_password, require_admin
from src.core.models_db import Job, User
from web.schemas import UserCreatePayload, UserUpdatePayload

router = APIRouter(prefix="/api/auth/users", tags=["users"])


def _user_public(user: User, query_count: int | None = None) -> dict[str, Any]:
    """The account fields an admin may see. Never includes the password hash. `query_count` = how many
    (non-deleted) questions the user has asked, passed in by the caller to avoid an N+1 query."""
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "team": user.team,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
        "daily_job_quota": user.daily_job_quota,
        "query_count": query_count,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _ensure_an_admin_remains(db: Session) -> None:
    """Refuse a change (after flushing it) that would leave no active admin — avoids locking everyone out."""
    db.flush()
    if db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.status == "active")) == 0:
        raise HTTPException(status_code=400, detail="At least one active admin must remain")


@router.get("")
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Admin-only: every account, with how many questions each has asked (per-user query analytics)."""
    users = db.scalars(select(User).order_by(User.created_at)).all()
    counts = dict(
        db.execute(
            select(Job.user_id, func.count()).where(Job.deleted_at.is_(None)).group_by(Job.user_id)
        ).all()
    )
    return [_user_public(u, counts.get(u.id, 0)) for u in users]


@router.post("", status_code=201)
def create_user(
    payload: UserCreatePayload,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Admin provisions an account (the self-serve /register route stays disabled). The new user can then
    sign in and chat — their queries land in the jobs table under their own user_id, so per-user and (via
    the team tag) per-group query analytics work from first login. Rejects duplicate usernames."""
    username = payload.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=422, detail="Username must be at least 3 characters")
    if len(payload.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(status_code=409, detail="That username is already taken")
    email = (payload.email.strip() or None) if payload.email else None
    if email is not None and "@" not in email:
        raise HTTPException(status_code=422, detail="That does not look like an email address")
    if email is not None and db.scalar(select(User).where(func.lower(User.email) == email.lower())) is not None:
        raise HTTPException(status_code=409, detail="That email is already in use")
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        team=(payload.team.strip() or None) if payload.team else None,
        email=email,
        phone=(payload.phone.strip() or None) if payload.phone else None,
    )
    if payload.daily_job_quota is not None:
        user.daily_job_quota = max(0, payload.daily_job_quota)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_public(user, 0)


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdatePayload,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Admin edits an account. Guards stop you from locking admin out of the system."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        if payload.status == "disabled":
            raise HTTPException(status_code=400, detail="You cannot disable your own account")
        if payload.role == "member":
            raise HTTPException(status_code=400, detail="You cannot remove your own admin role")

    if payload.role is not None:
        user.role = payload.role
    if payload.status is not None:
        user.status = payload.status
    if payload.team is not None:
        user.team = payload.team.strip() or None
    if payload.email is not None:
        email = payload.email.strip() or None
        if email is not None and "@" not in email:
            raise HTTPException(status_code=422, detail="That does not look like an email address")
        if email is not None and db.scalar(
            select(User).where(func.lower(User.email) == email.lower(), User.id != user.id)
        ) is not None:
            raise HTTPException(status_code=409, detail="That email is already in use")
        user.email = email
    if payload.phone is not None:
        user.phone = payload.phone.strip() or None
    if payload.daily_job_quota is not None:
        user.daily_job_quota = max(0, payload.daily_job_quota)
    if payload.password is not None:
        if len(payload.password) < 8:
            raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
        user.password_hash = hash_password(payload.password)

    _ensure_an_admin_remains(db)  # catches demoting/disabling the last admin by any path
    db.commit()
    db.refresh(user)
    return _user_public(user)


@router.delete("/{user_id}")
def deactivate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """"Delete" = deactivate: the account can no longer sign in, but its chats and queries are kept
    (that search data is the point of having accounts). Re-enable with PATCH status=active."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    user.status = "disabled"
    _ensure_an_admin_remains(db)
    db.commit()
    db.refresh(user)
    return _user_public(user)
