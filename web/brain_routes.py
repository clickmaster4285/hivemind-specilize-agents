"""Knowledge brain API (doc/BRAIN_DESIGN.md §7). Mounted by web/server.py."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from src.core.auth import get_current_user, require_admin
from src.brain.db import BrainDBError, connect
from src.brain.embed import EmbedError
from src.brain.retrieve import MAX_PASSAGES, search
from src.core.models_db import User
from web.schemas import TeamDocPayload

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


# ── team knowledge (BACKLOG L30 T4): documents for one team ───────────────────────────────────────────────────

def _team_for(user: User, team: str | None) -> str:
    """Admins look at any team's documents; a member only at their own team's."""
    wanted = (team or "").strip() if user.role == "admin" else (user.team or "")
    if not wanted:
        raise HTTPException(status_code=400, detail="Choose a team" if user.role == "admin" else "You are in no team")
    return wanted


@router.get("/team-docs")
def team_documents(team: str | None = None, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """A team's documents (members see their own team's; admins choose the team)."""
    from src.brain import team_docs
    from src.core.config import Config

    wanted = _team_for(user, team)
    try:
        return {"team": wanted, "documents": team_docs.listing(Config.from_env(), wanted)}
    except BrainDBError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/team-docs", status_code=201)
def upload_team_document(payload: TeamDocPayload, admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Add a file (PDF, Word, Markdown, text, HTML) to a team's knowledge; the same name again replaces it. Its text
    is read and embedded now (a long PDF takes a while), then the team's searches find it."""
    import base64
    import binascii

    from src.brain import team_docs
    from src.core.config import Config

    try:
        data = base64.b64decode(payload.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="The file did not arrive whole — try again") from exc
    try:
        return team_docs.add(Config.from_env(), payload.team.strip(), payload.filename.strip(), data,
                             uploaded_by=admin.username)
    except team_docs.TeamDocError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (BrainDBError, EmbedError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/team-docs/{doc_id}")
def remove_team_document(doc_id: int, team: str, admin: User = Depends(require_admin)) -> dict[str, Any]:
    from src.brain import team_docs
    from src.core.config import Config

    if not team_docs.remove(Config.from_env(), team.strip(), doc_id):
        raise HTTPException(status_code=404, detail="That document is not in this team's knowledge")
    return {"ok": True}

