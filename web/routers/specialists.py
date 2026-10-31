"""Specialists (src/llm/specialists.py): everyone may read them; only admins change them. They are
shared by all users and decide which tools (e.g. network_scan) a specialist may run."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.core.auth import get_current_user, require_admin
from src.core.models_db import User
from src.llm.specialists import COLLECTIONS, SpecialistStore
from web.schemas import SpecialistPayload, SpecialistUpdatePayload

router = APIRouter(prefix="/api/specialists", tags=["specialists"])
store = SpecialistStore()


@router.get("")
def list_specialists(_user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [s.to_dict() for s in store.load_all()]


@router.get("/options")
def specialist_options(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """What the edit form offers: the assistant's tools and the knowledge-base collections."""
    from src.llm.assistant import TOOLS

    return {"tools": [{"name": t.name, "description": t.description.split(". ")[0]} for t in TOOLS],
            "collections": list(COLLECTIONS)}


@router.post("")
def create_specialist(payload: SpecialistPayload, _user: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        specialist = store.create(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return specialist.to_dict()


@router.put("/{specialist_id}")
def update_specialist(
    specialist_id: str, payload: SpecialistUpdatePayload, _user: User = Depends(require_admin)
) -> dict[str, Any]:
    try:
        specialist = store.update(specialist_id, payload.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return specialist.to_dict()


@router.delete("/{specialist_id}")
def delete_specialist(specialist_id: str, _user: User = Depends(require_admin)) -> dict[str, str]:
    try:
        store.delete(specialist_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "id": specialist_id}


@router.post("/reset")
def reset_specialists(_user: User = Depends(require_admin)) -> list[dict[str, Any]]:
    return [s.to_dict() for s in store.reset_defaults()]
