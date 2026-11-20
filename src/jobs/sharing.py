"""Chats and projects shared read-only by link (BACKLOG L30 T4; the user's choice, 2026-11-20: "just share, send the
link; the other opens it, views it, and can chat on if they want").

The owner makes a link for a chat — or a project, which covers every chat in it, also ones added later. Anyone
signed in who opens it becomes a reader: they read it under "Shared with me" (messages, sources, files), never change
it, and may "Continue in my own chat" (a new chat of theirs from its summary). A new link replaces the old one;
"Stop sharing" ends the link and every reader's access at once. Only the token's SHA-256 is stored.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Literal

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from src.core.auth import token_digest
from src.core.models_db import USER_FORMER, ChatShare, ChatThreadRow, Job, ProjectRow, User

Kind = Literal["chat", "project"]
LINK_TOKEN_BYTES = 24


class ShareError(ValueError):
    """Not the owner's, or a link that does not work (any more)."""


def thread_of(job: Job) -> str | None:
    return json.loads(job.payload_json or "{}").get("thread_id")


def _column(kind: Kind):
    return ChatShare.thread_id if kind == "chat" else ChatShare.project_id


def _owned(session: Session, owner: User, kind: Kind, object_id: str) -> None:
    row = session.get(ChatThreadRow if kind == "chat" else ProjectRow, object_id)
    if row is None or row.user_id != owner.id or row.deleted_at is not None:
        raise ShareError(f"{kind} not found")


def can_read_thread(session: Session, user: User, thread_id: str | None) -> bool:
    """This user opened a link to the chat, or to the project it is in. (Owners and admins are checked by the caller.)"""
    if not thread_id:
        return False
    thread = session.get(ChatThreadRow, thread_id)
    if thread is None or thread.deleted_at is not None:
        return False
    target = ChatShare.thread_id == thread_id
    if thread.project_id:
        target = or_(target, ChatShare.project_id == thread.project_id)
    return session.scalar(select(ChatShare.id).where(target, ChatShare.owner_id == thread.user_id,
                                                     ChatShare.user_id == user.id).limit(1)) is not None


def can_read_job(session: Session, user: User, job: Job) -> bool:
    return can_read_thread(session, user, thread_of(job))


def link_state(session: Session, owner: User, kind: Kind, object_id: str) -> dict[str, Any]:
    """Whether the chat / project has a live link, and who opened it."""
    _owned(session, owner, kind, object_id)
    rows = session.execute(select(ChatShare, User.username).outerjoin(User, User.id == ChatShare.user_id)
                           .where(_column(kind) == object_id, ChatShare.owner_id == owner.id)).all()
    return {"shared": any(s.token_hash for s, _ in rows),
            "readers": sorted(name for s, name in rows if s.user_id and name)}


def make_link(session: Session, owner: User, kind: Kind, object_id: str) -> str:
    """A new link token (shown once; the caller commits). It replaces the old link; readers keep their access."""
    _owned(session, owner, kind, object_id)
    session.execute(delete(ChatShare).where(_column(kind) == object_id, ChatShare.owner_id == owner.id,
                                            ChatShare.token_hash.is_not(None)))
    token = secrets.token_urlsafe(LINK_TOKEN_BYTES)
    session.add(ChatShare(owner_id=owner.id, token_hash=token_digest(token),
                          **({"thread_id": object_id} if kind == "chat" else {"project_id": object_id})))
    session.flush()
    return token


def stop_sharing(session: Session, owner: User, kind: Kind, object_id: str) -> set[int]:
    """End the link and every reader's access (the caller commits). → the readers, to tell them."""
    _owned(session, owner, kind, object_id)
    readers = set(session.scalars(select(ChatShare.user_id).where(_column(kind) == object_id,
                                                                  ChatShare.user_id.is_not(None))))
    session.execute(delete(ChatShare).where(_column(kind) == object_id, ChatShare.owner_id == owner.id))
    return readers


def open_link(session: Session, user: User, token: str) -> dict[str, Any]:
    """Someone signed in opened a link: they become a reader (the caller commits). → where to go: the chat, or the
    project's newest chat."""
    link = session.scalar(select(ChatShare).where(ChatShare.token_hash == token_digest(token)))
    owner = session.get(User, link.owner_id) if link else None
    if link is None or owner is None or owner.status == USER_FORMER:
        raise ShareError("This link does not work any more — ask for a new one")
    kind: Kind = "chat" if link.thread_id else "project"
    object_id = link.thread_id or link.project_id
    if user.id != owner.id and session.scalar(select(ChatShare.id).where(
            _column(kind) == object_id, ChatShare.user_id == user.id).limit(1)) is None:
        session.add(ChatShare(owner_id=owner.id, user_id=user.id,
                              **({"thread_id": object_id} if kind == "chat" else {"project_id": object_id})))
        session.flush()
    if kind == "chat":
        return {"thread_id": object_id, "own": user.id == owner.id}
    newest = session.scalar(select(ChatThreadRow.id).where(ChatThreadRow.project_id == object_id,
                                                           ChatThreadRow.deleted_at.is_(None))
                            .order_by(ChatThreadRow.updated_at.desc()).limit(1))
    if newest is None:
        raise ShareError("This project has no chats yet")
    return {"thread_id": newest, "own": user.id == owner.id}


def shared_with_me(session: Session, user: User) -> list[dict[str, Any]]:
    """Every chat this user may read through a link they opened — the chat's, or its project's — newest first."""
    rows = session.scalars(select(ChatShare).where(ChatShare.user_id == user.id)).all()
    direct = {r.thread_id for r in rows if r.thread_id}
    projects = {r.project_id for r in rows if r.project_id}
    found = select(ChatThreadRow, User.username, ProjectRow.name).join(User, User.id == ChatThreadRow.user_id) \
        .outerjoin(ProjectRow, ProjectRow.id == ChatThreadRow.project_id) \
        .where(ChatThreadRow.deleted_at.is_(None), User.status != USER_FORMER, ChatThreadRow.user_id != user.id,
               or_(ChatThreadRow.id.in_(direct or {""}), ChatThreadRow.project_id.in_(projects or {""})))
    return [{"thread_id": t.id, "title": t.title, "owner": owner, "project": project,
             "updated_at": t.updated_at.isoformat() if t.updated_at else None}
            for t, owner, project in session.execute(found.order_by(ChatThreadRow.updated_at.desc())).all()]

