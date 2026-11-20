"""Settings → Logs (BACKLOG L30 T5; admins): health and alerts, the app log's warnings and errors. The security log and
the server command log keep their own routes (web/routers/users.py)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core import health
from src.core.auth import get_db, require_admin
from src.core.config import Config
from src.core.models_db import User

router = APIRouter(prefix="/api/admin", tags=["logs"])

APP_LOG_LIMIT = 500


@router.get("/health")
def health_checks(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Each part of the system: ok / warn / bad, and what to do when not ok — worst first."""
    found = health.checks(db, Config.from_env())
    return {"checks": found, "problems": sum(c["status"] != health.OK for c in found)}


@router.get("/app-log")
def app_log(level: Literal["warning", "error"] = "warning", limit: int = Query(200, ge=1, le=APP_LOG_LIMIT),
            _admin: User = Depends(require_admin)) -> dict[str, Any]:
    """The newest warnings and errors of the API's own log (logs/api.log)."""
    return {"lines": health.app_log(level, limit)}
