"""Scheduled tasks (BACKLOG L26 H1): the caller's own schedules — list, add, run now, pause / resume, delete.
The scheduler thread that runs them is src/jobs/schedules.py."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from src.core.auth import get_current_user, get_db
from src.core.config import Config
from src.core.models_db import ChatThreadRow, User
from src.jobs import schedules
from web.schemas import ScheduleCreatePayload, ScheduleUpdatePayload

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _own(db: Session, user: User, schedule_id: int):
    try:
        return schedules.own(db, user, schedule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Schedule not found") from exc


def _out(db: Session, row) -> dict[str, Any]:
    chat = db.get(ChatThreadRow, row.thread_id)
    return schedules.to_dict(row, chat.title if chat else "")


@router.get("")
def list_schedules(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return schedules.listing(db, user)


@router.get("/unread")
def unread_results(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Scheduled chats with a new result the user has not opened (the browser's notification check)."""
    return schedules.unread(db, user)


@router.post("")
def create_schedule(payload: ScheduleCreatePayload, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        row = schedules.create(db, user, **payload.model_dump(), config=Config.from_env())
    except schedules.ScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return _out(db, row)


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: int, payload: ScheduleUpdatePayload, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = schedules.set_enabled(db, _own(db, user, schedule_id), payload.enabled)
    db.commit()
    return _out(db, row)


@router.post("/{schedule_id}/run")
def run_schedule_now(schedule_id: int, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    row = _own(db, user, schedule_id)
    try:
        schedules.run_now(db, row, Config.from_env())
    except schedules.ScheduleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _out(db, row)


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    db.delete(_own(db, user, schedule_id))
    db.commit()
    return Response(status_code=204)
