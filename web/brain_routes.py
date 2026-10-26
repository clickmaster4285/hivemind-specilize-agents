"""Knowledge brain API (doc/BRAIN_DESIGN.md §7). Mounted by web/server.py."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from src.core.auth import get_current_user
from src.brain.db import BrainDBError, connect
from src.brain.embed import EmbedError
from src.brain.retrieve import MAX_PASSAGES, search
from src.core.models_db import User

router = APIRouter(prefix="/api/brain", tags=["brain"])


def _account(user: User) -> str:
    """Row-level security key: each user only ever sees public rows + their own private rows."""
    return str(user.id)


@router.get("/search")
def brain_search(
    q: str = Query(min_length=2, max_length=1000, description="The question"),
    collection: list[str] | None = Query(None, description="Limit to collections, e.g. it"),
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid",
    limit: int = Query(MAX_PASSAGES, ge=1, le=20),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Debug view of retrieval: which passages a question finds, and why."""
    try:
        result = search(q, account_id=_account(user), collections=collection, mode=mode, limit=limit)
    except (BrainDBError, EmbedError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "query": result.query,
        "mode": result.mode,
        "enough_evidence": result.enough_evidence,
        "best_similarity": result.best_similarity,
        "exact_terms": result.exact_terms,
        "exact_match": result.exact_match,
        "timings_ms": result.timings_ms,
        "passages": [
            {
                "chunk_id": p.chunk_id,
                "title": p.title,
                "url": p.url,
                "heading_path": p.heading_path,
                "source": p.source,
                "publisher": p.publisher,
                "licence": p.licence,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                "score": round(p.score, 5),
                "similarity": round(p.similarity, 4) if p.similarity is not None else None,
                "vector_rank": p.vector_rank,
                "keyword_rank": p.keyword_rank,
                "content": p.content,
            }
            for p in result.passages
        ],
    }


@router.get("/stats")
def brain_stats(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """What the brain knows: one line per source, plus totals."""
    try:
        with connect(account_id=_account(user)) as conn:
            cur = conn.cursor(row_factory=dict_row)
            sources = cur.execute("SELECT * FROM brain.source_status ORDER BY slug").fetchall()
    except BrainDBError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    for row in sources:
        for key in ("last_checked_at", "last_changed_at"):
            if row.get(key):
                row[key] = row[key].isoformat()
    return {
        "sources": sources,
        "totals": {
            "sources": len(sources),
            "active_docs": sum(r["active_docs"] for r in sources),
            "chunks": sum(r["chunks"] for r in sources),
        },
    }
