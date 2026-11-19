"""The assistant picks tools itself (native tool calling) — logic tested with a fake model.

The real model's choices are measured by `python -m src.llm.assistant --eval` (27/27 on 2026-10-17);
run it here with RUN_LLM_EVAL=1 (slow, needs Ollama).
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from src.llm import assistant
from src.jobs import live
from src.core.config import Config
from src.llm.job_start import classify_query
from src.llm.llm_client import LLMStopped

CFG = Config.from_env()


@pytest.fixture(autouse=True)
def offline_router(monkeypatch):
    """decide() asks the routing call first (src/llm/router.py): keep it offline, leaving the choice to the
    fake model's tool call, as the tests here expect. The router itself: tests/test_router.py."""
    monkeypatch.setattr(assistant, "route_message", lambda *a, **k: assistant.Route())


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

    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat_tools", chat_tools)


def test_direct_answer_streams_and_finishes(monkeypatch):
    _fake_model(monkeypatch, text="Hello! How can I help?")
    events = []
    d = assistant.decide("hello", job_id="a1", config=CFG, on_progress=events.append)
    assert d.answered and d.errors == []
    types = [e["type"] for e in events]
    assert types[:3] == ["status", "session_start", "agent_start"] and types[-2:] == ["agent_done", "session_done"]
    assert events[-2]["output"] == "Hello! How can I help?"
    assert live.snapshot("a1") == {}


def test_direct_answer_gets_post_answer_extras(monkeypatch):
    # regression: the direct-reply path used to end at session_done, so it never got suggestions/quiz/images
    answer = "## Seoul\nPalaces and markets.\n\n## Busan\nBeaches and temples. " + "More detail. " * 10
    _fake_model(monkeypatch, text=answer)
    monkeypatch.setattr("src.jobs.chat_runner.generate_suggestions", lambda *a, **kw: ["What about Jeju?"])
    cfg = dataclasses.replace(CFG, suggestions_enabled=True, quiz_enabled=False, web_images_enabled=False)
    events = []
    assistant.decide("places to visit in korea", job_id="a1x", config=cfg, on_progress=events.append)
    types = [e["type"] for e in events]
    # answer_written: the browser may send the next question while the extras are made (O52)
    assert types[-4:] == ["agent_done", "answer_written", "answer_extras", "session_done"]
    assert events[-2]["suggestions"] == ["What about Jeju?"]


@pytest.mark.parametrize("found", [[{"filename": "web_01.jpg", "mime": "image/jpeg", "anchor": "seoul"}], []])
def test_image_placeholders_come_first_then_images_clear_them(monkeypatch, found):
    # each section names its heading: only a heading that names one thing gets pictures (ISSUES_LOG O53)
    answer = "## Seoul\nSeoul has palaces and markets.\n\n## Busan\nBusan has beaches. " + "More detail. " * 10
    _fake_model(monkeypatch, text=answer)
    monkeypatch.setattr("src.tools.web_images.gather_web_images", lambda *a, **kw: found)
    cfg = dataclasses.replace(CFG, suggestions_enabled=False, quiz_enabled=False, web_images_enabled=True)
    events = []
    assistant.decide("places to visit in korea", job_id="a1w", config=cfg, on_progress=events.append)
    extras = [e for e in events if e["type"] == "answer_extras"]
    assert extras[0]["images_pending"] == ["seoul", "busan"]          # known before any search runs
    assert extras[1]["web_images"] == found                            # [] still sent: it clears them
    assert events[-1]["type"] == "session_done"


def test_preface_then_unusable_tool_call_is_not_published_as_the_answer(monkeypatch):
    # regression: "Here are some places…\n\n### 🏛️ **I" + a tool call we can't run was shown as the reply
    _fake_model(monkeypatch, text="Here are some places:\n\n### **I", calls=[{"name": "no_such_tool", "arguments": {}}])
    events = []
    d = assistant.decide("places to visit in turkey", job_id="a1z", config=CFG, on_progress=events.append)
    assert not d.answered and d.intent == "kb"
    assert "agent_done" not in [e["type"] for e in events]
    assert live.snapshot("a1z") == {}


def test_stopped_answer_gets_no_extras(monkeypatch):
    _fake_model(monkeypatch, stop=True)
    monkeypatch.setattr("src.jobs.chat_runner.generate_suggestions", lambda *a, **kw: ["never"])
    events = []
    assistant.decide("x", job_id="a1y", config=dataclasses.replace(CFG, suggestions_enabled=True),
                     on_progress=events.append)
    assert "answer_extras" not in [e["type"] for e in events]


