"""Sign-in sessions (BACKLOG L16 S1), password hashing and default admin bootstrap.

A sign-in gives two `HttpOnly` cookies, so page scripts (and any injected script) never see them: a short
access token (JWT, sent to every `/api` call) and a refresh token (random, sent only to `/api/auth`, stored
hashed in `refresh_tokens`). When the access token runs out the browser trades the refresh token for a new
pair; each refresh token works once.
"""

from __future__ import annotations

import functools
import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from passlib.context import CryptContext
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from src.core import modules
from src.core.config import DEV_JWT_SECRET, Config
from src.core.db import get_session, session_scope
from src.core.models_db import USER_ACTIVE, RefreshToken, User, as_utc, utcnow

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
# a sign-in ends this long after it began, however often it was refreshed (OWASP session management: an absolute
# timeout next to the idle one, which is the refresh token's REFRESH_TOKEN_DAYS)
SESSION_MAX_AGE = timedelta(days=30)
# while an account must replace an admin-chosen password, only these calls work (the page shows the change form)
PASSWORD_CHANGE_PATHS = frozenset({"/api/auth/me", "/api/auth/password", "/api/auth/logout", "/api/events"})
PASSWORD_CHANGE_HEADER = "X-Password-Change"
# How a session is named in My account → Sessions, from the browser's User-Agent: the first match wins, so the
# browsers built on Chrome come before it (Edge says "Chrome/… Edg/…") and Chrome before Safari (it says both).
UA_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
               ("Safari/", "Safari"))
UA_SYSTEMS = (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
              ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux"))
DEVICE_CHARS = 120


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


@functools.cache
def _no_account_hash() -> str:
    return hash_password(secrets.token_urlsafe(REFRESH_TOKEN_BYTES))


def check_credentials(user: User | None, password: str) -> bool:
    """The password check of a sign-in. With no matching account a hash is still checked, so a wrong name
    takes as long as a wrong password and the answer does not tell which usernames exist (L16 S2)."""
    if user is None:
        verify_password(password, _no_account_hash())
        return False
    return verify_password(password, user.password_hash)


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


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def device_name(user_agent: str | None) -> str:
    """"Chrome on Windows" from a User-Agent; "Unknown device" when it says neither (a script, curl)."""
    ua = user_agent or ""
    browser = next((name for mark, name in UA_BROWSERS if mark in ua), "")
    system = next((name for mark, name in UA_SYSTEMS if mark in ua), "")
    name = f"{browser} on {system}" if browser and system else browser or system
    return (name or "Unknown device")[:DEVICE_CHARS]


def _issue_refresh(db: Session, user_id: int, family: str, config: Config, started: datetime, *,
                   device: str = "", address: str = "") -> str:
    token = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    db.add(RefreshToken(user_id=user_id, family=family, token_hash=token_digest(token), session_started_at=started,
                        device=device, address=address,
                        expires_at=min(utcnow() + timedelta(days=config.refresh_token_days),
                                       started + SESSION_MAX_AGE)))
    return token


def _revoke_family(db: Session, family: str) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.family == family, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=utcnow()))


def revoke_user_sessions(db: Session, user_id: int, *, keep_family: str | None = None) -> int:
    """End every sign-in of this account (but `keep_family`, the caller's own), at once: their access tokens stop
    at the next call (get_current_user checks the family). For a password, role or status change, a reset link,
    "Sign out everywhere". Returns the number of sign-ins ended."""
    stmt = select(RefreshToken.family).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    if keep_family:
        stmt = stmt.where(RefreshToken.family != keep_family)
    families = set(db.scalars(stmt))
    if families:
        db.execute(update(RefreshToken).where(RefreshToken.family.in_(families), RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))
    return len(families)


def list_sessions(db: Session, user_id: int) -> list[dict[str, Any]]:
    """This account's live sign-ins, newest activity first: one per family — when it began, when it was last
    renewed, the device and the address it was made from. One query (GROUP BY family)."""
    rows = db.execute(
        select(RefreshToken.family, func.min(func.coalesce(RefreshToken.session_started_at, RefreshToken.created_at)),
               func.max(RefreshToken.created_at), func.max(RefreshToken.device), func.max(RefreshToken.address))
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > utcnow())
        .group_by(RefreshToken.family)
        .order_by(func.max(RefreshToken.created_at).desc())
    ).all()
    return [{"id": family, "started_at": as_utc(started).isoformat(), "last_active_at": as_utc(last).isoformat(),
             "device": device or "Unknown device", "address": address or ""}
            for family, started, last, device, address in rows]


