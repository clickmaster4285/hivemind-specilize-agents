"""Skills — saved, named instruction packs for a repeatable task (BACKLOG L14, ISSUES_LOG O13).

A specialist is the domain (who answers); a skill is the job (how to do one thing: an incident report, a campaign
plan, a monthly SEO check). Modelled on claude.ai's Customize → Skills:
  Discover   the built-in catalog — files in skills/<name>/SKILL.md, shipped with the code
  Yours      table `skills`: team skills (owner None, admins manage them, everyone sees them) and personal skills
             (only their owner sees them). "+" copies a catalog skill into Yours, where it can be edited.
A skill runs only when the user picks it or types /name (no automatic picking yet — user's decision).

Format: the open Agent Skills SKILL.md — YAML front matter (`name`, `description`, optional `allowed-tools`,
`metadata`: title / category / specialist / inputs) and the instructions as Markdown — so skills written for other
assistants can be uploaded and ours exported. Tools named there that MulteAgent does not have are dropped; nothing
in a skill is ever executed, it is text the model reads.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import yaml
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.core.config import ROOT, Config
from src.core.conversation import estimate_tokens
from src.core.db import session_scope
from src.core.models_db import SkillRow, User, utcnow
from src.llm.specialists import GENERAL, Specialist, SpecialistStore, tool_names

CATALOG_DIR = ROOT / "skills"
SKILL_FILE = "SKILL.md"
# the Agent Skills format's own limits: name 1-64 lowercase letters, digits and single hyphens; description ≤ 1024
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
# a skill's instructions join the system prompt; the rest of the context is for the assistant's own prompt, the chat
# memory (CONTEXT_HISTORY_SHARE), sources and the answer
SKILL_CONTEXT_SHARE = 0.2
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)(.*)\Z", re.S)


class SkillError(ValueError):
    """A skill that cannot be saved: the message says what to fix."""


@dataclass
class Skill:
    name: str                                       # its /name — lowercase-with-hyphens
    description: str                                # what it does and when to use it
    instructions: str                               # the steps and the output format (Markdown)
    title: str = ""                                 # display name; from the name when empty
    category: str = ""
    specialist: str = ""                            # the specialist that answers with it; "" = the usual pick
    tools: list[str] = field(default_factory=list)  # assistant tools it may use
    inputs: list[str] = field(default_factory=list)  # what it needs from the user (asked once when missing)
    id: int | None = None                           # set once it is in someone's Yours list
    team: bool = False                              # a team skill (everyone sees it) rather than a personal one
    origin: str = ""                                # the catalog skill it was added from
    enabled: bool = True

    def __post_init__(self) -> None:
        self.title = self.title or self.name.replace("-", " ").capitalize()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        """The skill as a SKILL.md file (download / share)."""
        front: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.tools:
            front["allowed-tools"] = " ".join(self.tools)
        meta = {"title": self.title, "category": self.category, "specialist": self.specialist,
                "inputs": "; ".join(self.inputs)}
        if any(meta.values()):
            front["metadata"] = {k: v for k, v in meta.items() if v}
        head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000).strip()
        return f"---\n{head}\n---\n\n{self.instructions.strip()}\n"


def _strings(value: Any, split: str) -> list[str]:
    """A list in YAML, or one string split on `split` (the format's metadata values are strings)."""
    items = value if isinstance(value, list) else str(value or "").split(split)
    return [s for s in (str(i).strip() for i in items) if s]


def instruction_budget(config: Config) -> int:
    """Tokens a skill's instructions may take."""
    return int(config.llm_num_ctx * SKILL_CONTEXT_SHARE)


def check(skill: Skill, *, budget: int) -> Skill:
    """Validate and tidy a skill: a usable name, a description, instructions within the budget; tools and a
    specialist MulteAgent does not have are dropped (a skill written for another assistant still loads)."""
    skill.name = skill.name.strip().lower()
    if not (NAME.match(skill.name) and len(skill.name) <= NAME_MAX):
        raise SkillError("the name must be lowercase letters, digits and hyphens (e.g. incident-report), "
                         f"at most {NAME_MAX} characters")
    skill.description = " ".join(skill.description.split())
    if not skill.description:
        raise SkillError("add a description: what the skill does and when to use it")
    if len(skill.description) > DESCRIPTION_MAX:
        raise SkillError(f"the description is {len(skill.description)} characters; the limit is {DESCRIPTION_MAX}")
    skill.instructions = skill.instructions.strip()
    if not skill.instructions:
        raise SkillError("add the instructions: the steps to follow and what the answer should look like")
    if estimate_tokens(skill.instructions) > budget:
        raise SkillError(f"the instructions are about {estimate_tokens(skill.instructions)} tokens; the model's "
                         f"context leaves room for {budget} — shorten them")
    known = set(tool_names())
    skill.tools = [t for t in dict.fromkeys(skill.tools) if t in known]
    skill.specialist = skill.specialist if skill.specialist in {s.id for s in SpecialistStore().load_all()} else ""
    skill.title, skill.category = skill.title.strip()[:120], skill.category.strip()[:60]
    return skill


def parse_skill(text: str, *, budget: int) -> Skill:
    """A SKILL.md file → a checked Skill."""
    match = _FRONT.match(text.lstrip("﻿"))
    if not match:
        raise SkillError("a SKILL.md starts with front matter between --- lines (name and description)")
    try:
        front = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"the front matter is not valid YAML: {exc}") from exc
    if not isinstance(front, dict):
        raise SkillError("the front matter must be key: value lines")
    meta = front.get("metadata") if isinstance(front.get("metadata"), dict) else {}
    return check(Skill(
        name=str(front.get("name") or ""), description=str(front.get("description") or ""),
        instructions=match.group(2), title=str(meta.get("title") or ""), category=str(meta.get("category") or ""),
        specialist=str(meta.get("specialist") or ""), tools=_strings(front.get("allowed-tools"), " "),
        inputs=_strings(meta.get("inputs"), ";"),
    ), budget=budget)


