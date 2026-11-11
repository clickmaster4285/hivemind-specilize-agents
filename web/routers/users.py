"""Admin-only account management: list, create, edit and deactivate users (query analytics per user);
approve or reject sign-ups, invite links and the open sign-up switch (BACKLOG L16 S7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core import abuse, modules, signup
from src.core.abuse import ADDRESS_KEY, Lock, account_key
from src.core.accounts import AccountError, check_password, clean_email, clean_text, clean_username
from src.core.auth import get_db, hash_password, may_use, require_admin, user_modules
from src.core.models_db import USER_ACTIVE, USER_DISABLED, USER_PENDING, Job, User, UserInvite
from web.schemas import InvitePayload, SignupSettingsPayload, UserCreatePayload, UserUpdatePayload

router = APIRouter(prefix="/api/auth/users", tags=["users"])


def _user_public(user: User, query_count: int | None = None, names: dict[int, str] | None = None,
                 locked: dict[str, Lock] | None = None) -> dict[str, Any]:
    """The account fields an admin may see. Never includes the password hash. `query_count` = how many
    (non-deleted) questions the user has asked, passed in by the caller to avoid an N+1 query; `names`
    (id → username) names the admin who invited a sign-up, likewise; `locked` (abuse.locks by key) says
    whether the account must wait before its next sign-in (L16 S2b)."""
    lock = (locked or {}).get(account_key(user.id, user.username))
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "team": user.team,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
        "daily_job_quota": user.daily_job_quota,
        "modules": user_modules(user),
        "query_count": query_count,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "signup_address": user.signup_address,
        "invited_by": (names or {}).get(user.invited_by) if user.invited_by else None,
        "locked_until": lock.until.isoformat() if lock else None,
        "failed_logins": lock.failures if lock else 0,
    }


def _modules_json(ids: list[str]) -> str:
    try:
        return modules.dump(ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _checked(rule, *args, **kwargs):
    """Run an account rule (src/core/accounts.py); a broken rule becomes its HTTP answer."""
    try:
        return rule(*args, **kwargs)
    except AccountError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/modules")
def list_modules(_admin: User = Depends(require_admin)) -> list[dict[str, Any]]:
    """The modules an admin can give a member (src/core/modules.py), for the user form."""
    return [{"id": m.id, "title": m.title, "description": m.description, "default": m.default}
            for m in modules.MODULES]


def _ensure_an_admin_remains(db: Session) -> None:
    """Refuse a change (after flushing it) that would leave no active admin — avoids locking everyone out."""
    db.flush()
    if db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.status == USER_ACTIVE)) == 0:
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
    names = {u.id: u.username for u in users}
    locked = {lock.key: lock for lock in abuse.locks(db)}
    return [_user_public(u, counts.get(u.id, 0), names, locked) for u in users]


@router.post("", status_code=201)
def create_user(
    payload: UserCreatePayload,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Admin provisions an account (active at once; self sign-ups wait in the list instead). The new user can
    sign in and chat — their queries land in the jobs table under their own user_id, so per-user and (via
    the team tag) per-group query analytics work from first login. Rejects duplicate usernames."""
    username = _checked(clean_username, db, payload.username)
    _checked(check_password, payload.password)
    email = _checked(clean_email, db, payload.email)
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        team=clean_text(payload.team),
        email=email,
        phone=clean_text(payload.phone),
    )
    if payload.modules is not None:
        user.modules_json = _modules_json(payload.modules)
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
        if payload.status == USER_DISABLED:
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
        user.email = _checked(clean_email, db, payload.email, owner_id=user.id)
    if payload.phone is not None:
        user.phone = payload.phone.strip() or None
    if payload.daily_job_quota is not None:
        user.daily_job_quota = max(0, payload.daily_job_quota)
    if payload.password is not None:
        _checked(check_password, payload.password)
        user.password_hash = hash_password(payload.password)
    if payload.modules is not None:
        user.modules_json = _modules_json(payload.modules)

    _ensure_an_admin_remains(db)  # catches demoting/disabling the last admin by any path
    db.commit()
    db.refresh(user)
    if not may_use(user, "devices"):
        from src.servers import server_vault

        server_vault.clear(user.id)   # access taken away: the device logins they added go at once
    return _user_public(user)


