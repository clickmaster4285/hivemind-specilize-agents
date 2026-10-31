"""System status: the health probe the UI polls (LLM reachability, queue depth, image pipeline)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.auth import get_db
from src.core.config import Config
from src.jobs.jobs import estimate_wait_seconds, queue_depth
from src.llm.llm_client import LLMClient

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    from src.images.image_client import ImageServiceClient

    config = Config.from_env()
    client = LLMClient(config)
    return {
        "ok": True,
        "llm_reachable": client.ping(),
        "model": config.llm_model,
        "base_url": config.llm_base_url,
        "queue_depth": queue_depth(db),
        "estimated_wait_seconds": estimate_wait_seconds(db, config),
        "llm_slots": config.llm_slots,
        "image_slots": config.image_slots,
        "image_profile": config.image_profile,
        "image_pipeline": ImageServiceClient(config).status(),
        "sso_enabled": config.sso_enabled,
        "auth_required": True,
    }
