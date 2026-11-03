"""L14 S1: skills — the SKILL.md format, the built-in catalog, team and personal skills, and /api/skills.
In-memory database, no model, no internet."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.auth import get_current_user, get_db
from src.core.db import Base
from src.core.models_db import User
from src.jobs import worker
from src.llm import skills
from src.llm.assistant import speaker
from src.llm.skills import Skill, SkillError, catalog, parse_skill
from src.llm.specialists import Specialist, tool_names
from web.routers import skills as skills_router

BUDGET = 500
GOOD = """---
name: weekly-report
description: Write the weekly status report. Use on Fridays.
allowed-tools: web_search Bash Read
metadata:
  title: Weekly report
  category: Operations
  specialist: it
  inputs: what got done; what is blocked
---

# Weekly report
1. List what got done.
"""


def _skill(name: str = "weekly-report", **extra) -> Skill:
    return Skill(**{"name": name, "description": "Write the weekly report.",
                    "instructions": "1. List what got done.", **extra})


# ── the format ─────────────────────────────────────────────────────────

def test_a_skill_file_is_read_and_written_back():
    skill = parse_skill(GOOD, budget=BUDGET)
    assert (skill.name, skill.title, skill.category, skill.specialist) == ("weekly-report", "Weekly report",
                                                                            "Operations", "it")
    assert skill.tools == ["web_search"]               # another assistant's tools (Bash, Read) are dropped
    assert skill.inputs == ["what got done", "what is blocked"]
    assert skill.instructions.startswith("# Weekly report")
    assert parse_skill(skill.to_markdown(), budget=BUDGET) == skill


@pytest.mark.parametrize(("text", "problem"), [
    ("# no front matter", "front matter"),
    ("---\nname: Weekly Report\ndescription: x\n---\nbody", "lowercase"),
    ("---\nname: weekly\n---\nbody", "description"),
    ("---\nname: weekly\ndescription: x\n---\n", "instructions"),
    ("---\nname: [unclosed\n---\nbody", "YAML"),
])
def test_a_broken_skill_file_says_what_to_fix(text, problem):
    with pytest.raises(SkillError, match=problem):
        parse_skill(text, budget=BUDGET)


def test_instructions_must_fit_the_models_context():
    with pytest.raises(SkillError, match="shorten"):
        skills.check(_skill(instructions="word " * 5000), budget=BUDGET)


def test_an_unknown_specialist_falls_back_to_the_usual_pick():
    assert skills.check(_skill(specialist="astrology"), budget=BUDGET).specialist == ""


def test_the_catalog_loads_and_every_skill_is_usable():
    found = catalog()
    assert len(found) >= 10 and len({s.name for s in found}) == len(found)
    for skill in found:
        assert skill.category and skill.inputs, skill.name
        declared = skills.CATALOG_DIR / skill.name / skills.SKILL_FILE
        allowed = re.search(r"^allowed-tools: (.*)$", declared.read_text(encoding="utf-8"), re.M)
        assert skill.tools == (allowed.group(1).split() if allowed else []), f"{skill.name}: unknown tool"


# ── team and personal skills ───────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add_all([User(id=1, username="admin", password_hash="x", role="admin"),
                     User(id=2, username="ana", password_hash="x", role="member"),
                     User(id=3, username="bilal", password_hash="x", role="member")])
    session.flush()
    yield session
    session.close()


def test_team_skills_are_for_everyone_personal_ones_for_their_owner(db):
    admin, ana, bilal = db.get(User, 1), db.get(User, 2), db.get(User, 3)
    skills.create(db, admin, _skill("team-report"), team=True, budget=BUDGET)
    skills.create(db, ana, _skill("my-report"), team=False, budget=BUDGET)
    assert [(s.name, s.team) for s in skills.yours(db, ana)] == [("team-report", True), ("my-report", False)]
    assert [s.name for s in skills.yours(db, bilal)] == ["team-report"]
    with pytest.raises(PermissionError):
        skills.create(db, ana, _skill("sneaky"), team=True, budget=BUDGET)


def test_only_admins_change_team_skills_and_nobody_sees_anothers(db):
    admin, ana, bilal = db.get(User, 1), db.get(User, 2), db.get(User, 3)
    team = skills.create(db, admin, _skill("team-report"), team=True, budget=BUDGET)
    mine = skills.create(db, ana, _skill("my-report"), team=False, budget=BUDGET)
    with pytest.raises(PermissionError):
        skills.update(db, ana, team.id, {"description": "changed"}, budget=BUDGET)
    with pytest.raises(KeyError):
        skills.delete(db, bilal, mine.id)
    assert skills.update(db, admin, team.id, {"description": "changed"}, budget=BUDGET).description == "changed"


def test_slash_name_finds_my_own_version_first(db):
    admin, ana = db.get(User, 1), db.get(User, 2)
    skills.create(db, admin, _skill("report", title="Team version"), team=True, budget=BUDGET)
    assert skills.find(db, ana, "report").title == "Team version"
    own = skills.create(db, ana, _skill("report", title="My version"), team=False, budget=BUDGET)
    assert skills.find(db, ana, "report").title == "My version"
    skills.update(db, ana, own.id, {"enabled": False}, budget=BUDGET)
    assert skills.find(db, ana, "report").title == "Team version"      # a switched-off skill is skipped
    with pytest.raises(SkillError, match="already"):
        skills.create(db, ana, _skill("report"), team=False, budget=BUDGET)


def test_built_in_skills_run_without_adding_them(db):
    admin, ana = db.get(User, 1), db.get(User, 2)
    first = catalog()[0]
    assert {s.name for s in skills.usable(db, ana)} == {s.name for s in catalog()}
    assert skills.find(db, ana, first.name).id is None                    # the built-in one, straight from the file
    team = skills.add_from_catalog(db, admin, first.name, team=True, budget=2000)
    assert skills.find(db, ana, first.name).id == team.id                  # a copy in Yours replaces it
    skills.update(db, admin, team.id, {"enabled": False}, budget=2000)
    assert skills.find(db, ana, first.name) is None                        # switched off means off, not the built-in


def test_adding_from_the_catalog_copies_it(db):
    ana = db.get(User, 2)
    first = catalog()[0]
    added = skills.add_from_catalog(db, ana, first.name, team=False, budget=2000)
    assert (added.origin, added.instructions, added.team) == (first.name, first.instructions, False)
    with pytest.raises(KeyError):
        skills.add_from_catalog(db, ana, "no-such-skill", team=False, budget=2000)


# ── the API ────────────────────────────────────────────────────────────

@pytest.fixture
def api(db):
    app = FastAPI()
    app.include_router(skills_router.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 2)
    db.commit = db.flush                                  # keep the test's data in its one transaction
    return TestClient(app)


def test_the_api_lists_adds_uploads_and_downloads(api):
    name = catalog()[0].name
    assert api.post(f"/api/skills/catalog/{name}/add", json={}).status_code == 200
    found = api.get("/api/skills/catalog").json()
    assert next(s for s in found["skills"] if s["name"] == name)["added"] is True
    assert sum(c["count"] for c in found["categories"]) == len(found["skills"])
    uploaded = api.post("/api/skills/upload", json={"text": GOOD}).json()
    assert uploaded["name"] == "weekly-report" and uploaded["team"] is False
    download = api.get(f"/api/skills/{uploaded['id']}/download")
    assert download.text.startswith("---\nname: weekly-report")
    assert "weekly-report-SKILL.md" in download.headers["content-disposition"]
    listed = api.get("/api/skills").json()
    assert {s["name"] for s in listed["skills"]} == {name, "weekly-report"}
    assert {s["name"] for s in listed["usable"]} == {s.name for s in catalog()} | {"weekly-report"}


def test_the_api_refuses_what_a_member_may_not_do(api):
    bad = api.post("/api/skills", json={"name": "Bad Name", "description": "x", "instructions": "y"})
    assert bad.status_code == 400 and "lowercase" in bad.json()["detail"]
    team = api.post("/api/skills", json={"name": "team-one", "description": "x", "instructions": "y", "team": True})
    assert team.status_code == 403
    assert api.put("/api/skills/999", json={"enabled": False}).status_code == 404


# ── in chat (S2) ───────────────────────────────────────────────────────

def test_a_message_starting_with_slash_name_runs_that_skill(db):
    ana = db.get(User, 2)
    skills.create(db, ana, _skill("report", inputs=["the week"]), team=False, budget=BUDGET)
    skill, rest = skills.from_message(db, ana, "  /report   the mail server move\nand backups")
    assert skill.name == "report" and rest == "the mail server move\nand backups"
    skill, rest = skills.from_message(db, ana, "/REPORT")
    assert skill.name == "report" and "without details" in rest           # the skill asks for its inputs
    for text in ("/etc/hosts is broken", "/reports please", "please /report", "report this"):
        assert skills.from_message(db, ana, text) == (None, text)          # not a skill of hers: a normal message


def test_the_skill_shapes_who_answers(monkeypatch):
    it = Specialist(id="it", name="IT", covers="servers", instructions="Be exact.",
                    tools=["diagnose_server", "server_audit", "web_search"])
    skill = _skill(tools=["server_audit", "network_scan"], inputs=["which server"])
    applied = skills.apply(skill, it)
    assert applied.tools == ["server_audit"]               # only tools the specialist may use (network_scan is not)
    block = applied.prompt_block()
    assert block.startswith("You answer as MulteAgent's IT specialist") and "Be exact." in block
    assert "## Skill: Weekly report (/weekly-report)" in block and "It needs: which server" in block
    assert "ask_clarification" in block
    plain = skills.apply(_skill(), None)                   # no specialist: the plain assistant, every tool
    assert plain.prompt_block().startswith("## Skill:") and speaker(plain)["name"] == "Assistant"
    assert set(plain.tools) == set(tool_names())


def test_the_worker_lets_the_skill_pick_the_specialist(monkeypatch):
    asked = []
    monkeypatch.setattr(worker, "resolve_specialist", lambda choice, query, **_: (
        asked.append(choice) or (Specialist(id="it", name="IT", covers="", instructions="", tools=[]), "chosen")))
    events: list[dict] = []
    common = {"config": None, "conversation": [], "on_progress": events.append}
    worker._answerer({"specialist": "development"}, "q", _skill(specialist="it"), **common)
    worker._answerer({"specialist": "development"}, "q", _skill(), **common)
    worker._answerer({"specialist": "development"}, "q", None, **common)
    assert asked == ["it", "development", "development"]   # the skill's specialist wins over the chat bar's
    assert [e["how"] for e in events if e["type"] == "specialist_chosen"] == ["skill", "chosen", "chosen"]
    assert [e["title"] for e in events if e["type"] == "skill_chosen"] == ["Weekly report", "Weekly report"]
