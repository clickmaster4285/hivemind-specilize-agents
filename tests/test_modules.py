"""Module access (BACKLOG L19 D1c): admins have every module, members what an admin gave; the API enforces it."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core import modules
from src.core.auth import get_current_user, may_use, user_modules
from src.core.models_db import User
from web.routers import skills as skills_router
from web.routers import usage as usage_router
from web.routers.users import _modules_json


def _user(role: str = "member", granted: list[str] | None = None) -> User:
    return User(id=5, username="ana", password_hash="x", role=role,
                modules_json=json.dumps(granted) if granted is not None else modules.DEFAULT_MODULES_JSON)


def test_admins_have_every_module_and_new_members_the_defaults():
    assert user_modules(_user("admin", [])) == list(modules.MODULE_IDS)
    assert user_modules(_user()) == [m.id for m in modules.MODULES if m.default]
    assert not may_use(_user(), "devices")


def test_a_module_that_no_longer_exists_is_dropped_and_an_unknown_one_refused():
    assert user_modules(_user(granted=["skills", "gone"])) == ["skills"]
    with pytest.raises(HTTPException) as exc:
        _modules_json(["skills", "nope"])
    assert exc.value.status_code == 422


@pytest.mark.parametrize("router, path, module", [(skills_router, "/api/skills", "skills"),
                                                  (usage_router, "/api/usage?days=7", "usage")])
def test_the_api_refuses_a_module_the_member_was_not_given(router, path, module):
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_current_user] = lambda: _user(granted=[m for m in modules.MODULE_IDS if m != module])
    response = TestClient(app).get(path)
    assert response.status_code == 403 and "Ask an admin" in response.json()["detail"]
