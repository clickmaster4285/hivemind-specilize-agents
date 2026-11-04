"""ORM models for users, jobs, events, and model registry."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="member")  # member|admin
    team: Mapped[str | None] = mapped_column(String(40), nullable=True)  # e.g. "IT", "marketing" — segments query analytics
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    daily_job_quota: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled (disabled can't sign in)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    jobs: Mapped[list[Job]] = relationship(back_populates="user")


class RefreshToken(Base):
    """One refresh token of a sign-in (BACKLOG L16 S1); only its SHA-256 is kept. A sign-in is a family:
    each refresh spends the token and issues the next one in the same family. A spent token shown again
    means someone kept a copy, so the whole family is revoked — and with it every access token of it."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    family: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20))  # auto|chat|agentic (legacy rows may be debate|mixed)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    query: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    model_plan_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    user: Mapped[User] = relationship(back_populates="jobs")
    events: Mapped[list[JobEvent]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    event_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="events")

    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_event_seq"),)


class ModelRecord(Base):
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    backend: Mapped[str] = mapped_column(String(40), default="local_ollama")
    vram_class: Mapped[str] = mapped_column(String(20), default="medium")  # small|medium|large
    role: Mapped[str] = mapped_column(String(20), default="general")  # fast|strong|general
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    vision: Mapped[bool] = mapped_column(Boolean, default=False)
    image_gen: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommandLog(Base):
    """Audit trail of commands the agent ran on a server (SERVER_ACCESS_DECISION.md §8). One row per
    command that changed, or would have changed, a server — the host and command are kept, never the
    password. Read-only diagnose commands are not logged here (they change nothing)."""

    __tablename__ = "command_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    server_label: Mapped[str] = mapped_column(String(80))
    host: Mapped[str] = mapped_column(String(255))
    command: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(20))   # ran | confirmed | cancelled | refused | auto
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)   # first part of the output
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProjectRow(Base):
    """A folder that groups chats, per user. Membership is thread.project_id, not stored here."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Untitled project")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Feedback(Base):
    """A user's 👍/👎 (and optional note) on one answer. One row per (user, job); re-rating updates it."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rating: Mapped[str] = mapped_column(String(8))              # "up" | "down"
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_feedback_user_job"),)


class LlmUsage(Base):
    """One model call: who/what made it and what it cost (BACKLOG L11). Tokens come from Ollama's own counts
    (prompt_eval_count / eval_count); a call stopped mid-answer has no counts, so it is estimated."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    step: Mapped[str] = mapped_column(String(60))               # the code that asked: chat_runner, doc_writer, …
    model: Mapped[str] = mapped_column(String(120))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SeoProject(Base):
    """A website a user tracks (BACKLOG L15 P5): re-audited every `every_days`, each run saved as an SeoAudit."""

    __tablename__ = "seo_projects"
    __table_args__ = (UniqueConstraint("user_id", "host", name="uq_seo_project_user_host"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    host: Mapped[str] = mapped_column(String(255))
    start_url: Mapped[str] = mapped_column(String(1000))
    every_days: Mapped[int] = mapped_column(Integer, default=7)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SeoAudit(Base):
    """One site audit's result, kept so the next audit can say what changed (BACKLOG L15 P5).
    areas_json = {"On-page": 70, …}; checks_json = {"Exactly one H1": {"area", "score" 0..1, "failing": [urls]}}."""

    __tablename__ = "seo_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    host: Mapped[str] = mapped_column(String(255), index=True)
    start_url: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    total: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(20))
    pages: Mapped[int] = mapped_column(Integer)
    areas_json: Mapped[str] = mapped_column(Text)
    checks_json: Mapped[str] = mapped_column(Text)
    scheduled: Mapped[bool] = mapped_column(Boolean, default=False)      # run by the tracker, not asked in chat


class SkillRow(Base):
    """A skill in someone's "Yours" list (BACKLOG L14): owner_id = the user it belongs to, or None = a team skill every
    user sees (admins manage those). tools_json / inputs_json = JSON lists of strings. Catalog skills are files
    (skills/<name>/SKILL.md); adding one copies it here, `origin` = its catalog name."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_skill_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(60), default="")
    specialist: Mapped[str] = mapped_column(String(40), default="")
    tools_json: Mapped[str] = mapped_column(Text, default="[]")
    inputs_json: Mapped[str] = mapped_column(Text, default="[]")
    instructions: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatThreadRow(Base):
    """A chat's metadata (title, pin, project, deletion), per user, so history syncs across devices.
    The chat's messages are its jobs, grouped by the job payload's thread_id — not stored here."""

    __tablename__ = "chat_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)   # == client thread id / job thread_id
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Chat memory (BACKLOG L12): the earlier part of a long chat folded into a running summary, and the
    # creation time of the last message it covers; a chat continued from another one starts from its summary.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    continued_from: Mapped[str | None] = mapped_column(String(36), nullable=True)
