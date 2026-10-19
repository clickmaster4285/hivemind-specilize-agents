"""The assistant picks tools itself (native tool calling) — logic tested with a fake model.

The real model's choices are measured by `python -m src.assistant --eval` (27/27 on 2026-10-17);
run it here with RUN_LLM_EVAL=1 (slow, needs Ollama).
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from src import assistant, live
from src.config import Config
from src.intent_router import classify_query
from src.llm_client import LLMStopped

CFG = dataclasses.replace(Config.from_env(), assistant_tools=True)


def test_normal_messages_go_to_the_assistant_buttons_do_not():
    assert classify_query("what is the current weather", config=CFG).intent == "auto"
    assert classify_query("hello", config=CFG).intent == "auto"
    assert classify_query("a red car --ar 16:9", config=CFG).intent == "image_gen"      # flags: still direct
    assert classify_query("anything", config=CFG, force_intent="diagram").intent == "diagram"


def test_tool_list_follows_availability():
    names = lambda c: [t["function"]["name"] for t in assistant.tool_schemas(c)]
    assert "network_scan" in names(CFG)
    assert "network_scan" not in names(dataclasses.replace(CFG, network_scan_enabled=False))
    assert "web_search" not in names(dataclasses.replace(CFG, web_search_url=""))


def _fake_model(monkeypatch, text="", calls=None, stop=False):
    def chat_tools(self, *, messages, tools, model=None, temperature=0.2, on_delta=None, should_continue=None):
        if stop:
            raise LLMStopped("Half an ans")
        if text and on_delta:
            on_delta(text[:5])
            on_delta(text)
        return text, calls or []

    monkeypatch.setattr("src.llm_client.LLMClient.chat_tools", chat_tools)


def test_direct_answer_streams_and_finishes(monkeypatch):
    _fake_model(monkeypatch, text="Hello! How can I help?")
    events = []
    d = assistant.decide("hello", job_id="a1", config=CFG, on_progress=events.append)
    assert d.answered and d.errors == []
    types = [e["type"] for e in events]
    assert types[:3] == ["status", "session_start", "agent_start"] and types[-2:] == ["agent_done", "session_done"]
    assert events[-2]["output"] == "Hello! How can I help?"
    assert live.snapshot("a1") == {}


def test_tool_call_returns_tool_and_arguments(monkeypatch):
    _fake_model(monkeypatch, calls=[{"name": "get_weather", "arguments": {"place": "Lahore"}}])
    d = assistant.decide("kal Lahore mein barish hogi?", job_id="a2", config=CFG)
    assert not d.answered and (d.intent, d.tool, d.args) == ("weather", "get_weather", {"place": "Lahore"})


def test_unknown_or_switched_off_tool_falls_back_to_the_knowledge_base(monkeypatch):
    _fake_model(monkeypatch, calls=[{"name": "network_scan", "arguments": {}}])
    d = assistant.decide("scan", job_id="a3", config=dataclasses.replace(CFG, network_scan_enabled=False))
    assert not d.answered and d.intent == "kb"


def test_stop_keeps_the_partial_answer(monkeypatch):
    _fake_model(monkeypatch, stop=True)
    events = []
    d = assistant.decide("write an essay", job_id="a4", config=CFG, on_progress=events.append)
    assert d.answered and "Half an ans" in events[-2]["output"] and events[-2]["role"] == "Stopped"


@pytest.mark.skipif(os.getenv("RUN_LLM_EVAL") != "1", reason="slow real-model eval: set RUN_LLM_EVAL=1")
def test_real_model_picks_the_right_tools():
    result = assistant.evaluate(CFG)
    misses = [(r["text"], r.get("tool"), r["got"]) for r in result["rows"] if not r["ok"]]
    assert len(misses) <= 1, misses
