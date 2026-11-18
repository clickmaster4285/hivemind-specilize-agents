"""GET /api/health: anyone may learn the API is up; only a signed-in user gets the details (model, queue …)."""

from fastapi import Request

from src.core import status
from src.core.models_db import User
from web.routers import system


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/health", "headers": []})


def test_an_anonymous_caller_learns_only_that_the_api_is_up(monkeypatch):
    monkeypatch.setattr(system, "details", lambda config, db: (_ for _ in ()).throw(AssertionError("not asked")))
    monkeypatch.setattr(system, "optional_user", lambda request, db: None)
    assert system.health(_request(), db=None) == {"ok": True}


def test_a_signed_in_user_gets_the_details(monkeypatch):
    monkeypatch.setattr(system, "optional_user", lambda request, db: User(id=1, username="ana", password_hash="x"))
    monkeypatch.setattr(system, "details", lambda config, db: {"model": "qwen3.5:9b", "llm_reachable": True})
    assert system.health(_request(), db=None) == {"ok": True, "model": "qwen3.5:9b", "llm_reachable": True}


def test_no_cookie_is_no_user():
    from src.core.auth import optional_user

    assert optional_user(_request(), db=None) is None


def test_the_start_scripts_line():
    line = status.summary_line({"model": "qwen3.5:9b", "llm_reachable": True, "queue_depth": 0,
                                "image_pipeline": {"state": "stopped"}})
    assert line == "model qwen3.5:9b, Ollama reachable: True, image model: stopped, jobs waiting: 0"
