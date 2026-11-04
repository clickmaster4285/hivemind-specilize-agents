"""Sign-in sessions (BACKLOG L16 S1), password hashing and default admin bootstrap.

A sign-in gives two `HttpOnly` cookies, so page scripts (and any injected script) never see them: a short
access token (JWT, sent to every `/api` call) and a refresh token (random, sent only to `/api/auth`, stored
hashed in `refresh_tokens`). When the access token runs out the browser trades the refresh token for a new
pair; each refresh token works once.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from passlib.context import CryptContext
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.core.config import DEV_JWT_SECRET, Config
from src.core.db import get_session, session_scope
from src.core.models_db import RefreshToken, User, utcnow

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_COOKIE = "ma_access"
REFRESH_COOKIE = "ma_refresh"
# the refresh token goes only to refresh / logout, never to the other API calls
REFRESH_PATH = "/api/auth"
# two tabs whose access token ran out together both refresh with the same token; a second use this soon
# after the first is that race, not a stolen copy
REFRESH_REUSE_GRACE = timedelta(seconds=10)
# an HS256 key should be at least as long as the hash output (RFC 7518 §3.2)
MIN_SECRET_BYTES = 32
REFRESH_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def check_secret(config: Config) -> None:
    """Refuse to run with a guessable signing key: anyone who knows it can mint an admin token."""
    if config.jwt_secret == DEV_JWT_SECRET or len(config.jwt_secret.encode()) < MIN_SECRET_BYTES:
        raise RuntimeError(
            f"JWT_SECRET is unset or shorter than {MIN_SECRET_BYTES} bytes — set it in .env "
            '(python -c "import secrets; print(secrets.token_urlsafe(48))")'
        )


def create_access_token(user: User, family: str, config: Config | None = None) -> str:
    config = config or Config.from_env()
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "sid": family,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=config.jwt_expire_minutes),
    }
    return jwt.encode(payload, config.jwt_secret, algorithm="HS256")


def decode_token(token: str, config: Config | None = None) -> dict[str, Any]:
    config = config or Config.from_env()
    try:
        return jwt.decode(token, config.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub", "sid"]})
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    """SQLite hands timestamps back without their zone; they were stored in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _issue_refresh(db: Session, user_id: int, family: str, config: Config) -> str:
    token = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    db.add(RefreshToken(user_id=user_id, family=family, token_hash=_digest(token),
                        expires_at=utcnow() + timedelta(days=config.refresh_token_days)))
    return token


def _revoke_family(db: Session, family: str) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.family == family, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


def start_session(db: Session, user: User, config: Config | None = None) -> tuple[str, str]:
    """A new sign-in: (access token, refresh token). The user's expired tokens are cleared on the way."""
    config = config or Config.from_env()
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.expires_at < utcnow()))
    family = str(uuid.uuid4())
    refresh = _issue_refresh(db, user.id, family, config)
    db.commit()
    return create_access_token(user, family, config), refresh


def refresh_session(db: Session, token: str, config: Config | None = None) -> tuple[User, str, str | None]:
    """Spend a refresh token: (user, new access token, new refresh token — None when the same token came
    back within REFRESH_REUSE_GRACE, whose successor the browser already holds)."""
    config = config or Config.from_env()
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _digest(token)))
    now = utcnow()
    if row is None or row.revoked_at is not None or _aware(row.expires_at) <= now:
        raise _unauthorized("Session expired — sign in again")
    user = db.get(User, row.user_id)
    reused = row.used_at is not None and now - _aware(row.used_at) > REFRESH_REUSE_GRACE
    if user is None or user.status == "disabled" or reused:
        _revoke_family(db, row.family)
        db.commit()
        raise _unauthorized("Session ended — sign in again")
    if row.used_at is not None:
        return user, create_access_token(user, row.family, config), None
    row.used_at = now
    refresh = _issue_refresh(db, user.id, row.family, config)
    db.commit()
    return user, create_access_token(user, row.family, config), refresh


def end_session(db: Session, token: str) -> None:
    """Sign out: the refresh token's family is revoked, so its access tokens stop working at once too."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _digest(token)))
    if row is not None:
        _revoke_family(db, row.family)
        db.commit()


def set_session_cookies(response: Response, request: Request, access: str, refresh: str | None,
                        config: Config | None = None) -> None:
    config = config or Config.from_env()
    secure = request.url.scheme == "https"     # behind the HTTPS proxy; plain http only on localhost
    response.set_cookie(ACCESS_COOKIE, access, max_age=config.jwt_expire_minutes * 60, path="/api",
                        httponly=True, secure=secure, samesite="strict")
    if refresh is not None:
        response.set_cookie(REFRESH_COOKIE, refresh, max_age=config.refresh_token_days * 86400, path=REFRESH_PATH,
                            httponly=True, secure=secure, samesite="strict")


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/api")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)


def same_origin(request: Request) -> bool:
    """A browser names the page that sent a request in Origin; a write from another site's page is refused
    (CSRF). The host is the request's own, or the one the proxy forwarded it for. No Origin = not a page."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc in {request.headers.get("host"), request.headers.get("x-forwarded-host")}


def bootstrap_admin(config: Config | None = None) -> None:
    config = config or Config.from_env()
    with session_scope(config) as session:
        existing = session.scalar(select(User).where(User.username == config.default_admin_user))
        if existing:
            return
        session.add(
            User(
                username=config.default_admin_user,
                password_hash=hash_password(config.default_admin_password),
                role="admin",
            )
        )


def get_db() -> Session:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise _unauthorized("Authentication required")
    payload = decode_token(token)
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise _unauthorized("User not found")
    if user.status == "disabled":
        raise _unauthorized("This account is disabled")
    revoked = db.scalar(select(RefreshToken.id).where(RefreshToken.family == payload["sid"],
                                                      RefreshToken.revoked_at.is_not(None)).limit(1))
    if revoked is not None:
        raise _unauthorized("Signed out — sign in again")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def may_use_devices(user: User) -> bool:
    """Settings → Devices: admins, and the users an admin gave access."""
    return user.role == "admin" or bool(user.devices_access)


def require_devices(user: User = Depends(get_current_user)) -> User:
    if not may_use_devices(user):
        raise HTTPException(status_code=403, detail="Ask an admin for access to Devices")
    return user
