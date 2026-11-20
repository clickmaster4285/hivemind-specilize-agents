"""Request/response payloads for the web API, in one place so routers and tests share them."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── auth & users ───────────────────────────────────────────────────────────
class LoginPayload(BaseModel):
    username: str
    password: str
    captcha_id: str | None = None      # needed after a few failures (L16 S2b)
    captcha_answer: str | None = None


class UserCreatePayload(BaseModel):
    username: str
    password: str
    role: Literal["member", "admin"] = "member"
    team: str | None = None            # e.g. "IT", "marketing" — free text, segments query analytics
    email: str | None = None
    phone: str | None = None
    daily_job_quota: int | None = None  # None keeps the model default
    modules: list[str] | None = None   # src/core/modules.py ids; None = the defaults for a new member


class UserDeletePayload(BaseModel):
    """Delete an account for good: the admin types its username, so a slip of the mouse cannot do it."""

    confirm: str = Field(max_length=200)


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
    modules: list[str] | None = None   # the full new list of module ids (src/core/modules.py)


class UserBulkChanges(BaseModel):
    """What a bulk action may change (one person's password, email or phone is never set in bulk)."""

    model_config = ConfigDict(extra="forbid")
    role: Literal["member", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None
    team: str | None = None
    daily_job_quota: int | None = Field(default=None, ge=0)
    modules: list[str] | None = None


class UserBulkPayload(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    changes: UserBulkChanges


class PasswordChangePayload(BaseModel):
    """My account → Change password: the current one proves it is the person, not a borrowed open session."""

    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class ProfilePayload(BaseModel):
    """My account: what a person may change about themselves (an empty string clears it)."""

    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)


class PasswordResetPayload(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


class SignupPayload(BaseModel):
    """Self sign-up (BACKLOG L16 S7): the account waits for an admin's approval."""
    username: str
    email: str
    password: str
    phone: str | None = None
    invite: str | None = None          # the token from an invite link
    captcha_id: str | None = None      # always needed (L16 S2b)
    captcha_answer: str | None = None


class InvitePayload(BaseModel):
    email: str | None = None           # when given, the sign-up must use this email
    modules: list[str] | None = None   # given on approval; None = the defaults for a new member


class SignupSettingsPayload(BaseModel):
    open: bool                         # anyone who can open the page may sign up (else invite links only)


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


# ── skills (src/llm/skills.py) ─────────────────────────────────────────────
class SkillPayload(BaseModel):
    name: str
    description: str
    instructions: str
    title: str = ""
    category: str = ""
    specialist: str = ""
    tools: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    enabled: bool = True
    team: bool = False             # for everyone (admins only) instead of only for me


class SkillUpdatePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    title: str | None = None
    category: str | None = None
    specialist: str | None = None
    tools: list[str] | None = None
    inputs: list[str] | None = None
    enabled: bool | None = None


class SkillDraftPayload(BaseModel):
    job_id: str                    # the finished task to learn a skill from (H2)


# a SKILL.md is a page of instructions: the model's context caps what is kept anyway, so a bigger "skill" is refused
# before it is parsed (BACKLOG L16 S3)
SKILL_FILE_CHARS = 100_000


class SkillUploadPayload(BaseModel):
    text: str = Field(max_length=SKILL_FILE_CHARS)     # a SKILL.md file's content
    team: bool = False


class SkillAddPayload(BaseModel):
    team: bool = False


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
    kind: Literal["ssh", "nvr", "network"] = "ssh"   # server_vault.KINDS: SSH server, camera recorder, router/switch


class DeviceShareIn(BaseModel):
    """One share of an admin's device: a person (user_id) or a team, and what the agent may do for them."""

    user_id: int | None = None
    team: str | None = Field(default=None, max_length=40)
    mode: Literal["audit", "confirm"] = "audit"


class DeviceSharesPayload(BaseModel):
    shares: list[DeviceShareIn] = Field(default_factory=list, max_length=500)


class ServerModePayload(BaseModel):
    mode: str


# ── models ──────────────────────────────────────────────────────────────────
class ModelUpdatePayload(BaseModel):
    enabled: bool | None = None
    role: Literal["fast", "strong", "general"] | None = None
    vram_class: Literal["small", "medium", "large"] | None = None
    max_concurrency: int | None = None


# ── chat history: threads, projects, feedback ───────────────────────────────
class SoftDeleteJobsPayload(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class SharedLinkPayload(BaseModel):
    """A share link's token, opened by someone signed in (BACKLOG L30 T4)."""

    token: str = Field(min_length=10, max_length=200)


class ThreadContinuePayload(BaseModel):
    # the client-made id of the new chat that continues this one
    new_thread_id: str = Field(min_length=1, max_length=36)


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
    # "Fresh answer" on an answer given earlier: write a new one instead of the kept one (BACKLOG L31)
    fresh: bool = False
    # the browser's IANA time zone: the job's prompts tell the time in it (was dropped before, ISSUES_LOG O92)
    client_time_zone: str | None = Field(default=None, max_length=64)


class ChoicePayload(BaseModel):
    """The options the user picked on a choice card (deploy_project); the job checks they were offered."""

    choice_id: str = Field(min_length=1, max_length=40)
    picked: list[str] = Field(default_factory=list, max_length=40)


class ConfirmPayload(BaseModel):
    command_id: str
    approved: bool


class ExportPayload(BaseModel):
    """"Download as": the answer exactly as shown (markdown) and the format to convert it to."""
    format: Literal["md", "txt", "docx", "pdf", "pptx", "html"]
    text: str = Field(min_length=1, max_length=400_000)


class ScheduleCreatePayload(BaseModel):
    """A question repeated in a chat every hour / day / week (BACKLOG L26 H1, src/jobs/schedules.py)."""

    prompt: str = Field(min_length=1)
    every: Literal["hour", "day", "week"]
    at_minute: int = 0
    weekday: int | None = None
    time_zone: str = Field(min_length=1, max_length=64)


class ScheduleUpdatePayload(BaseModel):
    enabled: bool
