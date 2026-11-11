"""Authentication: sign in / refresh / sign out (cookie sessions), self sign-up (accounts wait for an
admin), the current user, and SSO status."""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core import captcha
from src.core.abuse import (
    TooManyAttempts,
    account_key,
    address_key,
    check_login,
    clear_account,
    client_address,
    needs_captcha,
    record_failure,
    wait_text,
)
from src.core.accounts import AccountError
from src.core.auth import (
    REFRESH_COOKIE,
    check_credentials,
    clear_session_cookies,
    end_session,
    get_current_user,
    get_db,
    user_modules,
    refresh_session,
    set_session_cookies,
    start_session,
)
from src.core.config import Config
from src.core.models_db import USER_ACTIVE, USER_PENDING, User, utcnow
from src.core.signup import open_signup, sign_up, usable_invite
from web.schemas import LoginPayload, SignupPayload

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
        "modules": user_modules(user),
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


# a 400/401 carrying this header: the next try needs a CAPTCHA (GET /api/auth/captcha) — L16 S2b
CAPTCHA_HEADER = "X-Captcha"
CAPTCHA_ASKED = {CAPTCHA_HEADER: "required"}


def _captcha_refused(captcha_id: str | None) -> HTTPException:
    detail = "The characters did not match — type the ones in the new picture" if captcha_id else \
        "Type the characters in the picture"
    return HTTPException(status_code=400, detail=detail, headers=CAPTCHA_ASKED)


@router.get("/captcha")
def new_captcha() -> dict[str, str]:
    """A CAPTCHA for the login or sign-up form: its id and the picture (a data URL). Works once, 5 min."""
    return captcha.store.new()


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response,
          db: Session = Depends(get_db)) -> dict[str, Any]:
    # Sign in with either the username or the email (email match is case-insensitive). If two accounts
    # somehow share an email, the first is used — email is not enforced unique (yet).
    ident = payload.username.strip()
    user = db.scalars(
        select(User).where((User.username == ident) | (func.lower(User.email) == ident.lower()))
    ).first()
    # counted per account (not per spelling: username and email are one account) and per address (L16 S2)
    account = account_key(user.id if user else None, ident)
    address = address_key(client_address(request.scope))
    try:
        check_login(db, account, address)
    except TooManyAttempts as exc:
        raise HTTPException(status_code=429, detail=f"Too many attempts — try again in {wait_text(exc.retry_after)}",
                            headers={"Retry-After": str(math.ceil(exc.retry_after.total_seconds()))}) from exc
    if needs_captcha(db, account, address) and not captcha.store.check(payload.captcha_id, payload.captcha_answer):
        # the password is not checked and the try is not counted: no one locks an account without a person
        raise _captcha_refused(payload.captcha_id)
    if not check_credentials(user, payload.password):
        record_failure(db, account, address)
        raise HTTPException(status_code=401, detail="Invalid username/email or password",
                            headers=CAPTCHA_ASKED if needs_captcha(db, account, address) else None)
    clear_account(db, account)
    if user.status == USER_PENDING:
        raise HTTPException(status_code=403, detail="Your account is waiting for an admin's approval.")
    if user.status != USER_ACTIVE:
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


@router.get("/signup")
def signup_info(invite: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """What the sign-up form may offer: whether open sign-up is on, and the invite behind a link."""
    found = usable_invite(db, invite)
    return {
        "open": open_signup(db),
        "invite": {"email": found.email, "expires_at": found.expires_at.isoformat()} if found else None,
        "invite_valid": None if not invite else found is not None,
    }


@router.post("/signup", status_code=201)
def signup(payload: SignupPayload, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Self sign-up (BACKLOG L16 S7): the new account waits for an admin's approval. Always needs a CAPTCHA."""
    if not captcha.store.check(payload.captcha_id, payload.captcha_answer):
        raise _captcha_refused(payload.captcha_id)
    try:
        user = sign_up(db, username=payload.username, email=payload.email, phone=payload.phone,
                       password=payload.password, invite_token=payload.invite,
                       address=client_address(request.scope))
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"username": user.username, "status": user.status}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {**_user_view(user), "daily_job_quota": user.daily_job_quota}
