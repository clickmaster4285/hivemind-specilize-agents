"""Skills (src/llm/skills.py, BACKLOG L14): Discover = the built-in catalog; Yours = team skills (everyone sees them,
admins change them) and the user's personal skills (only they see and change them)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from src.core.auth import get_db, require_module
from src.core.config import Config
from src.core.models_db import User
from src.llm import skills
from web.schemas import SkillAddPayload, SkillPayload, SkillUpdatePayload, SkillUploadPayload

router = APIRouter(prefix="/api/skills", tags=["skills"])
SKILLS = require_module("skills")      # the routes of the skills module (src/core/modules.py)


def _budget() -> int:
    return skills.instruction_budget(Config.from_env())


def _saved(db: Session, action) -> dict[str, Any]:
    """Run a change, commit it, and turn the store's errors into HTTP answers."""
    try:
        skill = action()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except skills.SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return skill.to_dict() if skill else {"status": "deleted"}


@router.get("")
def list_yours(db: Session = Depends(get_db), user: User = Depends(SKILLS)) -> dict[str, Any]:
    """Yours (team + personal), and what the chat's "/" menu offers (usable: also the built-in skills)."""
    return {"skills": [s.to_dict() for s in skills.yours(db, user)],
            "usable": [s.to_dict() for s in skills.usable(db, user)], "can_manage_team": user.role == "admin"}


@router.get("/catalog")
def list_catalog(db: Session = Depends(get_db), user: User = Depends(SKILLS)) -> dict[str, Any]:
    """Discover: every built-in skill (marked when already in Yours) and the categories with their counts."""
    added = {s.origin for s in skills.yours(db, user) if s.origin}
    found = skills.catalog()
    return {"skills": [{**s.to_dict(), "added": s.name in added} for s in found],
            "categories": [{"name": n, "count": c} for n, c in sorted(Counter(s.category for s in found).items())]}


@router.post("")
def create_skill(payload: SkillPayload, db: Session = Depends(get_db),
                 user: User = Depends(SKILLS)) -> dict[str, Any]:
    data = payload.model_dump(exclude={"team"})
    return _saved(db, lambda: skills.create(db, user, skills.Skill(**data), team=payload.team, budget=_budget()))


@router.post("/upload")
def upload_skill(payload: SkillUploadPayload, db: Session = Depends(get_db),
                 user: User = Depends(SKILLS)) -> dict[str, Any]:
    """A SKILL.md written for MulteAgent or another assistant."""
    return _saved(db, lambda: skills.create(db, user, skills.parse_skill(payload.text, budget=_budget()),
                                            team=payload.team, budget=_budget()))


@router.post("/catalog/{name}/add")
def add_skill(name: str, payload: SkillAddPayload, db: Session = Depends(get_db),
              user: User = Depends(SKILLS)) -> dict[str, Any]:
    return _saved(db, lambda: skills.add_from_catalog(db, user, name, team=payload.team, budget=_budget()))


@router.put("/{skill_id}")
def update_skill(skill_id: int, payload: SkillUpdatePayload, db: Session = Depends(get_db),
                 user: User = Depends(SKILLS)) -> dict[str, Any]:
    changes = payload.model_dump(exclude_none=True)
    return _saved(db, lambda: skills.update(db, user, skill_id, changes, budget=_budget()))


@router.delete("/{skill_id}")
def delete_skill(skill_id: int, db: Session = Depends(get_db),
                 user: User = Depends(SKILLS)) -> dict[str, Any]:
    return _saved(db, lambda: skills.delete(db, user, skill_id))


@router.get("/{skill_id}/download")
def download_skill(skill_id: int, db: Session = Depends(get_db), user: User = Depends(SKILLS)) -> Response:
    """The skill as a SKILL.md file, to keep or share."""
    skill = next((s for s in skills.yours(db, user) if s.id == skill_id), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"no skill {skill_id}")
    return Response(skill.to_markdown(), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{skill.name}-SKILL.md"'})
