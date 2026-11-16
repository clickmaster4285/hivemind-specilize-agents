"""O52: the diagram rules ride in an answer's prompt only when the router expects a drawing (on by default)."""

from __future__ import annotations

import dataclasses

from src.core.config import Config
from src.llm import assistant
from src.llm.diagram_rules import DIAGRAM_RULES, NETDIAGRAM_RULES, NO_DIAGRAM_RULE
from src.llm.router import SEARCH, Route, parse_route
from src.tools import web_search as ws

CFG = Config.from_env()


def test_the_router_flag_defaults_on():
    assert parse_route('{"route": "model"}').diagram is True              # missing → keep the rules
    assert parse_route('{"route": "model", "diagram": false}').diagram is False
    assert Route().diagram is True                                        # a failed router keeps them too


def test_the_answer_prompt_carries_the_rules_only_when_on():
    on = assistant.messages_for("how does DNS work", CFG, None, CFG.llm_model, diagrams=True)[0]["content"]
    off = assistant.messages_for("what is 15% of 2400", CFG, None, CFG.llm_model, diagrams=False)[0]["content"]
    assert DIAGRAM_RULES in on and NETDIAGRAM_RULES in on and NO_DIAGRAM_RULE not in on
    assert DIAGRAM_RULES not in off and NETDIAGRAM_RULES not in off and NO_DIAGRAM_RULE in off
    assert len(off) < len(on) - len(DIAGRAM_RULES)


def test_a_routed_web_search_passes_the_flag_and_the_tool_choice_keeps_the_default():
    web_on = dataclasses.replace(CFG, web_search_url="http://searxng.test")
    decision = assistant.pre_route(Route(route=SEARCH, query="pm2 next.js", diagram=False), "q", web_on, None)
    assert decision is not None and decision.args.get("diagram") == "no"
    assert assistant.tool_flag(decision.args, "diagram", default=True) is False
    assert assistant.tool_flag({"query": "x"}, "diagram", default=True) is True


def test_the_web_answer_prompt_follows_the_flag(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(ws, "search", lambda *a, **k: [object()])
    monkeypatch.setattr(ws, "gather", lambda *a, **k: [])
    monkeypatch.setattr(ws, "by_page", lambda passages: [ws.Passage(url="https://a.example/x", title="A",
                                                                         text="facts", heading="", published="")])
    for flag in (True, False):
        ws.answer_from_web("q", CFG, chat=lambda **kw: seen.append(kw["system"]) or "answer [1]", model="m",
                           now="Monday", diagram=flag)
    assert DIAGRAM_RULES in seen[0] and NO_DIAGRAM_RULE in seen[1] and DIAGRAM_RULES not in seen[1]
