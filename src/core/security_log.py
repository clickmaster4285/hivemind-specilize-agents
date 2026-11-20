"""The security log (BACKLOG L16 S6): what an admin should be able to look back at.

Failed sign-ins, accounts locked and computers blocked after too many of them, a sign-in token used twice (a copied
cookie: the whole session is ended), uploads refused (a file that is not what it says, too big, too many pixels),
and an admin's unlock. Until now these went only to logs/api.log, which rolls over and needs a shell to read.

Rows older than SECURITY_LOG_DAYS are removed when new ones are written. Writing an event never fails the request
it belongs to: a broken log loses the event, not the sign-in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core.models_db import SecurityEvent, utcnow

log = logging.getLogger(__name__)

SECURITY_LOG_DAYS = 90
LIST_LIMIT = 500
DETAIL_CHARS = 500

LOGIN_FAILED = "login_failed"
ACCOUNT_LOCKED = "account_locked"
ADDRESS_BLOCKED = "address_blocked"
TOKEN_REUSED = "token_reused"
UPLOAD_REFUSED = "upload_refused"
UNLOCKED = "unlocked"
ACCOUNT_DELETED = "account_deleted"     # an admin deleted an account for good (src/core/account_delete.py)
KINDS = (LOGIN_FAILED, ACCOUNT_LOCKED, ADDRESS_BLOCKED, TOKEN_REUSED, UPLOAD_REFUSED, UNLOCKED, ACCOUNT_DELETED)


def record(db: Session, kind: str, *, user_id: int | None = None, account: str = "", address: str = "",
           detail: str = "", now: datetime | None = None) -> None:
    """Add one event to `db` (the caller commits, with its own change); old events go at the same time."""
    try:
        now = now or utcnow()
        db.execute(delete(SecurityEvent).where(SecurityEvent.at <= now - timedelta(days=SECURITY_LOG_DAYS)))
        db.add(SecurityEvent(at=now, kind=kind, user_id=user_id, account=account[:300], address=address[:300],
                             detail=detail[:DETAIL_CHARS]))
    except Exception as exc:  # noqa: BLE001 — the request goes on without its log row
        log.warning("security log: %s not recorded: %s", kind, exc)


def record_now(kind: str, **fields: Any) -> None:
    """For a request whose own transaction is rolled back (a refused upload ends the job's creation): the event is
    written in a session of its own."""
    from src.core.db import session_scope

    try:
        with session_scope() as session:
            record(session, kind, **fields)
    except Exception as exc:  # noqa: BLE001
        log.warning("security log: %s not recorded: %s", kind, exc)


def recent(db: Session, *, kind: str | None = None, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
    """Newest first, for the admin's page."""
    query = select(SecurityEvent).order_by(SecurityEvent.at.desc(), SecurityEvent.id.desc())
    if kind:
        query = query.where(SecurityEvent.kind == kind)
    return [{"id": e.id, "at": e.at.isoformat() if e.at else None, "kind": e.kind, "user_id": e.user_id,
             "account": e.account, "address": e.address, "detail": e.detail}
            for e in db.scalars(query.limit(max(1, min(limit, LIST_LIMIT))))]
