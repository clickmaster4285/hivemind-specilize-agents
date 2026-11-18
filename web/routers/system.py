"""System status: GET /api/health. Anyone may ask whether the API is up ({"ok": true}); only a signed-in user gets the
details (model, Ollama, queue, image service, memory budget — src/core/status.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.core.auth import get_db, optional_user
from src.core.config import Config
from src.core.status import details

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    if optional_user(request, db) is None:
        return {"ok": True}
    return {"ok": True, **details(Config.from_env(), db)}