def test_tool_call_returns_tool_and_arguments(monkeypatch):
    _fake_model(monkeypatch, calls=[{"name": "get_weather", "arguments": {"place": "Lahore"}}])
    d = assistant.decide("kal Lahore mein barish hogi?", job_id="a2", config=CFG)
    assert not d.answered and (d.intent, d.tool, d.args) == ("get_weather", "get_weather", {"place": "Lahore"})


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


@pytest.mark.parametrize("learning", [True, False])
def test_a_quiz_only_when_the_user_is_learning(monkeypatch, learning):
    # the user's rule (ISSUES_LOG O52): a quiz holds the GPU 9-17 s, so only for learning a topic
    answer = "## Seoul\nPalaces and markets.\n\n## Busan\nBeaches and temples. " + "More detail. " * 20   # past the quiz's minimum length
    _fake_model(monkeypatch, text=answer)
    monkeypatch.setattr(assistant, "route_message", lambda *a, **k: assistant.Route(learning=learning))
    monkeypatch.setattr("src.jobs.chat_runner.generate_quiz", lambda *a, **kw: [{"q": "Where are the beaches?"}])
    cfg = dataclasses.replace(CFG, suggestions_enabled=False, quiz_enabled=True, web_images_enabled=False)
    events = []
    assistant.decide("teach me about korea's cities", job_id="aq", config=cfg, on_progress=events.append)
    assert any("quiz" in e for e in events if e["type"] == "answer_extras") is learning


# ── O69: a tool the picked specialist lacks goes to the one that has it ───
@pytest.fixture
def defaults(monkeypatch):
    from src.llm.specialists import SpecialistStore, default_specialists

    specialists = {s.id: s for s in default_specialists()}
    monkeypatch.setattr(SpecialistStore, "enabled", lambda self: list(specialists.values()))
    return specialists


def test_hand_over_finds_the_specialist_with_the_tool(defaults):
    dev, it = defaults["development"], defaults["it"]
    assert "operate_server" not in dev.tools
    assert assistant.hand_over("operate_server", dev) is it
    assert assistant.hand_over("web_search", dev) is dev            # it has it: stays
    assert assistant.hand_over("", dev) is dev and assistant.hand_over("operate_server", None) is None


def test_a_server_follow_up_runs_instead_of_being_answered_from_memory(monkeypatch, defaults):
    cfg = dataclasses.replace(CFG, security_scan_enabled=True)
    _fake_model(monkeypatch, text="PM2 is not installed",
                calls=[{"name": "operate_server", "arguments": {"server": "uk", "task": "pm2 restart all"}}])
    d = assistant.decide("next run pm2 restart all", job_id="o69a", config=cfg, specialist=defaults["development"],
                         may_hand_over=True)
    assert not d.answered and (d.tool, d.args["task"]) == ("operate_server", "pm2 restart all")


def test_the_router_naming_a_server_tool_offers_it_to_the_tool_choice(monkeypatch, defaults):
    cfg = dataclasses.replace(CFG, security_scan_enabled=True)
    routed_with: list[set[str]] = []
    offered: list[set[str]] = []

    def route(query, config, conversation=None, *, tools=None, **_):
        routed_with.append({t["function"]["name"] for t in tools})
        return assistant.Route(route=assistant.TOOL, tool="operate_server", args={"task": "pm2 restart all"})

    def chat_tools(self, *, messages, tools, **_):
        offered.append({t["function"]["name"] for t in tools})
        return "", [{"name": "operate_server", "arguments": {"server": "uk", "task": "pm2 restart all"}}]

    monkeypatch.setattr(assistant, "route_message", route)
    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat_tools", chat_tools)
    d = assistant.decide("next run pm2 restart all", job_id="o69b", config=cfg, specialist=defaults["development"],
                         may_hand_over=True)
    assert "operate_server" in routed_with[0] and "operate_server" in offered[0]
    assert d.tool == "operate_server"


def test_a_skill_or_chosen_specialist_keeps_its_own_tools(monkeypatch, defaults):
    """O72: the keyword-plan skill went to the site audit once the router saw every tool."""
    cfg = dataclasses.replace(CFG, security_scan_enabled=True)
    routed_with: list[set[str]] = []

    def route(query, config, conversation=None, *, tools=None, **_):
        routed_with.append({t["function"]["name"] for t in tools})
        return assistant.Route(route=assistant.TOOL, tool="operate_server", args={"task": "pm2 restart all"})

    monkeypatch.setattr(assistant, "route_message", route)
    _fake_model(monkeypatch, text="Run it on your server yourself.",
                calls=[{"name": "operate_server", "arguments": {"task": "pm2 restart all"}}])
    d = assistant.decide("next run pm2 restart all", job_id="o72", config=cfg, specialist=defaults["development"])
    assert "operate_server" not in routed_with[0]           # the router sees the specialist's own tools
    assert d.tool != "operate_server"                         # and the job is not moved