def catalog(config: Config | None = None) -> list[Skill]:
    """Discover: every built-in skill, by category then title. A broken file is a bug — it raises."""
    budget = instruction_budget(config or Config.from_env())
    skills = []
    for path in sorted(CATALOG_DIR.glob(f"*/{SKILL_FILE}")):
        skill = parse_skill(path.read_text(encoding="utf-8"), budget=budget)
        if skill.name != path.parent.name:
            raise SkillError(f"{path}: name {skill.name!r} must match its folder")
        skills.append(skill)
    return sorted(skills, key=lambda s: (s.category, s.title.casefold()))


# ── Yours: team and personal skills ─────────────────────────────────────────

def _from_row(row: SkillRow) -> Skill:
    return Skill(name=row.name, description=row.description, instructions=row.instructions, title=row.title,
                 category=row.category, specialist=row.specialist, tools=json.loads(row.tools_json or "[]"),
                 inputs=json.loads(row.inputs_json or "[]"), id=row.id, team=row.owner_id is None,
                 origin=row.origin, enabled=row.enabled)


def _fill(row: SkillRow, skill: Skill) -> None:
    row.name, row.title, row.description = skill.name, skill.title, skill.description
    row.category, row.specialist, row.instructions = skill.category, skill.specialist, skill.instructions
    row.tools_json, row.inputs_json = json.dumps(skill.tools), json.dumps(skill.inputs)
    row.enabled, row.updated_at = skill.enabled, utcnow()


def _visible(user: User):
    return or_(SkillRow.owner_id.is_(None), SkillRow.owner_id == user.id)


def yours(session: Session, user: User) -> list[Skill]:
    """The team's skills, then the user's own, each by category then title."""
    rows = session.scalars(select(SkillRow).where(_visible(user))).all()
    return sorted((_from_row(r) for r in rows), key=lambda s: (not s.team, s.category, s.title.casefold()))


def usable(session: Session, user: User) -> list[Skill]:
    """What /name can run, one skill per name: the user's own first (it may adapt a team skill), else the team's,
    else the built-in one — which works without adding it, unless Yours holds a copy (even a switched-off one)."""
    own = yours(session, user)
    in_yours = {s.name for s in own}
    picked: dict[str, Skill] = {}
    for skill in [*sorted((s for s in own if s.enabled), key=lambda s: s.team),
                  *(s for s in catalog() if s.name not in in_yours)]:
        picked.setdefault(skill.name, skill)
    return list(picked.values())


def find(session: Session, user: User, name: str) -> Skill | None:
    """The skill /name runs (see usable)."""
    return next((s for s in usable(session, user) if s.name == name.strip().lower()), None)