@router.delete("/{user_id}")
def deactivate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """"Delete" = deactivate: the account can no longer sign in, but its chats and queries are kept
    (that search data is the point of having accounts). Re-enable with PATCH status=active.
    A sign-up still waiting for approval is rejected instead: deleted (it has no chats yet)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if user.status == USER_PENDING:
        public = {**_user_public(user), "status": "rejected"}
        db.delete(user)
        db.commit()
        return public
    user.status = USER_DISABLED
    _ensure_an_admin_remains(db)
    db.commit()
    db.refresh(user)
    return _user_public(user)


# ── sign-up: the open switch and invite links (BACKLOG L16 S7) ─────────────────────────────────────────
@router.get("/signup-settings")
def get_signup_settings(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"open": signup.open_signup(db)}


@router.put("/signup-settings")
def put_signup_settings(payload: SignupSettingsPayload, _admin: User = Depends(require_admin),
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    signup.set_open_signup(db, payload.open)
    return {"open": signup.open_signup(db)}


def _invite_public(invite: UserInvite, names: dict[int, str]) -> dict[str, Any]:
    return {"id": invite.id, "email": invite.email, "modules": modules.parse(invite.modules_json),
            "created_by": names.get(invite.created_by), "created_at": invite.created_at.isoformat(),
            "expires_at": invite.expires_at.isoformat()}


@router.get("/invites")
def list_invites(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """The invite links not yet used and not expired (the links themselves cannot be shown again)."""
    invites = signup.open_invites(db)
    names = dict(db.execute(select(User.id, User.username).where(User.id.in_({i.created_by for i in invites}))).all())
    return [_invite_public(i, names) for i in invites]


@router.post("/invites", status_code=201)
def create_invite(payload: InvitePayload, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    """A new invite link's token — shown this once; the page turns it into the link to share."""
    modules_json = _modules_json(payload.modules) if payload.modules is not None else None
    token, invite = signup.create_invite(db, admin, email=payload.email, modules_json=modules_json)
    return {**_invite_public(invite, {admin.id: admin.username}), "token": token}


@router.delete("/invites/{invite_id}")
def revoke_invite(invite_id: int, _admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    invite = db.get(UserInvite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    db.delete(invite)
    db.commit()
    return {"ok": True}


# ── locks after failed sign-ins (BACKLOG L16 S2b) ────────────────────────────────────────────────────
@router.get("/locks")
def list_locks(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Who must wait before the next sign-in: accounts (with their username) and computers."""
    found = abuse.locks(db)
    accounts = {int(lock.key.removeprefix(abuse.USER_KEY)): lock for lock in found if lock.key.startswith(abuse.USER_KEY)}
    names = dict(db.execute(select(User.id, User.username).where(User.id.in_(accounts))).all()) if accounts else {}
    return {
        "accounts": [{"user_id": uid, "username": names.get(uid), "failures": lock.failures,
                      "until": lock.until.isoformat()} for uid, lock in accounts.items()],
        "addresses": [{"address": lock.key.removeprefix(ADDRESS_KEY), "failures": lock.failures,
                       "until": lock.until.isoformat()} for lock in found if lock.key.startswith(ADDRESS_KEY)],
    }


@router.delete("/{user_id}/lock")
def unlock_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"unlocked": abuse.unlock(db, account_key(user.id, user.username), by=admin.username)}


@router.delete("/locks/address/{address}")
def unlock_address(address: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"unlocked": abuse.unlock(db, abuse.address_key(address), by=admin.username)}
