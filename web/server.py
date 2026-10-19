"""FastAPI gateway: auth, agents, models, jobs."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.specialists import COLLECTIONS, SpecialistStore
from src.attachments import image_path
from src.artifacts import resolve_job_file
from src.auth import (
    bootstrap_admin,
    create_access_token,
    get_current_user,
    get_db,
    require_admin,
    verify_password,
)
from src.config import Config
from src.db import init_db
from src.jobs import (
    cancel_job,
    create_job,
    estimate_wait_seconds,
    get_job,
    job_to_dict,
    list_events_after,
    queue_depth,
    soft_delete_job,
    soft_delete_jobs,
)
from src.llm_client import LLMClient
from src.model_registry import list_models, sync_models_from_ollama
from src.models_db import Job, ModelRecord, User
from src.worker import start_inprocess_worker
from web.brain_routes import router as brain_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
store = SpecialistStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from src.db import session_scope

    config = Config.from_env()
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.artifacts_dir.mkdir(parents=True, exist_ok=True)
    init_db(config)
    bootstrap_admin(config)
    with session_scope(config) as session:
        sync_models_from_ollama(session, config, probe_details=True)
    start_inprocess_worker(config)
    # Keep the knowledge brain fresh: re-check sources whose refresh_every has passed
    from src.brain.scheduler import start_scheduler

    start_scheduler(config)
    if config.image_warm_on_start:
        # SDXL lives in its own process; start it (and load the model) in the background
        import threading

        from src.image_client import ImageServiceClient

        threading.Thread(target=ImageServiceClient(config).warm, name="image-service-warm", daemon=True).start()
    yield


app = FastAPI(title="MulteAgent API", version="0.4.0", lifespan=lifespan)
app.include_router(brain_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginPayload(BaseModel):
    username: str
    password: str


class SpecialistPayload(BaseModel):
    name: str
    covers: str
    instructions: str = ""
    tools: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    enabled: bool = True
    sort_order: int | None = None
    accent: str = "#6b8cae"


class SpecialistUpdatePayload(BaseModel):
    name: str | None = None
    covers: str | None = None
    instructions: str | None = None
    tools: list[str] | None = None
    collections: list[str] | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    accent: str | None = None


class SoftDeleteJobsPayload(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class ImageAttachmentIn(BaseModel):
    filename: str = "image.png"
    mime: str = "image/png"
    data: str


class ConversationTurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class JobCreatePayload(BaseModel):
    query: str = ""
    # auto | chat | agentic (older clients may still send debate/mixed: treated as auto, see create_job)
    mode: str = "auto"
    # auto = the assistant picks a specialist · none = no specialist · or a specialist id (src/specialists.py)
    specialist: str = "auto"
    model: str | None = None
    allow_overflow: bool = True
    priority: int = 100
    images: list[ImageAttachmentIn] = Field(default_factory=list)
    # Prior turns in this chat (so “write that as docx” sees previous answers)
    conversation: list[ConversationTurnIn] = Field(default_factory=list)
    # Explicit composer actions: describe | generate | edit | inpaint | doc_gen
    force_intent: str | None = None
    image_profile: Literal["fast", "quality", "balanced"] | None = None
    image_strength: float | None = None


class ModelUpdatePayload(BaseModel):
    enabled: bool | None = None
    role: Literal["fast", "strong", "cloud", "general"] | None = None
    vram_class: Literal["small", "medium", "large"] | None = None
    max_concurrency: int | None = None


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    from src.image_client import ImageServiceClient

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


@app.get("/api/auth/sso/status")
def sso_status() -> dict[str, Any]:
    config = Config.from_env()
    return {
        "enabled": config.sso_enabled,
        "message": (
            "SSO hook ready — set SSO_ENABLED=true and wire your IdP callback in a future deploy."
            if config.sso_enabled
            else "SSO disabled. Using local JWT accounts."
        ),
    }


@app.post("/api/auth/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "username": user.username, "role": user.role},
    }


@app.post("/api/auth/register")
def register_disabled() -> dict[str, Any]:
    """Multi-user registration is deferred — use default admin for testing."""
    raise HTTPException(
        status_code=403,
        detail="User registration is disabled for now. Sign in as admin (default: admin / admin123).",
    )


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "daily_job_quota": user.daily_job_quota,
    }


# Specialists (src/specialists.py): everyone may read them; only admins change them — they are
# shared by all users and decide which tools (network_scan) a specialist may run.
@app.get("/api/specialists")
def list_specialists(_user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [s.to_dict() for s in store.load_all()]


@app.get("/api/specialists/options")
def specialist_options(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """What the edit form offers: the assistant's tools and the knowledge-base collections."""
    from src.assistant import TOOLS

    return {"tools": [{"name": t.name, "description": t.description.split(". ")[0]} for t in TOOLS],
            "collections": list(COLLECTIONS)}