def end_session_of(db: Session, user_id: int, family: str) -> bool:
    """End one sign-in of this account (My account → Sessions → Sign out). False when it is not theirs or gone."""
    owned = db.scalar(select(RefreshToken.id).where(RefreshToken.user_id == user_id, RefreshToken.family == family,
                                                    RefreshToken.revoked_at.is_(None)).limit(1))
    if owned is None:
        return False
    _revoke_family(db, family)
    return True


def session_family(request: Request) -> str | None:
    """The caller's own sign-in (its family id), from the access cookie — kept when they end their other sessions."""
    token = request.cookies.get(ACCESS_COOKIE)
    try:
        return decode_token(token)["sid"] if token else None
    except HTTPException:
        return None


def start_session(db: Session, user: User, config: Config | None = None, *, device: str = "",
                  address: str = "") -> tuple[str, str]:
    """A new sign-in: (access token, refresh token). The user's expired tokens are cleared on the way."""
    config = config or Config.from_env()
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.expires_at < utcnow()))
    family = str(uuid.uuid4())
    refresh = _issue_refresh(db, user.id, family, config, utcnow(), device=device, address=address)
    db.commit()
    return create_access_token(user, family, config), refresh


def refresh_session(db: Session, token: str, config: Config | None = None,
                    address: str = "") -> tuple[User, str, str | None]:
    """Spend a refresh token: (user, new access token, new refresh token — None when the same token came
    back within REFRESH_REUSE_GRACE, whose successor the browser already holds)."""
    config = config or Config.from_env()
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_digest(token)))
    now = utcnow()
    if row is None or row.revoked_at is not None or as_utc(row.expires_at) <= now:
        raise _unauthorized("Session expired — sign in again")
    user = db.get(User, row.user_id)
    reused = row.used_at is not None and now - as_utc(row.used_at) > REFRESH_REUSE_GRACE
    if user is None or user.status != USER_ACTIVE or reused:
        _revoke_family(db, row.family)
        if reused:      # a copied cookie, or a browser bug: the whole session ends — an admin should see it (S6)
            from src.core import security_log
            security_log.record(db, security_log.TOKEN_REUSED, user_id=row.user_id,
                                account=user.username if user else "", address=address,
                                detail="a used sign-in token came back; the session was ended")
        db.commit()
        raise _unauthorized("Session ended — sign in again")
    if row.used_at is not None:
        return user, create_access_token(user, row.family, config), None
    row.used_at = now
    # rows made before session_started_at existed: their own time is the best guess of when the sign-in began
    refresh = _issue_refresh(db, user.id, row.family, config, as_utc(row.session_started_at or row.created_at),
                             device=row.device or "", address=row.address or "")
    db.commit()
    return user, create_access_token(user, row.family, config), refresh


def end_session(db: Session, token: str) -> None:
    """Sign out: the refresh token's family is revoked, so its access tokens stop working at once too."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_digest(token)))
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
    if user.status != USER_ACTIVE:
        raise _unauthorized("This account is not active")
    revoked = db.scalar(select(RefreshToken.id).where(RefreshToken.family == payload["sid"],
                                                      RefreshToken.revoked_at.is_not(None)).limit(1))
    if revoked is not None:
        raise _unauthorized("Signed out — sign in again")
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_PATHS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Choose your own password first",
                            headers={PASSWORD_CHANGE_HEADER: "required"})
    return user


def stream_user(request: Request) -> User:
    """The signed-in user for a long-lived stream (SSE): checked in a short session of its own and handed back
    detached, so the stream does not keep a database connection for its whole life (ISSUES_LOG O121 — with
    get_current_user's `get_db`, the session closes only when the stream ends: ~15 open tabs used up the pool)."""
    from src.core.db import session_scope

    with session_scope() as db:
        user = get_current_user(request, db)
        db.expunge(user)            # its columns are loaded; nothing else is read from it later
    return user


def optional_user(request: Request, db: Session) -> User | None:
    """The signed-in user, or None (no cookie, an expired or revoked session) — for answers that only say less to an
    anonymous caller instead of refusing them (GET /api/health)."""
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def user_modules(user: User) -> list[str]:
    """The modules the user may use (src/core/modules.py): every one for an admin, else what an admin gave."""
    return list(modules.MODULE_IDS) if user.role == "admin" else modules.parse(user.modules_json)


def may_use(user: User, module: str) -> bool:
    return module in user_modules(user)


def require_module(module: str) -> Callable[..., User]:
    """A route of one module: refused (403) for a user an admin has not given it."""
    title = next(m.title for m in modules.MODULES if m.id == module)

    def dependency(user: User = Depends(get_current_user)) -> User:
        if not may_use(user, module):
            raise HTTPException(status_code=403, detail=f"Ask an admin for access to {title}")
        return user

    return dependency
