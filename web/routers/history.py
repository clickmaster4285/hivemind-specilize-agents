"""Chat history: thread + project metadata, per user, so titles/pins/projects sync across devices.
A thread's messages stay its jobs (grouped by the job's thread_id); these rows hold only metadata."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from src.core.auth import get_current_user, get_db
from src.core.config import Config
from src.core.models_db import ChatThreadRow, User
from src.jobs import history_store
from src.jobs.chat_search import DEFAULT_RESULTS, search_chats
from src.jobs.chat_memory import compact_chat, memory_status, summarize_chat
from src.llm.llm_client import LLMError
from web.schemas import (
    FeedbackPayload,
    HistoryImportPayload,
    ProjectCreatePayload,
    ProjectRenamePayload,
    SharedLinkPayload,
    ThreadContinuePayload,
    ThreadUpsertPayload,
)

router = APIRouter(tags=["history"])


@router.get("/api/history")
def get_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return history_store.list_history(db, user.id)


@router.get("/api/search")
def search_endpoint(
    q: str,
    limit: int = DEFAULT_RESULTS,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Search the caller's own chats, questions and answers (BACKLOG L26 H3; admins too see only their own)."""
    return search_chats(db, user.id, q, limit)


@router.post("/api/history/import")
def import_history_endpoint(
    payload: HistoryImportPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """One-time migration of a user's browser-stored history (no-op once they have any thread row)."""
    result = history_store.import_history(db, user.id, threads=payload.threads, projects=payload.projects)
    db.commit()
    return result


@router.put("/api/threads/{thread_id}")
def upsert_thread_endpoint(
    thread_id: str,
    payload: ThreadUpsertPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    fields = payload.model_fields_set
    kwargs: dict[str, Any] = {}
    if "title" in fields:
        kwargs["title"] = payload.title
    if "pinned" in fields:
        kwargs["pinned"] = payload.pinned
    if "project_id" in fields:  # present (even as null) ⇒ change it; absent ⇒ leave as-is
        kwargs["project_id"] = payload.project_id
    try:
        row = history_store.upsert_thread(db, user.id, thread_id, **kwargs)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    db.commit()
    return history_store._thread_dict(row)


def _chat_summary(thread_id: str, user: User) -> str:
    """The whole chat as one summary (a model call — the route runs in a worker thread)."""
    try:
        return summarize_chat(Config.from_env(), thread_id, user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=f"The model could not summarise this chat: {exc}") from exc


@router.post("/api/threads/{thread_id}/seen", status_code=204)
def thread_seen(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    """The user opened the chat: a scheduled run's new result is read (ISSUES_LOG O99)."""
    row = db.get(ChatThreadRow, thread_id)
    if row is not None and row.user_id == user.id and row.unread:
        row.unread = False
        db.commit()
    return Response(status_code=204)


@router.post("/api/threads/{thread_id}/summary")
def summary_endpoint(thread_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """"Save summary": the chat summarised (goal, key facts and decisions, open questions)."""
    return {"summary": _chat_summary(thread_id, user)}


@router.get("/api/threads/{thread_id}/memory")
def memory_endpoint(thread_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """How full the chat's memory is and how much room is left before its oldest messages are summarised (O76)."""
    try:
        return memory_status(Config.from_env(), thread_id, user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/threads/{thread_id}/compact")
def compact_endpoint(thread_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """/compact: summarise all but the last exchange now (a model call — the route runs in a worker thread)."""
    config = Config.from_env()
    try:
        folded = compact_chat(config, thread_id, user.id)
        return {"folded": folded, "memory": memory_status(config, thread_id, user.id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=f"The model could not summarise this chat: {exc}") from exc


@router.post("/api/threads/{thread_id}/continue")
def continue_endpoint(
    thread_id: str,
    payload: ThreadContinuePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """"Summarize & continue in a new chat": the new chat starts with this chat's summary as its memory."""
    summary = _chat_summary(thread_id, user)
    old = history_store._owned_thread(db, thread_id, user.id)
    try:
        row = history_store.upsert_thread(db, user.id, payload.new_thread_id,
                                          title=f"{old.title if old else 'Chat'} (continued)"[:200],
                                          project_id=old.project_id if old else None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    row.summary, row.continued_from = summary, thread_id
    db.commit()
    return {"thread": history_store._thread_dict(row), "summary": summary}


@router.delete("/api/threads/{thread_id}")
def delete_thread_endpoint(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ok = history_store.delete_thread(db, user.id, thread_id)
    db.commit()
    return {"ok": ok}


@router.post("/api/feedback")
def submit_feedback(
    payload: FeedbackPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Record the user's 👍/👎 (and optional note) on one answer (job)."""
    job_id = payload.job_id.strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id required")
    rating = history_store.set_feedback(db, user.id, job_id, payload.rating, payload.note)
    db.commit()
    return {"ok": True, "rating": rating}


@router.post("/api/projects")
def create_project_endpoint(
    payload: ProjectCreatePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = history_store.create_project(db, user.id, payload.name)
    db.commit()
    return history_store._project_dict(row)


@router.put("/api/projects/{project_id}")
def rename_project_endpoint(
    project_id: str,
    payload: ProjectRenamePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    row = history_store.rename_project(db, user.id, project_id, payload.name)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    db.commit()
    return history_store._project_dict(row)


@router.delete("/api/projects/{project_id}")
def delete_project_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    ok = history_store.delete_project(db, user.id, project_id)
    db.commit()
    return {"ok": ok}


# ── sharing chats and projects by link (BACKLOG L30 T4): the owner's side ──────────────────────────────────

def _link_route(kind: str, object_id: str, user: User, db: Session, action: str) -> dict[str, Any]:
    from src.jobs import sharing

    try:
        if action == "state":
            return sharing.link_state(db, user, kind, object_id)
        if action == "make":
            token = sharing.make_link(db, user, kind, object_id)
            db.commit()
            return {"token": token, **sharing.link_state(db, user, kind, object_id)}
        sharing.stop_sharing(db, user, kind, object_id)
        db.commit()
        return {"shared": False, "readers": []}
    except sharing.ShareError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/threads/{thread_id}/link")
def thread_link(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Whether my chat has a share link, and who opened it."""
    return _link_route("chat", thread_id, user, db, "state")


@router.post("/api/threads/{thread_id}/link")
def make_thread_link(thread_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    """A read-only link to my chat for anyone signed in (shown once; a new one replaces the old)."""
    return _link_route("chat", thread_id, user, db, "make")


@router.delete("/api/threads/{thread_id}/link")
def stop_thread_link(thread_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Stop sharing: the link stops working and everyone who opened it loses access."""
    return _link_route("chat", thread_id, user, db, "stop")


@router.get("/api/projects/{project_id}/link")
def project_link(project_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict[str, Any]:
    return _link_route("project", project_id, user, db, "state")


@router.post("/api/projects/{project_id}/link")
def make_project_link(project_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)) -> dict[str, Any]:
    """A read-only link to every chat of my project (also ones added later)."""
    return _link_route("project", project_id, user, db, "make")


@router.delete("/api/projects/{project_id}/link")
def stop_project_link(project_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)) -> dict[str, Any]:
    return _link_route("project", project_id, user, db, "stop")


# ── … and the reader's side ───────────────────────────────────────────────────────────────────────────────

@router.post("/api/shared/open")
def open_shared_link(payload: SharedLinkPayload, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Someone signed in opened a share link: they may read the chat (or the project's chats) from now on."""
    from src.jobs.sharing import ShareError, open_link

    try:
        found = open_link(db, user, payload.token)
    except ShareError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return found


def _shared_thread(db: Session, user: User, thread_id: str) -> ChatThreadRow:
    from src.jobs.sharing import can_read_thread

    thread = db.get(ChatThreadRow, thread_id)
    if thread is None or not can_read_thread(db, user, thread_id):
        raise HTTPException(status_code=404, detail="This chat is not shared with you (or no longer)")
    return thread


@router.get("/api/shared/{thread_id}")
def shared_chat(thread_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """A shared chat to read: its title, owner and messages (each job; the page loads their events by id)."""
    from src.jobs.chat_memory import _thread_jobs
    from src.jobs.jobs import job_to_dict

    thread = _shared_thread(db, user, thread_id)
    owner = db.get(User, thread.user_id)
    jobs = _thread_jobs(db, thread_id, thread.user_id, before=None, after=None)
    return {"thread_id": thread_id, "title": thread.title, "owner": owner.username if owner else "—",
            "jobs": [job_to_dict(j, slim=True) for j in jobs]}


@router.post("/api/shared/{thread_id}/continue")
def continue_shared(thread_id: str, payload: ThreadContinuePayload, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict[str, Any]:
    """"Continue in my own chat": a new chat of mine that starts from the shared chat's summary (its owner's chat is
    not touched)."""
    thread = _shared_thread(db, user, thread_id)
    title = thread.title
    try:
        summary = summarize_chat(Config.from_env(), thread_id, thread.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=f"The model could not summarise this chat: {exc}") from exc
    try:
        row = history_store.upsert_thread(db, user.id, payload.new_thread_id, title=f"{title} (continued)"[:200])
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    row.summary, row.continued_from = summary, None       # not linked: the source is someone else's chat
    db.commit()
    return {"thread": history_store._thread_dict(row), "summary": summary}

