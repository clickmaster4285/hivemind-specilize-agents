"""Library: the files the app made for the caller — documents, spreadsheets, slides, pages, generated pictures —
across all their chats, newest first (BACKLOG L24 U5, the /library page).

Each job records its files in payload["attachments"] (worker.py on `artifact_ready`, image_gen for generated
pictures). Every entry the app made carries a `kind`; the user's own uploads have none, and the answer's web
illustrations are kind "web" (found online, not made), so both stay out. Files are opened through the job's own
attachment route, which checks ownership again.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.auth import get_current_user, get_db
from src.core.models_db import Job, User

router = APIRouter(prefix="/api/library", tags=["library"])

# pictures the answer borrowed from the web are shown with the answer, not listed as the user's files
FOUND_NOT_MADE = "web"
# how many of the caller's latest chat turns are searched for files: a page of files, not an archive export
MAX_JOBS_SCANNED = 1000


def made_files(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The files a job's payload says the app made (see the module docstring)."""
    rows = payload.get("attachments")
    if not isinstance(rows, list):
        return []
    return [
        r for r in rows
        if isinstance(r, dict) and r.get("filename") and r.get("kind") and r.get("kind") != FOUND_NOT_MADE
    ]


@router.get("")
def list_library(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """The caller's own files (admins too: this is "my files", not an audit view)."""
    stmt = (
        select(Job)
        .where(Job.user_id == user.id, Job.deleted_at.is_(None))
        .order_by(Job.created_at.desc())
        .limit(MAX_JOBS_SCANNED)
    )
    out: list[dict[str, Any]] = []
    for job in db.scalars(stmt).all():
        try:
            payload = json.loads(job.payload_json or "{}")
        except json.JSONDecodeError:
            continue
        for f in made_files(payload):
            out.append({
                "job_id": job.id,
                "thread_id": payload.get("thread_id"),
                "query": job.query,
                "filename": f["filename"],
                "mime": f.get("mime"),
                "bytes": f.get("bytes"),
                "kind": f["kind"],
                "created_at": job.created_at.isoformat() if job.created_at else None,
            })
    return out
