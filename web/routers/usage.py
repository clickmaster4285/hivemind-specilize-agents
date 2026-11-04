"""Token usage (BACKLOG L11): admins see every user's model usage, members only their own."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.auth import get_db, require_module
from src.core.models_db import User
from src.llm.usage import summary

router = APIRouter(tags=["usage"])
USAGE = require_module("usage")      # the routes of the usage module (src/core/modules.py)


@router.get("/api/usage")
def usage_summary(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(USAGE),
) -> dict[str, Any]:
    """Tokens in/out, model time and answers over the last `days`, per user and per step."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return {"days": days, "all_users": user.role == "admin",
            **summary(db, since, user_id=None if user.role == "admin" else user.id)}
