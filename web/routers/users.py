"""Admin-only account management: list, create, edit and deactivate users (query analytics per user);
approve or reject sign-ups, invite links and the open sign-up switch (BACKLOG L16 S7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core import abuse, live_events, modules, security_log, signup
from src.core.abuse import ADDRESS_KEY, Lock, account_key
from src.core.accounts import AccountError, check_password, clean_email, clean_text, clean_username
from src.core.auth import (get_db, hash_password, may_use, require_admin, revoke_user_sessions, session_family,
                           user_modules)
from src.core.models_db import USER_ACTIVE, USER_DISABLED, USER_FORMER, USER_PENDING, Job, User, UserInvite
from web.schemas import (InvitePayload, SignupSettingsPayload, UserBulkPayload, UserCreatePayload, UserDeletePayload,
                         UserUpdatePayload)

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


def _live_user(db: Session, user_id: int) -> User:
    """An account an admin may act on: a deleted person's anonymous account (status former) is not one."""
    user = db.get(User, user_id)
    if user is None or user.status == USER_FORMER:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _ensure_an_admin_remains(db: Session) -> None:
    """Refuse a change (after flushing it) that would leave no active admin — avoids locking everyone out."""
    db.flush()
    if db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.status == USER_ACTIVE)) == 0:
        raise HTTPException(status_code=400, detail="At least one active admin must remain")


@router.get("")
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Admin-only: every account, with how many questions each has asked (per-user query analytics). A deleted
    person's anonymous account (status former) is left out."""
    users = db.scalars(select(User).where(User.status != USER_FORMER).order_by(User.created_at)).all()
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
    live_events.after_commit(db, live_events.USERS, admins=True)
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
    user.must_change_password = True      # the admin chose it: the person replaces it at the first sign-in
    db.add(user)
    db.flush()
    security_log.record(db, security_log.ACCOUNT_CREATED, user_id=user.id, account=user.username,
                        detail=f"by {_admin.username}: {user.role}" + (f", team {user.team}" if user.team else ""))
    db.commit()
    db.refresh(user)
    return _user_public(user, 0)


def _apply_changes(db: Session, admin: User, user: User, payload: UserUpdatePayload,
                   admin_session: str | None) -> list[str]:
    """Apply the fields that were sent; → what changed, in words, for the security log. A new password, role or
    status ends the person's sign-ins (a leaked password stops working at once) — an admin changing their own
    account keeps the sign-in they are using (`admin_session`). The caller commits."""
    if user.id == admin.id:
        if payload.status == USER_DISABLED:
            raise HTTPException(status_code=400, detail="You cannot disable your own account")
        if payload.role == "member":
            raise HTTPException(status_code=400, detail="You cannot remove your own admin role")
    changed: list[str] = []

    def put(field: str, value: Any, label: str | None = None) -> None:
        old = getattr(user, field)
        if old != value:
            setattr(user, field, value)
            changed.append(label or f"{field.replace('_', ' ')} {old or '—'} → {value or '—'}")

    if payload.role is not None:
        put("role", payload.role)
    if payload.status is not None:
        put("status", payload.status, "approved" if user.status == USER_PENDING and payload.status == USER_ACTIVE
            else None)
    if payload.team is not None:
        put("team", payload.team.strip() or None)
    if payload.email is not None:
        put("email", _checked(clean_email, db, payload.email, owner_id=user.id))
    if payload.phone is not None:
        put("phone", payload.phone.strip() or None)
    if payload.daily_job_quota is not None:
        put("daily_job_quota", max(0, payload.daily_job_quota))
    if payload.modules is not None:
        put("modules_json", _modules_json(payload.modules), "modules " + (", ".join(payload.modules) or "none"))
    if payload.password is not None:
        _checked(check_password, payload.password)
        user.password_hash = hash_password(payload.password)
        user.must_change_password = user.id != admin.id     # someone else's password: they choose their own next
        changed.append("password reset")
    if any(c == "password reset" or c.startswith(("role ", "status ", "approved")) for c in changed):
        ended = revoke_user_sessions(db, user.id, keep_family=admin_session if user.id == admin.id else None)
        if ended:
            changed.append(f"{ended} sign-in{'s' if ended != 1 else ''} ended")
    return changed


def _audit_changes(db: Session, admin: User, user: User, changed: list[str]) -> None:
    if changed:
        security_log.record(db, security_log.ACCOUNT_CHANGED, user_id=user.id, account=user.username,
                            detail=f"by {admin.username}: " + "; ".join(changed))


def _after_access_change(user: User) -> None:
    if not may_use(user, "devices"):
        from src.servers import server_vault

        server_vault.clear(user.id)   # access taken away: the device logins they added go at once


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdatePayload,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Admin edits an account; each change goes to the security log. Guards stop you from locking admin out."""
    live_events.after_commit(db, live_events.USERS, admins=True)
    user = _live_user(db, user_id)
    _audit_changes(db, admin, user, _apply_changes(db, admin, user, payload, session_family(request)))
    _ensure_an_admin_remains(db)  # catches demoting/disabling the last admin by any path
    db.commit()
    db.refresh(user)
    _after_access_change(user)
    return _user_public(user)


@router.post("/bulk")
def update_users(payload: UserBulkPayload, request: Request, admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    """The same change to several accounts (Users table → select → action), in one transaction: all or none.
    Only role, status, team, quota and modules — a password or an email is one person's."""
    live_events.after_commit(db, live_events.USERS, admins=True)
    users = db.scalars(select(User).where(User.id.in_(set(payload.ids)), User.status != USER_FORMER)).all()
    if len(users) != len(set(payload.ids)):
        raise HTTPException(status_code=404, detail="Some of the selected users no longer exist — reload the list")
    changes = UserUpdatePayload(**payload.changes.model_dump(exclude_unset=True))
    changed = 0
    for user in users:
        if user.status == USER_PENDING and changes.status == USER_DISABLED:
            continue                 # a sign-up is rejected one by one (Review), not disabled in bulk
        done = _apply_changes(db, admin, user, changes, session_family(request))
        _audit_changes(db, admin, user, done)
        changed += bool(done)
    _ensure_an_admin_remains(db)
    db.commit()
    for user in users:
        _after_access_change(user)
    return {"changed": changed, "selected": len(users)}


