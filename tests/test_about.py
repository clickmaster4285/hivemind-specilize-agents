"""Answers about MulteAgent itself come from live facts, never from guesses."""

from __future__ import annotations

import dataclasses

from src import about
from src.config import Config

CFG = Config.from_env()


def test_identity_comes_from_ollama_not_a_table(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"details": {"parameter_size": "9.0B", "quantization_level": "Q4_K_M"},
                    "model_info": {"general.base_model.0.name": "Qwen3.5 9B Base",
                                   "general.base_model.0.organization": "Qwen", "general.license": "apache-2.0"}}

    about._cache.clear()
    monkeypatch.setattr(about.requests, "post", lambda *a, **k: Resp())
    text = about.model_identity(CFG, "any-model:9b")
    assert text == "any-model:9b (Qwen3.5 9B Base by Qwen, 9.0B parameters, Q4_K_M, licence apache-2.0)"
    assert "any-model:9b" in about.identity_line(CFG, "any-model:9b")


def test_identity_without_ollama_is_just_the_name(monkeypatch):
    about._cache.clear()

    def down(*a, **k):
        raise ConnectionError("refused")

    monkeypatch.setattr(about.requests, "post", down)
    assert about.model_identity(CFG, "some:1b") == "some:1b"


def test_about_system_lists_live_sources(monkeypatch):
    monkeypatch.setattr(about, "brain_facts", lambda c: "- Windows Server documentation — DNS, 42 pages")
    text = about.about_system(CFG, "Friday 17 October 2026, 11:00", "qwen3.5:9b")
    assert "42 pages" in text and "17 October 2026" in text and "Mermaid" in text
    assert "{" not in text.replace("{{", "")                    # every placeholder filled


def test_brain_down_or_off_is_said_plainly(monkeypatch):
    assert "switched off" in about.brain_facts(dataclasses.replace(CFG, brain_enabled=False))

    def broken(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("src.brain.db.connect", broken)
    assert "cannot be reached" in about.brain_facts(CFG)


def test_about_answer_skips_the_brain(monkeypatch):
    from src import chat_runner

    seen = {}

    class FakeClient:
        def __init__(self, config):
            pass

        def chat(self, *, system, user, model=None, images=None, **kw):
            seen["system"] = system
            return "I am MulteAgent."

    monkeypatch.setattr(chat_runner, "LLMClient", FakeClient)
    monkeypatch.setattr(about, "brain_facts", lambda c: "- Python documentation — HOWTO guides, 27 pages")
    monkeypatch.setattr(chat_runner, "answer_question", lambda *a, **k: (_ for _ in ()).throw(AssertionError("brain used")))
    events = []
    result = chat_runner.run_chat("what is in your brain", config=CFG, use_brain=True, about=True,
                                  on_progress=events.append)
    assert result.output == "I am MulteAgent." and "27 pages" in seen["system"]
    done = [e for e in events if e["type"] == "agent_done"][0]
    assert done["role"] == "About MulteAgent"


def test_time_is_told_in_the_users_time_zone():
    from datetime import datetime, timezone

    from src.clock import current_time_text, set_time_zone

    noon_utc = datetime(2026, 10, 17, 6, 11, tzinfo=timezone.utc)
    assert "11:11 AM" in current_time_text(noon_utc, "Asia/Karachi")
    assert "Asia/Karachi" in current_time_text(noon_utc, "Asia/Karachi")
    assert "02:11 AM" in current_time_text(noon_utc, "America/New_York")
    set_time_zone("Europe/London")
    assert "07:11 AM" in current_time_text(noon_utc) and "Europe/London" in current_time_text(noon_utc)
    set_time_zone("not/a-zone")                                # bad input from a browser: server time
    assert "not/a-zone" not in current_time_text(noon_utc)
    set_time_zone(None)
