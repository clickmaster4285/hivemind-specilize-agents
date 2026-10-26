"""Diagram requests: routing, Mermaid clean-up/checks, retry — no LLM needed."""

from __future__ import annotations

import dataclasses

import pytest

from src.core.config import Config
from src.tools.diagram_gen import check_mermaid, extract, fix_mermaid, run_diagram, write_diagram
from src.llm.intent_router import classify_query

# these test the example-based backup router (the assistant normally picks tools itself)
CFG = dataclasses.replace(Config.from_env(), assistant_tools=False)


@pytest.mark.parametrize(
    "query, after, intent",
    [
        ("create an architecture diagram for our FastAPI + Next.js + Ollama app", False, "diagram"),
        ("draw a flowchart of the user login process", False, "diagram"),
        ("create a schematic of a home network with router, switch and NAS", False, "diagram"),
        ("mere app ka architecture diagram banao", False, "diagram"),
        ("sequence diagram for OAuth login", False, "diagram"),
        ("explain the architecture of DNS", False, "chat"),
        ("what is a flowchart?", False, "chat"),
        ("draw a cat sitting on a sofa", False, "image_gen"),
        ("create a photorealistic image of a server room", False, "image_gen"),
        ("make a pdf report of the network diagram", False, "doc_gen"),
        ("add a Redis cache between the API and the database", True, "diagram"),
        ("add a Redis cache between the API and the database", False, "chat"),
    ],
)
def test_routing(query, after, intent):
    assert classify_query(query, config=CFG, allow_llm=False, after_diagram=after).intent == intent


def test_forced_and_manual_modes():
    assert classify_query("anything", config=CFG, force_intent="diagram").intent == "diagram"
    assert classify_query("draw a flowchart of CI/CD", requested_mode="agentic", config=CFG).intent == "diagram"


def test_last_answer_was_diagram():
    from src.jobs.jobs import _last_answer_was_diagram

    conv = [{"role": "user", "content": "draw it"}, {"role": "assistant", "content": "**X**\n```mermaid\nflowchart LR\n```"}]
    assert _last_answer_was_diagram(conv) is True
    assert _last_answer_was_diagram(conv + [{"role": "assistant", "content": "plain answer"}]) is False
    assert _last_answer_was_diagram(None) is False


def test_fix_quotes_risky_labels_only():
    code = "flowchart LR\n  web[Web (Next.js)] -->|HTTP/JSON| api[API]\n  api --> db[(Postgres: brain)]\n  q{Cache hit?}"
    fixed = fix_mermaid(code)
    assert 'web["Web (Next.js)"]' in fixed
    assert '-->|"HTTP/JSON"|' in fixed
    assert 'api[API]' in fixed                      # nothing risky: left alone
    assert 'db[("Postgres: brain")]' in fixed
    assert 'q{"Cache hit?"}' in fixed or "q{Cache hit?}" in fixed
    assert check_mermaid(fixed) == []


def test_fix_leaves_quoted_and_other_types_alone():
    code = 'flowchart TD\n  a["Already (quoted)"] --> b'
    assert fix_mermaid(code) == code
    seq = "sequenceDiagram\n  Browser->>API: POST /login (JSON)"
    assert fix_mermaid(seq) == seq


@pytest.mark.parametrize(
    "code, problem",
    [
        (None, "no ```mermaid"),
        ("hello\nA --> B", "first line must be a diagram type"),
        ('flowchart LR\n  subgraph x["X"]\n  a --> b', "1 subgraph but 0 end"),
        ("flowchart LR\n  start --> end --> stop", '"end" is used as a node id'),
        ('flowchart LR\n  a["open --> b', "unclosed quote"),
    ],
)
def test_check_finds_problems(code, problem):
    assert any(problem in p for p in check_mermaid(code))


def test_extract_reply_format():
    raw = "TITLE: Login flow\n```mermaid\nflowchart LR\n a --> b\n```\nNOTES:\n- one\n- two"
    assert extract(raw) == ("Login flow", "flowchart LR\n a --> b", "- one\n- two")
    assert extract("no diagram")[1] is None


def test_retry_when_first_answer_is_broken():
    replies = iter([
        "TITLE: T\n```mermaid\nflowchart LR\n  subgraph s\n  a --> b\n```",                      # missing end
        "TITLE: T\n```mermaid\nflowchart LR\n  subgraph s\n  a --> b\n  end\n```\nNOTES:\n- ok",
    ])
    prompts = []

    def chat(system, user, temperature):
        prompts.append(user)
        return next(replies)

    title, code, notes, problems = write_diagram("draw it", CFG, chat=chat)
    assert problems == [] and code.endswith("end") and notes == "- ok"
    assert "1 subgraph but 0 end" in prompts[1]                     # the model was told what was wrong


def test_run_diagram_reply_and_events(monkeypatch):
    import src.tools.diagram_gen as dg

    monkeypatch.setattr(dg, "write_diagram", lambda q, c, conv, chat=None: ("Arch", "flowchart LR\n a --> b", "- note", []))
    events = []
    output, errors, _ = run_diagram("draw arch diagram", job_id="j", config=CFG, on_progress=events.append)
    assert output == "**Arch**\n\n```mermaid\nflowchart LR\n a --> b\n```\n\n- note" and errors == []
    assert [e["type"] for e in events] == ["session_start", "agent_start", "status", "agent_done", "session_done"]