@router.delete("/{user_id}")
def deactivate_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """"Delete" = deactivate: the account can no longer sign in, but its chats and queries are kept
    (that search data is the point of having accounts). Re-enable with PATCH status=active.
    A sign-up still waiting for approval is rejected instead: deleted (it has no chats yet)."""
    live_events.after_commit(db, live_events.USERS, admins=True)
    user = _live_user(db, user_id)
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


@router.post("/{user_id}/delete")
def delete_user(
    user_id: int,
    payload: UserDeletePayload,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Delete an account (src/core/account_delete.py): the person cannot sign in again and their name goes; their
    chats stay, anonymised. "Deactivate" (DELETE /{user_id}) is the undoable one. The admin types the username."""
    live_events.after_commit(db, live_events.USERS, admins=True)
    from src.core.account_delete import AccountBusy, after_commit, delete_account, delete_knowledge_base_rows
    from src.core.config import Config

    user = _live_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if payload.confirm.strip() != user.username:
        raise HTTPException(status_code=400, detail="Type the username exactly to confirm the delete")
    config = Config.from_env()
    username = user.username
    try:
        done = delete_account(db, user)
    except AccountBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _ensure_an_admin_remains(db)
    security_log.record(db, security_log.ACCOUNT_DELETED, user_id=user_id, account=username,
                        detail=f"by {admin.username}: {done.jobs} questions kept as {user.username}, "
                               f"{done.rows} rows removed")
    db.commit()
    # after the commit: kept connections and the knowledge base (another database) — a failure there must not
    # undo the delete
    after_commit(done)
    done.knowledge_base = delete_knowledge_base_rows(user_id, config)
    return {"status": "deleted", "id": user_id, "username": username, "kept_as": user.username,
            "questions": done.jobs, "rows": done.rows, "knowledge_base": done.knowledge_base}


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
    user = _live_user(db, user_id)
    return {"unlocked": abuse.unlock(db, account_key(user.id, user.username), by=admin.username)}


@router.post("/{user_id}/reset-link")
def reset_link(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """A one-time password-reset link for this person (src/core/password_reset.py) — the token is shown this once;
    the page turns it into the link to send them. Their earlier unused links stop working."""
    from src.core.password_reset import RESET_LIFETIME, create_link

    user = _live_user(db, user_id)
    if user.status != USER_ACTIVE:
        raise HTTPException(status_code=400, detail="Only an active account can reset its password")
    token, reset = create_link(db, user, admin)
    security_log.record(db, security_log.RESET_LINK, user_id=user.id, account=user.username,
                        detail=f"by {admin.username}, valid {int(RESET_LIFETIME.total_seconds() // 3600)} h")
    db.commit()
    return {"token": token, "expires_at": reset.expires_at.isoformat()}


COMMAND_LOG_LIMIT = 300        # newest rows the admin page shows


@router.get("/command-log")
def command_log(search: str = "", limit: int = COMMAND_LOG_LIMIT, _admin: User = Depends(require_admin),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    """What the agent ran (or was refused) on servers, newest first, for every user — kept after an account is
    deleted (ISSUES_LOG O123). `search` matches the user, server, host or command."""
    from src.core.models_db import CommandLog

    stmt = select(CommandLog, User.username, User.status).outerjoin(User, User.id == CommandLog.user_id)
    words = search.strip()
    if words:
        like = f"%{words}%"
        stmt = stmt.where(CommandLog.username.ilike(like) | User.username.ilike(like)
                          | CommandLog.server_label.ilike(like) | CommandLog.host.ilike(like)
                          | CommandLog.command.ilike(like))
    rows = db.execute(stmt.order_by(CommandLog.created_at.desc()).limit(max(1, min(limit, COMMAND_LOG_LIMIT)))).all()
    return {"rows": [{
        "id": row.id, "at": row.created_at.isoformat() if row.created_at else None,
        # the name saved with the row (the audit trail keeps it after a delete), else the account's
        "username": row.username or name or "(unknown)", "deleted": row.user_id is None or status == USER_FORMER,
        "server": row.server_label, "host": row.host, "command": row.command, "decision": row.decision,
        "exit_code": row.exit_code, "output": row.output or "",
    } for row, name, status in rows]}


@router.get("/security-events")
def security_events(kind: str | None = None, limit: int = security_log.LIST_LIMIT,
                    _admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The security log, newest first (BACKLOG L16 S6): failed sign-ins, locks, reused sign-in tokens, refused
    uploads, unlocks. `kind` narrows it to one of security_log.KINDS."""
    if kind and kind not in security_log.KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(security_log.KINDS)}")
    events = security_log.recent(db, kind=kind, limit=limit)
    ids = {e["user_id"] for e in events if e["user_id"] is not None}
    names = dict(db.execute(select(User.id, User.username).where(User.id.in_(ids))).all()) if ids else {}
    return {"events": [{**e, "username": names.get(e["user_id"])} for e in events], "kinds": list(security_log.KINDS),
            "kept_days": security_log.SECURITY_LOG_DAYS}


@router.delete("/locks/address/{address}")
def unlock_address(address: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"unlocked": abuse.unlock(db, abuse.address_key(address), by=admin.username)}
