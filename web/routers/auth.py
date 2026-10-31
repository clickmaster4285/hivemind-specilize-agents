"""Authentication: sign in, the disabled self-serve register, the current user, and SSO status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.auth import create_access_token, get_current_user, get_db, verify_password
from src.core.config import Config
from src.core.models_db import User, utcnow
from web.schemas import LoginPayload

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/sso/status")
def sso_status() -> dict[str, Any]:
    config = Config.from_env()
    return {
        "enabled": config.sso_enabled,
        "message": (
            "SSO hook ready — set SSO_ENABLED=true and wire your IdP callback in a future deploy."
            if config.sso_enabled
            else "SSO disabled. Using local JWT accounts."
        ),
    }


@router.post("/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    # Sign in with either the username or the email (email match is case-insensitive). If two accounts
    # somehow share an email, the first is used — email is not enforced unique (yet).
    ident = payload.username.strip()
    user = db.scalars(
        select(User).where((User.username == ident) | (func.lower(User.email) == ident.lower()))
    ).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username/email or password")
    if user.status == "disabled":
        raise HTTPException(status_code=403, detail="This account is disabled. Ask an admin to re-enable it.")
    user.last_login_at = utcnow()  # so the admin can see who is actually active
    db.commit()
    token = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "team": user.team,
            "email": user.email,
            "phone": user.phone,
            "status": user.status,
        },
    }


@router.post("/register")
def register_disabled() -> dict[str, Any]:
    """Public self-serve registration stays off: accounts are created by an admin (POST /api/auth/users)."""
    raise HTTPException(
        status_code=403,
        detail="Open registration is disabled. Ask an admin to create your account.",
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "team": user.team,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
        "daily_job_quota": user.daily_job_quota,
    }