def _chat_from(replies):
    replies = iter(replies)
    return lambda system, user, temperature: next(replies)


GOOD = "TITLE: Login\n```mermaid\nflowchart TD\n  q{\"OK?\"} -->|\"No\"| ok[\"Access\"]\n```\nNOTES:\n- first"


def test_review_ok_keeps_the_diagram():
    title, code, notes, problems = write_diagram("login flow", CFG, chat=_chat_from([GOOD, "OK"]))
    assert 'ok["Access"]' in code and notes == "- first"


def test_review_correction_is_used():
    fixed = "TITLE: Login\n```mermaid\nflowchart TD\n  q{\"OK?\"} -->|\"No\"| deny[\"Denied\"]\n```\nNOTES:\n- fixed"
    title, code, notes, problems = write_diagram("login flow", CFG, chat=_chat_from([GOOD, fixed]))
    assert 'deny["Denied"]' in code and notes == "- fixed" and problems == []


def test_broken_review_correction_is_ignored():
    broken = "TITLE: Login\n```mermaid\nflowchart TD\n  subgraph s\n  a --> b\n```"
    title, code, notes, problems = write_diagram("login flow", CFG, chat=_chat_from([GOOD, broken]))
    assert 'ok["Access"]' in code                                   # the working first diagram stays


def test_style_lines_are_dropped():
    assert "style" not in fix_mermaid('flowchart LR\n  a --> b\n  style a fill:#f9f,stroke:#333')


@pytest.mark.parametrize("query", [
    "create an image of Technical engineering schematic of a compact security room (500 sq ft), floor plan view",
    "generate a picture of a network topology with glowing cables, cyberpunk style",
    "YouTube thumbnail for a flowchart tutorial, bold colours",
])
def test_explicit_image_requests_stay_images(query):
    assert classify_query(query, config=CFG, allow_llm=False).intent == "image_gen"


def test_diagram_without_image_words_is_a_diagram():
    q = ("diagram of school connected to classes, each class with a teacher, each teacher with students, "
         "clean white background, isometric perspective, blueprint style, precise labels")
    assert classify_query(q, config=CFG, allow_llm=False).intent == "diagram"


FLAT_MINDMAP = """mindmap
  root((School))
    Class_1["Class A"]
    Class_2["Class B"]
    Teacher_1["Teacher X"]
    Teacher_2["Teacher Y"]
    Student_1["Student 1"]
    Student_2["Student 2"]
    Student_3["Student 3"]
    style School fill:#f9f9f9,stroke:#333"""


def test_style_lines_dropped_in_every_diagram_type():
    fixed = fix_mermaid(FLAT_MINDMAP)
    assert "style" not in fixed and "fill:" not in fixed
    assert "sequenceDiagram" in fix_mermaid("sequenceDiagram\n  A->>B: hi\n  linkStyle 0 stroke:red")
    assert "linkStyle" not in fix_mermaid("sequenceDiagram\n  A->>B: hi\n  linkStyle 0 stroke:red")


def test_flat_mindmap_is_a_problem_nested_one_is_not():
    assert any("mindmap is flat" in p for p in check_mermaid(fix_mermaid(FLAT_MINDMAP)))
    nested = "mindmap\n  root((School))\n    A[Class A]\n      T[Teacher X]\n        S1[Student 1]\n        S2[Student 2]\n    B[Class B]\n      U[Teacher Y]"
    assert check_mermaid(nested) == []


def test_arrow_into_own_subgraph_is_a_problem():
    bad = 'flowchart TD\n  subgraph school["School"]\n    A["Class A"]\n    B["Class B"]\n  end\n  school --> A\n  school --> B'
    assert any('subgraph "school" and "A"' in p for p in check_mermaid(bad))
    ok = 'flowchart TD\n  school["School"] --> A["Class A"]\n  subgraph g["Grade 1"]\n    A\n  end\n  other["Office"] --> g'
    assert check_mermaid(ok) == []


@pytest.mark.parametrize("query, intent", [
    ("what is in your brain", "about"),
    ("in your brain you have any microsoft data?", "about"),
    ("tell me how you're working and how you're generating the response", "about"),
    ("in which field your specialized", "about"),
    ("who are you", "about"),
    ("what is MulteAgent", "about"),
    ("hello", "chat"),
    ("how do I configure DNS scavenging", "chat"),
    ("do you know how DNS forwarders work", "chat"),
    ("can you explain python decorators", "chat"),
    ("can you generate an image of a cat", "image_gen"),
])
def test_questions_about_the_assistant(query, intent):
    assert classify_query(query, config=CFG, allow_llm=False).intent == intent


def test_stop_ends_a_diagram(monkeypatch):
    import src.tools.diagram_gen as dg
    from src.llm.llm_client import LLMStopped

    def stopped(*a, **k):
        raise LLMStopped("")

    monkeypatch.setattr(dg, "write_diagram", stopped)
    events = []
    output, errors, _ = run_diagram("draw arch diagram", job_id="j-stop", config=CFG, on_progress=events.append)
    assert "Stopped" in output and errors == []
    assert [e for e in events if e["type"] == "agent_done"][0]["role"] == "Stopped"
