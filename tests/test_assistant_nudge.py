"""The deterministic web_search nudge (src/assistant._should_web_search): factual/how-to questions
search + cite; actions, local/personal, code-writing and creative stay direct."""

from types import SimpleNamespace

import pytest

from src.llm.assistant import _should_web_search, pre_route

CFG = SimpleNamespace(web_search_url="http://127.0.0.1:8888")

FACTUAL = [
    "how do I configure a firewall from scratch",
    "how to set up a reverse proxy with nginx",
    "what is the difference between TCP and UDP",
    "best practices for securing an nginx server",
    "compare postgresql and mysql for a web app",
    "what are the steps to harden a fresh ubuntu server",
    "how does OSPF routing choose the best path",
    "should I use docker or a vm for this project",
]

DIRECT = [
    "hi",
    "thanks!",
    "what is in your brain",                     # about the assistant
    "what is my ip address",                     # local / personal
    "login to pk server and restart nginx",      # server action
    "ssh into web01 and check the disk",         # server action
    "write a python function to reverse a list", # code writing
    "write me a short story about a cat",        # creative
    "draw a flowchart of the login process",     # diagram
    "scan 192.168.1.0/24 for devices",           # has an IP -> local/network tool
    "translate this paragraph to french",
    "remember my name is Fahad",
]


@pytest.mark.parametrize("q", FACTUAL)
def test_factual_questions_route_to_web_search(q):
    assert _should_web_search(q, CFG, None) is True, q


@pytest.mark.parametrize("q", DIRECT)
def test_other_messages_do_not(q):
    assert _should_web_search(q, CFG, None) is False, q


def test_off_when_searxng_not_configured():
    assert _should_web_search("how to configure a firewall from scratch", SimpleNamespace(web_search_url=""), None) is False


def test_off_when_specialist_lacks_the_tool():
    sp = SimpleNamespace(tools=["search_knowledge_base"])
    assert _should_web_search("how to configure a firewall from scratch", CFG, sp) is False


def test_pre_route_sends_a_factual_question_to_web_search():
    # decide() and the eval share pre_route, so the eval scores this path as real chats take it (O44)
    routed = pre_route(FACTUAL[0], CFG, None)
    assert (routed.tool, routed.intent, routed.args) == ("web_search", "web_search", {"query": FACTUAL[0]})


def test_pre_route_leaves_other_messages_to_the_model():
    # clarify_ok=False: no clarify call (that one would need the model); DIRECT has no web question
    assert all(pre_route(q, CFG, None, clarify_ok=False) is None for q in DIRECT)
