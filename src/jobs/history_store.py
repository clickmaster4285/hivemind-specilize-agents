"""Chat-thread and project metadata, stored per user so history syncs across devices.

A thread's messages are its jobs, grouped by the job payload's ``thread_id`` (see
``src.jobs.jobs.create_job``). These rows hold only the metadata the UI lets the user change:
a chat's title, pin, project and deletion, and a project's name. Everything here is scoped
to one user; a row owned by another user is treated as not found.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.models_db import ChatThreadRow, ProjectRow

# marks "field not provided" in a partial update, so project_id can be set to None explicitly
_UNSET: Any = object()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _thread_dict(row: ChatThreadRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "project_id": row.project_id,
        "pinned": bool(row.pinned),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _project_dict(row: ProjectRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _owned_thread(session: Session, thread_id: str, user_id: int) -> ChatThreadRow | None:
    row = session.get(ChatThreadRow, thread_id)
    return row if row is not None and row.user_id == user_id else None


def _owned_project(session: Session, project_id: str, user_id: int) -> ProjectRow | None:
    row = session.get(ProjectRow, project_id)
    return row if row is not None and row.user_id == user_id else None


def list_history(session: Session, user_id: int) -> dict[str, list[dict[str, Any]]]:
    """All of a user's active (non-deleted) threads and projects."""
    threads = session.scalars(
        select(ChatThreadRow).where(ChatThreadRow.user_id == user_id, ChatThreadRow.deleted_at.is_(None))
    ).all()
    projects = session.scalars(
        select(ProjectRow).where(ProjectRow.user_id == user_id, ProjectRow.deleted_at.is_(None))
    ).all()
    return {
        "threads": [_thread_dict(t) for t in threads],
        "projects": [_project_dict(p) for p in projects],
    }


def touch_thread(session: Session, user_id: int, thread_id: str, *, title: str | None = None) -> ChatThreadRow:
    """Create the thread row on first message, or bump its updated_at. Keeps a user-set title."""
    row = session.get(ChatThreadRow, thread_id)
    if row is None:
        row = ChatThreadRow(id=thread_id, user_id=user_id, title=(title or "New chat").strip()[:200] or "New chat")
        session.add(row)
    elif row.user_id == user_id:
        if title and row.title in ("", "New chat"):
            row.title = title.strip()[:200] or row.title
        row.updated_at = _utcnow()
    return row


def upsert_thread(
    session: Session,
    user_id: int,
    thread_id: str,
    *,
    title: str | None = None,
    pinned: bool | None = None,
    project_id: Any = _UNSET,
) -> ChatThreadRow:
    """Create the thread row if missing, then apply the fields that were provided."""
    row = _owned_thread(session, thread_id, user_id)
    if row is None:
        if session.get(ChatThreadRow, thread_id) is not None:
            raise PermissionError("thread belongs to another user")
        row = ChatThreadRow(id=thread_id, user_id=user_id)
        session.add(row)
    if title is not None:
        row.title = title.strip()[:200] or row.title or "New chat"
    if pinned is not None:
        row.pinned = bool(pinned)
    if project_id is not _UNSET:
        row.project_id = project_id or None
    row.deleted_at = None
    row.updated_at = _utcnow()
    return row


def delete_thread(session: Session, user_id: int, thread_id: str) -> bool:
    """Soft-delete a thread row. Its jobs are soft-deleted separately by the caller."""
    row = _owned_thread(session, thread_id, user_id)
    if row is None:
        return False
    if row.deleted_at is None:
        row.deleted_at = _utcnow()
    row.pinned = False
    row.updated_at = _utcnow()
    return True


def create_project(session: Session, user_id: int, name: str) -> ProjectRow:
    from uuid import uuid4

    row = ProjectRow(id=str(uuid4()), user_id=user_id, name=name.strip()[:200] or "Untitled project")
    session.add(row)
    return row


def rename_project(session: Session, user_id: int, project_id: str, name: str) -> ProjectRow | None:
    row = _owned_project(session, project_id, user_id)
    if row is None or row.deleted_at is not None:
        return None
    row.name = name.strip()[:200] or row.name
    return row


def import_history(
    session: Session,
    user_id: int,
    *,
    threads: list[dict[str, Any]],
    projects: list[dict[str, Any]],
) -> dict[str, Any]:
    """One-time migration of a user's browser-stored history. No-op once they have any thread row,
    so re-posting is safe. Project ids are preserved so threads' project_id still points at them."""
    existing = session.scalar(
        select(func.count()).select_from(ChatThreadRow).where(ChatThreadRow.user_id == user_id)
    )
    if existing:
        return {"imported": False, "reason": "already has history"}
    for p in projects:
        pid = str(p.get("id") or "").strip()
        if not pid or session.get(ProjectRow, pid) is not None:
            continue
        session.add(ProjectRow(id=pid, user_id=user_id, name=str(p.get("name") or "").strip()[:200] or "Untitled project"))
    session.flush()
    count = 0
    for t in threads:
        tid = str(t.get("id") or "").strip()
        if not tid or session.get(ChatThreadRow, tid) is not None:
            continue
        row = upsert_thread(
            session,
            user_id,
            tid,
            title=(t.get("title") if isinstance(t.get("title"), str) else None),
            pinned=bool(t.get("pinned")),
            project_id=(t.get("project_id") or t.get("projectId") or None),
        )
        if t.get("deleted_at") or t.get("deletedAt"):
            row.deleted_at = _utcnow()
        count += 1
    return {"imported": True, "threads": count}


def delete_project(session: Session, user_id: int, project_id: str) -> bool:
    """Soft-delete a project and detach its threads (they return to 'no project')."""
    row = _owned_project(session, project_id, user_id)
    if row is None:
        return False
    if row.deleted_at is None:
        row.deleted_at = _utcnow()
    for thread in session.scalars(
        select(ChatThreadRow).where(ChatThreadRow.user_id == user_id, ChatThreadRow.project_id == project_id)
    ).all():
        thread.project_id = None
        thread.updated_at = _utcnow()
    return True
