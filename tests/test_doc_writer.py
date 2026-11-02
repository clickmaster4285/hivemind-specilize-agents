"""New documents are written section by section, each checked, then converted (BACKLOG L10)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from src.core.config import Config
from src.tools import answer_export as ax
from src.tools import doc_writer as dw

OUTLINE = {"title": "Docker Networking Basics", "format": "pdf",
           "sections": [{"heading": "Bridge networks", "covers": "the default network"},
                        {"heading": "Host networks", "covers": "sharing the host stack"},
                        {"heading": "Choosing one", "covers": "when to use which"}]}


@pytest.fixture
def config(tmp_path):
    return dataclasses.replace(Config.from_env(), artifacts_dir=tmp_path)


def fake_client(monkeypatch, outline: dict | str, bodies: list[str]):
    calls: list[str] = []
    replies = list(bodies)

    class Fake:
        def __init__(self, *a, **k):
            pass

        def chat(self, *, system, user, **kw):
            if system.startswith("You plan a document"):
                return outline if isinstance(outline, str) else "Sure:\n" + json.dumps(outline)
            calls.append(user)
            text = replies.pop(0)
            if kw.get("on_delta"):
                kw["on_delta"](text)
            return text

    monkeypatch.setattr(dw, "LLMClient", Fake)
    return calls


def test_each_section_is_written_on_its_own_and_the_file_is_named_after_the_topic(monkeypatch, config):
    calls = fake_client(monkeypatch, OUTLINE, ["- default for containers", "## Host networks\nShares the stack.",
                                               "| Need | Use |\n|---|---|\n| isolation | bridge |"])
    events: list[dict] = []
    doc, errors, _ = dw.run_document_writer("make a pdf about docker networks", job_id="j", config=config,
                                            model="m", on_progress=events.append)
    assert errors == [] and len(calls) == 3
    assert "section 2 of 3 — Host networks" in calls[1] and "- Choosing one: when to use which" in calls[1]
    assert doc.startswith("# Docker Networking Basics") and doc.count("## Host networks") == 1   # repeat dropped
    ready = next(e for e in events if e["type"] == "artifact_ready")
    assert ready["filename"] == "Docker_Networking_Basics.pdf"
    assert next(e for e in events if e["type"] == "agent_done")["output"] == doc     # re-exportable later


def test_a_failed_check_rewrites_the_section_once(monkeypatch, config):
    # section 2 first repeats section 1 (rewritten fine), section 3 is empty twice (reported)
    calls = fake_client(monkeypatch, OUTLINE, ["Bridge text.", "Bridge text.", "Host text.", "", ""])
    doc, errors, _ = dw.run_document_writer("doc", job_id="j", config=config, model="m", format="md")
    assert len(calls) == 5
    assert "Host text." in doc and "## Choosing one" not in doc
    assert errors == ["section “Choosing one”: it came back empty"]


def test_spreadsheets_and_unusable_outlines_go_to_the_agentic_worker(monkeypatch, config):
    fake_client(monkeypatch, {**OUTLINE, "format": "xlsx"}, [])
    assert dw.run_document_writer("budget sheet", job_id="j", config=config, model="m") is None
    fake_client(monkeypatch, "I cannot plan that", [])
    assert dw.run_document_writer("doc", job_id="j", config=config, model="m", format="docx") is None


def test_section_checks():
    assert dw.section_problem("  ", []) == "it came back empty"
    assert dw.section_problem("Same text", ["same text!"]) == "it repeated an earlier section"
    assert dw.section_problem("New text", ["other"]) is None
    assert dw.tidy_section("### Bridge networks\n```bash\nls", "Bridge networks") == "```bash\nls\n```"


def test_topic_title(monkeypatch, config):
    assert ax.topic_title("# Docker Basics\n\ntext", "make this a pdf", config) == "Docker Basics"
    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat", lambda self, **kw: '"Kubernetes Pod Scheduling."\n')
    assert ax.topic_title("Pods are scheduled…", "explain it", config) == "Kubernetes Pod Scheduling"

    def down(self, **kw):
        raise ConnectionError("ollama down")

    monkeypatch.setattr("src.llm.llm_client.LLMClient.chat", down)
    assert ax.topic_title("Pods are scheduled…", "explain pods", config) == "explain pods"
