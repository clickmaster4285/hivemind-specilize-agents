"""Request/response payloads for the web API, in one place so routers and tests share them."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── auth & users ───────────────────────────────────────────────────────────
class LoginPayload(BaseModel):
    username: str
    password: str


class UserCreatePayload(BaseModel):
    username: str
    password: str
    role: Literal["member", "admin"] = "member"
    team: str | None = None            # e.g. "IT", "marketing" — free text, segments query analytics
    email: str | None = None
    phone: str | None = None
    daily_job_quota: int | None = None  # None keeps the model default


class UserUpdatePayload(BaseModel):
    # Every field is optional: only the ones sent are changed. For team/email/phone an empty string
    # clears the field; omit the key to leave it as it is. `password`, when given, resets the password.
    role: Literal["member", "admin"] | None = None
    team: str | None = None
    email: str | None = None
    phone: str | None = None
    daily_job_quota: int | None = None
    status: Literal["active", "disabled"] | None = None
    password: str | None = None


# ── specialists ────────────────────────────────────────────────────────────
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


# ── servers (SSH vault) ─────────────────────────────────────────────────────
class ServerLoginPayload(BaseModel):
    label: str = ""
    host: str
    port: int = 22
    username: str
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None
    mode: str = "audit"            # audit | confirm | trusted (server_vault.MODES)


class ServerModePayload(BaseModel):
    mode: str


# ── models ──────────────────────────────────────────────────────────────────
class ModelUpdatePayload(BaseModel):
    enabled: bool | None = None
    role: Literal["fast", "strong", "cloud", "general"] | None = None
    vram_class: Literal["small", "medium", "large"] | None = None
    max_concurrency: int | None = None


# ── chat history: threads, projects, feedback ───────────────────────────────
class SoftDeleteJobsPayload(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class ThreadUpsertPayload(BaseModel):
    # all optional: only the fields sent are changed (title=rename, pinned=pin, project_id=move)
    title: str | None = None
    pinned: bool | None = None
    project_id: str | None = None


class ProjectCreatePayload(BaseModel):
    name: str = "Untitled project"


class ProjectRenamePayload(BaseModel):
    name: str


class HistoryImportPayload(BaseModel):
    threads: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)


class FeedbackPayload(BaseModel):
    job_id: str
    rating: str                 # "up" | "down"
    note: str | None = None


# ── jobs ───────────────────────────────────────────────────────────────────
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
    # Which chat this job belongs to (client-generated id). Lets history regroup a conversation's jobs
    # into one chat after a reload/login, instead of one chat per job.
    thread_id: str | None = None
    # "deep" = the Deep research toggle: research the topic across many sources (src/tools/deep_research.py)
    depth: str | None = None
    # Options toggle: offer a few candidate approaches to pick from before writing the full answer
    options: bool = False
    # A follow-up answer (picked an approach card, or answered a clarify form): routes normally — web
    # search still available — but the model won't ask to clarify again.
    followup: bool = False
    # "Try again, better": the user's reason for a 👎, steered into this regenerate (feedback phase 2)
    refine: str | None = None


class ConfirmPayload(BaseModel):
    command_id: str
    approved: bool
