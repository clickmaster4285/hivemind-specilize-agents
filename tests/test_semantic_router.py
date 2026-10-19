"""Semantic router: decisions by meaning from data/router/intents.yaml, measured on eval.yaml."""

from __future__ import annotations

import dataclasses

import pytest
import requests

from src import intent_router
from src.config import Config
from src.intent_router import classify_query

# these test the example-based backup router (the assistant normally picks tools itself)
CFG = dataclasses.replace(Config.from_env(), assistant_tools=False)


def _ollama_up() -> bool:
    try:
        return requests.get(f"{CFG.llm_base_url}/api/version", timeout=2).ok
    except requests.RequestException:
        return False


needs_ollama = pytest.mark.skipif(not _ollama_up(), reason="needs the embedding model (Ollama)")


@needs_ollama
def test_no_wrong_decisions_on_the_eval_set():
    from src.semantic_router import evaluate

    result = evaluate(CFG)
    wrong = [(r["text"], r["intent"], r["got"]) for r in result["wrong"]]
    assert wrong == [], f"wrong routes (fix with examples in data/router/intents.yaml): {wrong}"
    assert result["right"] >= 0.9 * result["total"]          # the rest are close calls for the LLM


def test_offline_falls_back_to_the_old_rules(monkeypatch):
    def down(*a, **k):
        raise RuntimeError("Ollama is not reachable")

    monkeypatch.setattr("src.semantic_router.SemanticRouter.classify", down)
    assert classify_query("draw a cat sitting on a sofa", config=CFG, allow_llm=False).intent == "image_gen"
    assert classify_query("hello", config=CFG, allow_llm=False).intent == "chat"


def test_flags_mean_an_image_prompt():
    assert classify_query("neon city street at night --ar 16:9 --v 6.0", config=CFG, allow_llm=False).intent == "image_gen"


@needs_ollama
def test_a_mode_the_user_picked_wins_except_for_tools():
    assert classify_query("explain DNS zones", requested_mode="agentic", config=CFG, allow_llm=False).intent == "agentic"
    assert classify_query("draw a flowchart of CI/CD", requested_mode="agentic", config=CFG,
                          allow_llm=False).intent == "diagram"


@needs_ollama
def test_greeting_keeps_the_knowledge_base_out():
    from src.brain.answer import should_use_brain

    route = classify_query("good morning, how are you doing", config=CFG, allow_llm=False)
    assert route.intent == "chat" and "greeting" in route.reason
    assert not should_use_brain("good morning, how are you doing", intent=route.intent, reason=route.reason,
                                has_images=False)


def test_new_intents_need_no_code(tmp_path, monkeypatch):
    """Adding an intent = adding examples: the router and the LLM menu pick it up from the file."""
    from src.semantic_router import SemanticRouter

    f = tmp_path / "intents.yaml"
    f.write_text("intents:\n  weather:\n    note: weather questions\n    examples:\n      - will it rain today\n"
                 "  chat:\n    note: questions\n    examples:\n      - what is DNS\n", encoding="utf-8")
    router = SemanticRouter(CFG, intents_file=f)
    assert router.intents == ["chat", "weather"] and router.notes["weather"] == "weather questions"


def test_legacy_rules_are_only_a_fallback():
    assert "Frozen" in (intent_router._legacy_route.__doc__ or "")
