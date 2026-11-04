"""Authentication: sign in / refresh / sign out (cookie sessions), the disabled self-serve register, the
current user, and SSO status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.auth import (
    REFRESH_COOKIE,
    clear_session_cookies,
    end_session,
    get_current_user,
    get_db,
    refresh_session,
    set_session_cookies,
    start_session,
    verify_password,
)
from src.core.config import Config
from src.core.models_db import User, utcnow
from web.schemas import LoginPayload

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_view(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "team": user.team,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
    }


@router.get("/sso/status")
def sso_status() -> dict[str, Any]:
    config = Config.from_env()
    return {
        "enabled": config.sso_enabled,
        "message": (
            "SSO hook ready — set SSO_ENABLED=true and wire your IdP callback in a future deploy."
            if config.sso_enabled
            else "SSO disabled. Using local accounts."
        ),
    }


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response,
          db: Session = Depends(get_db)) -> dict[str, Any]:
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
    access, refresh = start_session(db, user)
    set_session_cookies(response, request, access, refresh)
    return {"user": _user_view(user)}


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Trade the refresh cookie for a new access + refresh pair (the browser calls this on a 401)."""
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not signed in")
    user, access, new_refresh = refresh_session(db, token)
    set_session_cookies(response, request, access, new_refresh)
    return {"user": _user_view(user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        end_session(db, token)
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/register")
def register_disabled() -> dict[str, Any]:
    """Public self-serve registration stays off: accounts are created by an admin (POST /api/auth/users)."""
    raise HTTPException(
        status_code=403,
        detail="Open registration is disabled. Ask an admin to create your account.",
    )


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {**_user_view(user), "daily_job_quota": user.daily_job_quota}
