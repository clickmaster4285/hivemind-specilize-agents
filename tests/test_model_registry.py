"""The model list mirrors what Ollama has installed — local only, no cloud role or overflow."""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.core.config import Config
from src.core.db import Base
from src.core.models_db import ModelRecord
from src.llm.model_registry import resolve_model_plan, sync_models_from_ollama

CFG = dataclasses.replace(Config.from_env(), model_fast="qwen3.5:9b", model_strong="qwen3.5:9b", model_vision="")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    yield s
    s.close()


def _ollama(monkeypatch, names: list[str] | None):
    def list_tags(self):
        if names is None:
            raise ConnectionError("ollama down")
        return [{"name": n} for n in names]

    monkeypatch.setattr("src.llm.llm_client.LLMClient.list_tags", list_tags)
    monkeypatch.setattr("src.llm.llm_client.LLMClient.show_model", lambda self, name: {"capabilities": ["completion"]})


def _names(session) -> list[str]:
    return sorted(session.scalars(select(ModelRecord.name)).all())


def test_models_no_longer_installed_leave_the_list(monkeypatch, session):
    for name, role, backend in [("qwen3.5:9b", "strong", "local_ollama"), ("llama3.1:latest", "strong", "local_ollama"),
                                ("qwen3.5:cloud", "cloud", "remote_openai_compatible")]:
        session.add(ModelRecord(name=name, role=role, backend=backend, vram_class="medium", enabled=True, available=True))
    session.flush()
    _ollama(monkeypatch, ["qwen3.5:9b"])
    sync_models_from_ollama(session, CFG)
    assert _names(session) == ["qwen3.5:9b"]          # the old local model and the cloud entry are gone


def test_an_ollama_outage_never_empties_the_list(monkeypatch, session):
    session.add(ModelRecord(name="qwen3.5:9b", role="strong", backend="local_ollama", vram_class="medium", enabled=True))
    session.flush()
    _ollama(monkeypatch, None)
    sync_models_from_ollama(session, CFG)
    assert _names(session) == ["qwen3.5:9b"]


def test_plan_is_local_only(monkeypatch, session):
    _ollama(monkeypatch, ["qwen3.5:9b"])
    sync_models_from_ollama(session, CFG)
    plan = resolve_model_plan(session, CFG)
    assert plan["planner"] == plan["worker"] == "qwen3.5:9b"
    assert not any("overflow" in key for key in plan)
