"""Chat history: thread + project metadata, per user, so titles/pins/projects sync across devices.
A thread's messages stay its jobs (grouped by the job's thread_id); these rows hold only metadata."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import get_current_user, get_db
from src.core.models_db import User
from src.jobs import history_store
from web.schemas import (
    FeedbackPayload,
    HistoryImportPayload,
    ProjectCreatePayload,
    ProjectRenamePayload,
    ThreadUpsertPayload,
)

router = APIRouter(tags=["history"])


@router.get("/api/history")
def get_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return history_store.list_history(db, user.id)


@router.post("/api/history/import")
def import_history_endpoint(
    payload: HistoryImportPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """One-time migration of a user's browser-stored history (no-op once they have any thread row)."""
    result = history_store.import_history(db, user.id, threads=payload.threads, projects=payload.projects)
    db.commit()
    return result


@router.put("/api/threads/{thread_id}")
def upsert_thread_endpoint(
    thread_id: str,
    payload: ThreadUpsertPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    fields = payload.model_fields_set
    kwargs: dict[str, Any] = {}
    if "title" in fields:
        kwargs["title"] = payload.title
    if "pinned" in fields:
        kwargs["pinned"] = payload.pinned
    if "project_id" in fields:  # present (even as null) ⇒ change it; absent ⇒ leave as-is
        kwargs["project_id"] = payload.project_id
    try:
        row = history_store.upsert_thread(db, user.id, thread_id, **kwargs)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    db.commit()
    return history_store._thread_dict(row)


@router.delete("/api/threads/{thread_id}")
def delete_thread_endpoint(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ok = history_store.delete_thread(db, user.id, thread_id)
    db.commit()
    return {"ok": ok}


@router.post("/api/feedback")
def submit_feedback(
    payload: FeedbackPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Record the user's 👍/👎 (and optional note) on one answer (job)."""
    job_id = payload.job_id.strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id required")
    rating = history_store.set_feedback(db, user.id, job_id, payload.rating, payload.note)
    db.commit()
    return {"ok": True, "rating": rating}


@router.post("/api/projects")
def create_project_endpoint(
    payload: ProjectCreatePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = history_store.create_project(db, user.id, payload.name)
    db.commit()
    return history_store._project_dict(row)


@router.put("/api/projects/{project_id}")
def rename_project_endpoint(
    project_id: str,
    payload: ProjectRenamePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = history_store.rename_project(db, user.id, project_id, payload.name)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    db.commit()
    return history_store._project_dict(row)


@router.delete("/api/projects/{project_id}")
def delete_project_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ok = history_store.delete_project(db, user.id, project_id)
    db.commit()
    return {"ok": ok}