def _row_for_change(session: Session, user: User, skill_id: int) -> SkillRow:
    row = session.get(SkillRow, skill_id)
    if row is None or (row.owner_id is not None and row.owner_id != user.id):
        raise KeyError(f"no skill {skill_id}")
    if row.owner_id is None and user.role != "admin":
        raise PermissionError("only an admin can change a team skill — add it to yours to adapt it")
    return row


def _taken(session: Session, user: User, name: str, *, team: bool, skip: int | None = None) -> bool:
    owner = SkillRow.owner_id.is_(None) if team else SkillRow.owner_id == user.id
    row = session.scalars(select(SkillRow).where(owner, SkillRow.name == name)).first()
    return row is not None and row.id != skip


def create(session: Session, user: User, skill: Skill, *, team: bool, budget: int, origin: str = "") -> Skill:
    """Save a new skill in Yours: for the team (admins only) or for this user."""
    if team and user.role != "admin":
        raise PermissionError("only an admin can add a skill for the team")
    skill = check(skill, budget=budget)
    if _taken(session, user, skill.name, team=team):
        raise SkillError(f"there is already a {'team' if team else 'personal'} skill called /{skill.name}")
    row = SkillRow(owner_id=None if team else user.id, created_by=user.id, origin=origin)
    _fill(row, skill)
    session.add(row)
    session.flush()
    return _from_row(row)


def update(session: Session, user: User, skill_id: int, changes: dict[str, Any], *, budget: int) -> Skill:
    row = _row_for_change(session, user, skill_id)
    current = _from_row(row)
    editable = {"name", "description", "instructions", "title", "category", "specialist", "tools", "inputs",
                "enabled"}
    skill = check(Skill(**{**asdict(current), **{k: v for k, v in changes.items() if k in editable}}), budget=budget)
    if _taken(session, user, skill.name, team=current.team, skip=row.id):
        raise SkillError(f"there is already a skill called /{skill.name}")
    _fill(row, skill)
    session.flush()
    return _from_row(row)


def delete(session: Session, user: User, skill_id: int) -> None:
    session.delete(_row_for_change(session, user, skill_id))
    session.flush()


def add_from_catalog(session: Session, user: User, name: str, *, team: bool, budget: int) -> Skill:
    """Discover's "+": copy a built-in skill into Yours."""
    skill = next((s for s in catalog() if s.name == name), None)
    if skill is None:
        raise KeyError(f"no skill {name!r} in the catalog")
    return create(session, user, skill, team=team, budget=budget, origin=name)


# ── in chat (S2): /name picks the skill, the skill shapes who answers and how ──────────

# a message that starts with /name; a /path that is no skill of the user's stays a normal message
_SLASH = re.compile(r"\A\s*/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|\Z)\s*(.*)\Z", re.S | re.I)


def from_message(session: Session, user: User, query: str) -> tuple[Skill | None, str]:
    """(the skill the message starts with, the message without its /name) — or (None, the message)."""
    match = _SLASH.match(query)
    skill = find(session, user, match.group(1)) if match else None
    if skill is None:
        return None, query
    rest = match.group(2).strip()
    return skill, rest or f"(The user started the “{skill.title}” skill without details yet.)"


def for_job(config: Config, user_id: int | None, query: str) -> tuple[Skill | None, str]:
    """from_message for a job's user (the worker's entry point)."""
    if user_id is None or not _SLASH.match(query):
        return None, query
    with session_scope(config) as session:
        user = session.get(User, user_id)
        return from_message(session, user, query) if user else (None, query)


def prompt_block(skill: Skill) -> str:
    """What the model reads while the skill runs."""
    needs = ("\nIt needs: " + "; ".join(skill.inputs) + ". If the message and the chat do not give one of them, "
             "call ask_clarification first and ask only for what is missing.") if skill.inputs else ""
    return (f"## Skill: {skill.title} (/{skill.name})\nThe user picked this skill — follow its steps and its output "
            f"format for this answer.{needs}\n\n{skill.instructions}")


def apply(skill: Skill, specialist: Specialist | None) -> Specialist:
    """The specialist that answers with the skill: its instructions plus the skill's, and the skill's tools (only
    those the specialist may use — the IT-only tools stay IT-only). No specialist → the plain assistant with it."""
    base = specialist or Specialist(id=GENERAL, name="Assistant", covers="", instructions="", tools=tool_names())
    tools = [t for t in skill.tools if t in base.tools] or base.tools
    return replace(base, instructions=f"{base.instructions}\n\n{prompt_block(skill)}".strip(), tools=tools)
