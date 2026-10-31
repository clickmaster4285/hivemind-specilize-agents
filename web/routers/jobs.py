"""Jobs: create a chat/agentic job, list a user's jobs, read/cancel/confirm one, and stream its events.

Every single-job route scopes to the caller: a member may only touch their own jobs (admins see all).
That one check lives in `_owned_job` instead of being repeated in each handler.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.auth import get_current_user, get_db
from src.core.config import Config
from src.core.models_db import Job, User
from src.jobs.jobs import (
    cancel_job,
    create_job,
    get_job,
    job_to_dict,
    list_events_after,
    soft_delete_job,
    soft_delete_jobs,
)
from src.tools.artifacts import resolve_job_file
from src.tools.attachments import image_path
from web.schemas import ConfirmPayload, JobCreatePayload, SoftDeleteJobsPayload

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _owned_job(db: Session, job_id: str, user: User, *, forbid_status: int = 403) -> Job:
    """The job if it exists and the caller may see it, else 404/403 (admins may see any job).
    confirm uses forbid_status=404 so it never reveals that someone else's job id exists."""
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=forbid_status,
                            detail="Job not found" if forbid_status == 404 else "Forbidden")
    return job


@router.post("")
def create_job_endpoint(
    payload: JobCreatePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    config = Config.from_env()
    try:
        job = create_job(
            db,
            user=user,
            query=payload.query,
            mode=payload.mode,
            payload={
                "model": payload.model,
                "specialist": payload.specialist,
                "allow_overflow": payload.allow_overflow,
                "images": [img.model_dump() for img in payload.images],
                "conversation": [t.model_dump() for t in payload.conversation],
                "force_intent": payload.force_intent,
                "image_profile": payload.image_profile,
                "image_strength": payload.image_strength,
                "thread_id": payload.thread_id,
                "depth": payload.depth,
                "options": payload.options,
                "followup": payload.followup,
                "refine": payload.refine,
            },
            config=config,
            priority=payload.priority if user.role == "admin" else 100,
        )
        db.commit()
        db.refresh(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_to_dict(job)


@router.get("")
def list_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 100,
) -> list[dict[str, Any]]:
    stmt = (
        select(Job)
        .where(Job.deleted_at.is_(None))
        .order_by(Job.created_at.desc())
        .limit(min(limit, 100))
    )
    if user.role != "admin":
        stmt = stmt.where(Job.user_id == user.id)
    return [job_to_dict(j) for j in db.scalars(stmt).all()]


@router.post("/soft-delete")
def soft_delete_jobs_endpoint(
    payload: SoftDeleteJobsPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Soft-delete one or more jobs (conversation turns). Data is retained."""
    ids = [jid.strip() for jid in payload.job_ids if jid and jid.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="job_ids required")
    deleted = soft_delete_jobs(db, ids, user=user)
    db.commit()
    return {"status": "soft_deleted", "deleted_ids": deleted, "count": len(deleted)}


@router.delete("/{job_id}")
def soft_delete_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = _owned_job(db, job_id, user)
    soft_delete_job(db, job)
    db.commit()
    return {"status": "soft_deleted", "id": job_id}


@router.get("/{job_id}")
def get_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return job_to_dict(_owned_job(db, job_id, user))


@router.post("/{job_id}/cancel")
def cancel_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = _owned_job(db, job_id, user)
    try:
        job = cancel_job(db, job)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # ends a model call that is writing right now, instead of waiting for the whole answer
    from src.jobs import live

    live.stop(job.id)
    return job_to_dict(job)


@router.post("/{job_id}/confirm")
def confirm_command_endpoint(
    job_id: str,
    payload: ConfirmPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """The user clicked Run / Cancel on a command the agent asked to run (operate_server)."""
    _owned_job(db, job_id, user, forbid_status=404)
    from src.jobs import live

    found = live.answer_confirm(job_id, payload.command_id, payload.approved)
    return {"ok": found, "approved": payload.approved}


@router.get("/{job_id}/history")
def job_events_history(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return all stored events for a job (for chat history replay)."""
    job = _owned_job(db, job_id, user)
    rows = list_events_after(db, job_id, 0)
    events = []
    for row in rows:
        try:
            events.append(json.loads(row.event_json))
        except json.JSONDecodeError:
            events.append({"type": "raw", "body": row.event_json})
    return {"job": job_to_dict(job), "events": events}


@router.get("/{job_id}/attachments/{filename}")
def job_attachment(
    job_id: str,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Download an image or generated document for this job."""
    job = _owned_job(db, job_id, user)
    config = Config.from_env()
    payload = json.loads(job.payload_json or "{}")
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    match = next((a for a in attachments if a.get("filename") == filename), None)
    mime = "application/octet-stream"
    try:
        if match and match.get("relpath"):
            # Images use attachments.image_path; docs may use files/ via resolve_job_file.
            rel = str(match["relpath"])
            mime = str(match.get("mime") or mime)
            try:
                path = image_path(config, rel)
            except (ValueError, FileNotFoundError):
                path = resolve_job_file(config, job_id, filename)
        else:
            path = resolve_job_file(config, job_id, filename)
            from src.tools.artifacts import mime_for

            mime = mime_for(filename)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Attachment missing") from exc
    return FileResponse(path, media_type=mime, filename=filename)


@router.get("/{job_id}/events")
async def job_events_stream(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    _owned_job(db, job_id, user)

    async def event_generator():
        from src.jobs import live
        from src.core.db import session_scope

        last_seq = 0
        terminal = {"succeeded", "failed", "cancelled"}
        idle_rounds = 0
        sent_live: dict[str, str] = {}      # agent_id → text already streamed
        finished: set[str] = set()          # agents whose final answer was sent: no more live text
        while True:
            # read the live text BEFORE the stored events: text cleared just before a final event
            # then cannot be sent after it
            live_now = live.snapshot(job_id)
            payloads: list[tuple[int, str]] = []
            status = "queued"
            with session_scope() as session:
                current = get_job(session, job_id)
                if current is None:
                    yield f"data: {json.dumps({'type': 'fatal', 'error': 'Job missing'})}\n\n"
                    break
                status = current.status
                rows = list_events_after(session, job_id, last_seq)
                # Copy fields inside the session to avoid DetachedInstanceError.
                payloads = [(row.seq, row.event_json) for row in rows]
            for seq, event_json in payloads:
                last_seq = seq
                if '"agent_done"' in event_json:
                    try:
                        finished.add(str(json.loads(event_json).get("agent_id") or ""))
                    except json.JSONDecodeError:
                        pass
                yield f"data: {event_json}\n\n"
            for agent_id, text in live_now.items():
                if agent_id not in finished and sent_live.get(agent_id) != text:
                    sent_live[agent_id] = text
                    yield f"data: {json.dumps({'type': 'agent_stream', 'agent_id': agent_id, 'text': text})}\n\n"
            if status in terminal:
                # Keep draining until events stop arriving (avoid cutting off last msgs).
                idle_rounds = idle_rounds + 1 if not payloads else 0
                if idle_rounds >= 5:
                    break
            else:
                idle_rounds = 0
            # Wake early when append_event notifies; fall back to short poll.
            from src.jobs.job_events import wait_job

            await asyncio.to_thread(wait_job, job_id, 0.35)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
