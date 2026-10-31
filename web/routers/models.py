"""Model registry: list the installed models (synced from Ollama) and let an admin tune one."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.auth import get_current_user, get_db, require_admin
from src.core.config import Config
from src.core.models_db import ModelRecord, User
from src.llm.model_registry import list_models, sync_models_from_ollama
from web.schemas import ModelUpdatePayload

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def get_models(
    refresh: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    sync_models_from_ollama(db, Config.from_env(), probe_details=refresh)
    db.commit()
    return list_models(db)


@router.patch("/{model_name:path}")
def patch_model(
    model_name: str,
    payload: ModelUpdatePayload,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    row = db.scalar(select(ModelRecord).where(ModelRecord.name == model_name))
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")
    data = payload.model_dump(exclude_none=True)
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return {
        "name": row.name,
        "backend": row.backend,
        "vram_class": row.vram_class,
        "role": row.role,
        "max_concurrency": row.max_concurrency,
        "enabled": row.enabled,
        "vision": bool(getattr(row, "vision", False)),
        "image_gen": bool(getattr(row, "image_gen", False)),
        "available": bool(getattr(row, "available", True)),
    }
