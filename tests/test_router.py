"""The routing call (src/llm/router.py, ISSUES_LOG O51): one short model call decides search / clarify / the
model's own tool choice, and whether the user is learning a topic. Replaces the keyword lists."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from src.core.config import Config

from src.llm import assistant
from src.llm.assistant import pre_route
from src.llm.router import CLARIFY, MODEL, SEARCH, Route, parse_route, route_message

CFG = SimpleNamespace(web_search_url="http://127.0.0.1:8888", model_fast="m", llm_model="m")


def test_a_search_verdict_carries_its_words_and_flags():
    raw = 'Sure: ```json\n{"route": "search", "query": "GTA 6 download size", "news": false, "learning": false}\n```'
    assert parse_route(raw) == Route(SEARCH, "GTA 6 download size", False, False)
    assert parse_route('{"route": "search", "query": "x", "news": "true"}').news is True


def test_anything_unusable_leaves_it_to_the_model():
    for raw in ("", "search", '{"route": "web"}', "[1, 2]", '{"route": "search"'):
        assert parse_route(raw) == Route(), raw
    assert parse_route('{"route": "model", "query": "ignored", "learning": true}') == Route(MODEL, "", False, True)


def test_the_router_sees_the_previous_message_for_a_follow_up():
    seen: dict = {}

    def chat(**kwargs):
        seen.update(kwargs)
        return '{"route": "search", "query": "Jensen Huang age", "news": false, "learning": false}'

    conversation = [{"role": "user", "content": "who is jensen huang"}, {"role": "assistant", "content": "The CEO."}]
    route = route_message("and his age?", CFG, conversation, chat=chat)
    assert route.query == "Jensen Huang age"
    assert seen["user"].startswith("Previous message: who is jensen huang") and seen["user"].endswith("and his age?")
    assert seen["temperature"] == 0.0 and seen["max_tokens"]


def test_a_failed_router_call_leaves_it_to_the_model():
    def broken(**_kwargs):
        raise RuntimeError("ollama down")

    assert route_message("anything", CFG, chat=broken) == Route()


def test_search_goes_to_web_search_with_the_routers_words():
    routed = pre_route(Route(SEARCH, "GTA 6 download size", news=True, learning=False),
                       "how much time to download gta 6", CFG, None)
    assert routed.tool == "web_search"
    assert routed.args == {"query": "GTA 6 download size", "news": "yes"}


def test_search_without_web_search_is_left_to_the_model():
    assert pre_route(Route(SEARCH, "x"), "q", SimpleNamespace(web_search_url=""), None) is None
    specialist = SimpleNamespace(tools=["write_content"])
    assert pre_route(Route(SEARCH, "x"), "q", CFG, specialist) is None


def test_clarify_asks_questions_unless_a_follow_up(monkeypatch):
    questions = [{"question": "Budget?", "options": ["low", "high"]}]
    monkeypatch.setattr(assistant, "_ask_clarifying", lambda *a: questions)
    routed = pre_route(Route(CLARIFY), "recommend a laptop", CFG, None)
    assert routed.intent == "clarify" and routed.args == {"questions": questions}
    assert pre_route(Route(CLARIFY), "recommend a laptop", CFG, None, clarify_ok=False) is None


def test_model_goes_to_the_tool_choice():
    assert pre_route(Route(MODEL, learning=True), "what is 15% of 2400", CFG, None) is None


def test_clarifying_questions_see_the_previous_message(monkeypatch):
    # "name of lead woman actress" after a Spider-Man answer was asked "which movie? The Matrix, Inception…" (O55)
    seen: dict = {}

    def chat(self, **kwargs):
        seen.update(kwargs)
        return "[]"

    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat", chat)
    pre_route(Route(CLARIFY), "name of lead woman actress", CFG, None,
              previous="what is the plot of spider man brand new day")
    assert seen["user"].startswith("Previous message: what is the plot of spider man brand new day")


def test_no_clarifying_twice_in_a_row(monkeypatch):
    # the user typed their answer to a clarifying question and was asked another one (O55)
    monkeypatch.setattr(assistant, "route_message", lambda *a, **k: Route(CLARIFY))
    monkeypatch.setattr(assistant, "_ask_clarifying", lambda *a: [{"question": "Platform?", "options": ["PC", "PS5"]}])
    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat_tools", lambda self, **k: ("The lead is Zendaya.", []))
    conversation = [{"role": "user", "content": "name of lead woman actress"},
                    {"role": "assistant", "content": assistant.CLARIFY_INTRO}]
    cfg = dataclasses.replace(Config.from_env(), assistant_tools=True, suggestions_enabled=False,
                              quiz_enabled=False, web_images_enabled=False)
    decision = assistant.decide("Spider-Man: Brand New Day", job_id="c2", config=cfg, conversation=conversation)
    assert decision.intent != "clarify"
    assert assistant._just_clarified(conversation) and not assistant._just_clarified(conversation[:1])


def test_a_calculation_is_flagged_for_the_web_answer():
    route = parse_route('{"route": "search", "query": "GTA 6 size", "news": false, "learning": false, "calculation": true}')
    assert route.calculation is True
    assert pre_route(route, "how long to download gta 6", CFG, None).args["calculation"] == "yes"