def test_no_clarifying_questions_when_clarify_is_off(monkeypatch):
    """O73: a message sent with a skill (clarify_ok=False in the worker) is never asked clarifying questions."""
    asked: list[str] = []
    monkeypatch.setattr(assistant, "_ask_clarifying", lambda *a, **k: asked.append("x") or [{"q": 1}])
    route = assistant.Route(route=assistant.CLARIFY)
    assert assistant.pre_route(route, "To my landlord: fix the heater", CFG, None, clarify_ok=False) is None
    assert asked == []


# ── O83: the prompt names the servers the user added, so the model never says "add the server first" ───
def test_the_prompt_names_added_servers_only_when_given():
    with_servers = assistant.messages_for("go to /var/www", CFG, [], "m", servers="pk server, uk server")
    assert "already added in Settings → Devices: pk server, uk server" in with_servers[0]["content"]
    assert "already added" not in assistant.messages_for("hi", CFG, [], "m")[0]["content"]


def test_every_tools_runner_loads_and_each_tool_has_one_name():
    """The runners are "module:function" strings (imported on first use): a typo must fail here, not in a chat."""
    from src.llm.assistant import BY_JOB, TOOLS

    assert len(BY_JOB) == len(TOOLS)                     # the worker finds a tool by its job: never two per job
    # a second name only where the composer's buttons and the intent router start the same job
    assert {t.name: t.intent for t in TOOLS if t.intent} == {
        "search_knowledge_base": "kb", "generate_image": "image_gen", "draw_diagram": "diagram",
        "create_document": "doc_gen", "about_multeagent": "about", "ask_clarification": "clarify"}
    for tool in TOOLS:
        if tool.run:
            assert callable(tool.runner()), tool.name
    assert {t.name for t in TOOLS if t.admin_only} == {"security_check", "image_scan"}


def test_tool_facts_live_on_the_tool_entry():
    """Were hand lists in three files (ACTION_TOOLS, SERVER_TOOLS, NOT_RUN — ISSUES_LOG O97)."""
    from src.llm.assistant import TOOLS
    from src.llm.specialists import default_specialists

    machines = {t.name for t in TOOLS if t.acts_on_machines}
    assert machines == {"network_scan", "ping_host", "security_check", "image_scan", "server_audit", "nvr_cameras",
                        "network_device", "diagnose_server", "operate_server", "deploy_project"}
    assert {t.name for t in TOOLS if t.needs_server_login} == {
        "server_audit", "diagnose_server", "operate_server", "deploy_project"}
    assert {t.name for t in TOOLS if not t.router_runs} == {"operate_server"}
    by_id = {s.id: s for s in default_specialists()}
    assert machines <= set(by_id["it"].tools) and not machines & set(by_id["development"].tools)


def test_checks_whose_result_changes_offer_a_schedule():
    """ISSUES_LOG O99 step 5: under these answers the chat offers to repeat the check on a timetable."""
    from src.llm.assistant import TOOLS

    assert {t.name for t in TOOLS if t.repeatable} == {
        "network_scan", "ping_host", "security_check", "image_scan", "server_audit", "nvr_cameras", "network_device",
        "site_audit", "compare_sites", "keyword_research"}
    assert not any(t.repeatable and t.waits_for_user for t in TOOLS)        # nothing that changes a server


# ── questions about done work never change a server (ISSUES_LOG O105) ──

@pytest.mark.parametrize("query, tool", [
    ("di we suceesfully took the latest pull ? and build it successfully", "diagnose_server"),
    ("did the deploy work on uk?", "diagnose_server"),
    ("was the site restarted?", "diagnose_server"),
    ("deploy the latest on uk", "deploy_project"),                  # a job, not a question
    ("did you know how to deploy", "deploy_project"),               # no question mark: left to the model's pick
])
def test_a_question_about_done_work_is_checked_not_done(query, tool):
    assert assistant.as_check("deploy_project", {"server": "uk server", "project": "x"}, query)[0] == tool


def test_the_check_keeps_the_server_and_leaves_read_only_tools_alone():
    assert assistant.as_check("operate_server", {"server": "uk", "task": "pull"}, "did we pull?") == (
        "diagnose_server", {"server": "uk"})
    assert assistant.as_check("server_audit", {"server": "uk"}, "is it hardened?") == ("server_audit", {"server": "uk"})


def test_a_routed_deploy_for_a_question_becomes_the_check():
    route = assistant.Route(route="tool", tool="deploy_project", args={"server": "uk server"})
    decision = assistant.pre_route(route, "did we pull the latest and build it?", Config.from_env(), None)
    assert decision is not None and decision.tool == "diagnose_server" and decision.args == {"server": "uk server"}