@app.post("/api/specialists")
def create_specialist(payload: SpecialistPayload, _user: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        specialist = store.create(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return specialist.to_dict()


@app.put("/api/specialists/{specialist_id}")
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


@app.delete("/api/specialists/{specialist_id}")
def delete_specialist(specialist_id: str, _user: User = Depends(require_admin)) -> dict[str, str]:
    try:
        store.delete(specialist_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "id": specialist_id}


@app.post("/api/specialists/reset")
def reset_specialists(_user: User = Depends(require_admin)) -> list[dict[str, Any]]:
    return [s.to_dict() for s in store.reset_defaults()]


# Server logins for the session (src/server_vault.py): kept in this process's memory only — never on
# disk, in the database, a log or a job. Admins only; each admin sees only the servers they added.
class ServerLoginPayload(BaseModel):
    label: str = ""
    host: str
    port: int = 22
    username: str
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None


@app.get("/api/servers")
def list_servers(user: User = Depends(require_admin)) -> list[dict[str, Any]]:
    from src import server_vault

    return [s.public() for s in server_vault.list_servers(user.id)]


@app.post("/api/servers")
def add_server(payload: ServerLoginPayload, user: User = Depends(require_admin)) -> dict[str, Any]:
    """Log in once (learn the server's host key and access level), then keep the login for the session."""
    from src import linux_audit, server_vault

    try:
        server_vault.check_host(payload.host)
        trial = server_vault.ServerLogin(id="trial", label=payload.label or payload.host, host=payload.host.strip(),
                                         port=payload.port, username=payload.username.strip(),
                                         password=payload.password or None, private_key=payload.private_key or None,
                                         key_passphrase=payload.key_passphrase or None)
        info = linux_audit.test_login(trial)
        login = server_vault.add(user.id, label=trial.label, host=trial.host, port=trial.port,
                                 username=trial.username, password=trial.password, private_key=trial.private_key,
                                 key_passphrase=trial.key_passphrase, host_key=info["host_key"])
    except (server_vault.VaultError, linux_audit.AuditError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**login.public(), "os": info["os"], "access": info["access"]}


@app.delete("/api/servers/{server_id}")
def remove_server(server_id: str, user: User = Depends(require_admin)) -> dict[str, str]:
    from src import server_vault

    if not server_vault.remove(user.id, server_id):
        raise HTTPException(status_code=404, detail="server not found (the session may have ended)")
    return {"status": "removed", "id": server_id}


@app.delete("/api/servers")
def forget_servers(user: User = Depends(get_current_user)) -> dict[str, int]:
    """Logout: forget every server login of this user."""
    from src import server_vault

    return {"forgotten": server_vault.clear(user.id)}


@app.get("/api/models")
def get_models(
    refresh: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    sync_models_from_ollama(db, Config.from_env(), probe_details=refresh)
    db.commit()
    return list_models(db)


@app.patch("/api/models/{model_name:path}")
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


@app.post("/api/jobs")
def create_job_endpoint(
    payload: JobCreatePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    config = Config.from_env()
    try:
        job = create_job(
            db,
            user=user,
            query=payload.query,
            mode=payload.mode,
            payload={
                "model": payload.model,
                "specialist": payload.specialist,
                "allow_overflow": payload.allow_overflow,
                "images": [img.model_dump() for img in payload.images],
                "conversation": [t.model_dump() for t in payload.conversation],
                "force_intent": payload.force_intent,
                "image_profile": payload.image_profile,
                "image_strength": payload.image_strength,
            },
            config=config,
            priority=payload.priority if user.role == "admin" else 100,
        )
        db.commit()
        db.refresh(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_to_dict(job)


@app.get("/api/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 100,
) -> list[dict[str, Any]]:
    stmt = (
        select(Job)
        .where(Job.deleted_at.is_(None))
        .order_by(Job.created_at.desc())
        .limit(min(limit, 100))
    )
    if user.role != "admin":
        stmt = stmt.where(Job.user_id == user.id)
    return [job_to_dict(j) for j in db.scalars(stmt).all()]


@app.post("/api/jobs/soft-delete")
def soft_delete_jobs_endpoint(
    payload: SoftDeleteJobsPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Soft-delete one or more jobs (conversation turns). Data is retained."""
    ids = [jid.strip() for jid in payload.job_ids if jid and jid.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="job_ids required")
    deleted = soft_delete_jobs(db, ids, user=user)
    db.commit()
    return {"status": "soft_deleted", "deleted_ids": deleted, "count": len(deleted)}


@app.delete("/api/jobs/{job_id}")
def soft_delete_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    soft_delete_job(db, job)
    db.commit()
    return {"status": "soft_deleted", "id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return job_to_dict(job)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        job = cancel_job(db, job)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # ends a model call that is writing right now, instead of waiting for the whole answer
    from src import live

    live.stop(job.id)
    return job_to_dict(job)


@app.get("/api/jobs/{job_id}/history")
def job_events_history(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return all stored events for a job (for chat history replay)."""
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    rows = list_events_after(db, job_id, 0)
    events = []
    for row in rows:
        try:
            events.append(json.loads(row.event_json))
        except json.JSONDecodeError:
            events.append({"type": "raw", "body": row.event_json})
    return {"job": job_to_dict(job), "events": events}


@app.get("/api/jobs/{job_id}/attachments/{filename}")
def job_attachment(
    job_id: str,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Download an image or generated document for this job."""
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    config = Config.from_env()
    payload = json.loads(job.payload_json or "{}")
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    match = next((a for a in attachments if a.get("filename") == filename), None)
    mime = "application/octet-stream"
    try:
        if match and match.get("relpath"):
            # Images use attachments.image_path; docs may use files/ via resolve_job_file.
            rel = str(match["relpath"])
            mime = str(match.get("mime") or mime)
            try:
                path = image_path(config, rel)
            except (ValueError, FileNotFoundError):
                path = resolve_job_file(config, job_id, filename)
        else:
            path = resolve_job_file(config, job_id, filename)
            from src.artifacts import mime_for

            mime = mime_for(filename)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Attachment missing") from exc
    return FileResponse(path, media_type=mime, filename=filename)


@app.get("/api/jobs/{job_id}/events")
async def job_events_stream(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    async def event_generator():
        from src import live
        from src.db import session_scope

        last_seq = 0
        terminal = {"succeeded", "failed", "cancelled"}
        idle_rounds = 0
        sent_live: dict[str, str] = {}      # agent_id → text already streamed
        finished: set[str] = set()          # agents whose final answer was sent: no more live text
        while True:
            # read the live text BEFORE the stored events: text cleared just before a final event
            # then cannot be sent after it
            live_now = live.snapshot(job_id)
            payloads: list[tuple[int, str]] = []
            status = "queued"
            with session_scope() as session:
                current = get_job(session, job_id)
                if current is None:
                    yield f"data: {json.dumps({'type': 'fatal', 'error': 'Job missing'})}\n\n"
                    break
                status = current.status
                rows = list_events_after(session, job_id, last_seq)
                # Copy fields inside the session to avoid DetachedInstanceError.
                payloads = [(row.seq, row.event_json) for row in rows]
            for seq, event_json in payloads:
                last_seq = seq
                if '"agent_done"' in event_json:
                    try:
                        finished.add(str(json.loads(event_json).get("agent_id") or ""))
                    except json.JSONDecodeError:
                        pass
                yield f"data: {event_json}\n\n"
            for agent_id, text in live_now.items():
                if agent_id not in finished and sent_live.get(agent_id) != text:
                    sent_live[agent_id] = text
                    yield f"data: {json.dumps({'type': 'agent_stream', 'agent_id': agent_id, 'text': text})}\n\n"
            if status in terminal:
                # Keep draining until events stop arriving (avoid cutting off last msgs).
                idle_rounds = idle_rounds + 1 if not payloads else 0
                if idle_rounds >= 5:
                    break
            else:
                idle_rounds = 0
            # Wake early when append_event notifies; fall back to short poll.
            from src.job_events import wait_job

            await asyncio.to_thread(wait_job, job_id, 0.35)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    config = Config.from_env()
    uvicorn.run("web.server:app", host=config.api_host, port=config.api_port, reload=False)


if __name__ == "__main__":
    main()
