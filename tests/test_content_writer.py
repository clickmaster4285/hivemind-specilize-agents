"""L13/L15 write_content: checks without a model, the editor's score, the top-pages benchmark, and the draft →
check → revise loop. Search is stubbed: no SearXNG, no internet."""

from __future__ import annotations

import dataclasses
import json

import pytest

from src.core.config import Config
from src.tools import content_writer
from src.tools.content_writer import (Brief, Critique, check_draft, draft_score, parse_critique, reading_ease,
                                      split_meta)
from src.tools.serp import Benchmark

BRIEF = Brief(title="Solar panels", kind="blog post", keywords=["solar panels", "net metering"], words=60)


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    """The loop reads the top-ranking pages for the main keyword; tests never search the real web."""
    monkeypatch.setattr(content_writer, "benchmark", lambda keyword, config, **_: None)

GOOD = """# Solar panels for your home

Solar panels cut your power bill. Net metering pays you for spare power. You can start small and grow.
Ask a local installer for a free quote. Most homes need only a few days of work. The panels last for years.
Book your free quote today.

---
Meta title: Solar Panels for Your Home: Costs and Savings
Meta description: See what solar panels cost, how net metering pays you back, and how to get a free quote today."""


def test_split_meta_separates_the_copy():
    body, meta = split_meta(GOOD)
    assert "Meta title" not in body and not body.endswith("---")
    assert meta["title"].startswith("Solar Panels") and meta["description"].startswith("See what")


def test_reading_ease_tells_plain_from_dense():
    plain = "The cat sat on the mat. It was a warm day. The sun was out. We had tea and cake. " * 3
    dense = ("Notwithstanding considerable organisational heterogeneity, interdisciplinary collaboration "
             "necessitates comprehensive institutional accommodation of methodological pluralism. ") * 3
    assert reading_ease(plain) > 80 > 20 > reading_ease(dense)
    assert reading_ease("Too short.") is None


def test_a_good_draft_passes_the_checks():
    findings = check_draft(GOOD, BRIEF)
    assert all(f.ok for f in findings), [f"{f.label}: {f.detail}" for f in findings if not f.ok]


def test_a_weak_draft_says_what_to_fix():
    weak = "# Home energy\n\n" + "We sell things that are good for you and your family and your house. " * 12
    findings = {f.label: f for f in check_draft(weak, BRIEF)}
    assert not findings["Keywords used"].ok and "solar panels" in findings["Keywords used"].detail
    assert not findings["Main keyword in title and opening"].ok
    assert not findings["Length on target"].ok
    assert not findings["No repeated sentences"].ok
    assert findings["Meta title 15–60 characters"].detail == "missing"


def test_editor_score_and_draft_score():
    critique = parse_critique('noise {"scores": {"clarity": 8, "audience_fit": 6, "persuasion": 7, "structure": 9,'
                              ' "originality": 5}, "fixes": ["Cut the second paragraph"]} noise')
    assert critique.score == 7 and critique.fixes == ["Cut the second paragraph"]
    assert parse_critique("not json").score is None
    findings = check_draft(GOOD, BRIEF)
    assert draft_score(findings, Critique(None)) == 100
    assert draft_score(findings, Critique(6.0)) == 80


class FakeLLM:
    """Brief → draft 1 (misses the keyword) → editor → draft 2 (good) → editor (satisfied)."""

    calls: list[str] = []

    def __init__(self, _config):
        pass

    def chat(self, *, system, user, on_delta=None, **_):
        if system.startswith("You plan"):
            FakeLLM.calls.append("brief")
            return json.dumps({"title": "Solar panels", "kind": "blog post", "keywords": ["net metering"],
                               "words": 60, "reader": "general", "language": "English", "web": True})
        if system.startswith("You are a strict editor"):
            FakeLLM.calls.append("editor")
            good = "Net metering" in user
            return json.dumps({"scores": dict.fromkeys(content_writer.RUBRIC, 9 if good else 5),
                               "fixes": [] if good else ["Name the product"]})
        revising = "## Problems to fix" in user
        FakeLLM.calls.append("revise" if revising else "write")
        if revising:
            assert "Name the product" in user and "Keywords used" in user
        text = GOOD if revising else "# Energy\n\nBuy our things. They are good."
        if on_delta:
            on_delta(text)
        return text


def test_the_loop_revises_until_the_draft_is_good(monkeypatch):
    FakeLLM.calls = []
    monkeypatch.setattr(content_writer, "LLMClient", FakeLLM)
    events = []
    output, errors, _ = content_writer.run_content_writer(
        "write a blog post about solar panels", job_id="t-content", config=Config.from_env(), on_progress=events.append,
        request="a blog post about solar panels", keywords="solar panels")
    assert not errors
    assert FakeLLM.calls == ["brief", "write", "editor", "revise", "editor"]
    assert output.startswith("# Solar panels for your home")
    assert "| Draft 2 ✅ |" in output and "| Draft 1 |" in output
    assert next(e for e in events if e["type"] == "agent_done")["role"] == "Content writer"


def test_the_loop_keeps_the_best_draft_when_a_revision_is_worse(monkeypatch):
    class Worse(FakeLLM):
        def chat(self, *, system, user, on_delta=None, **kw):
            if "## Problems to fix" in user:
                return "# x"
            return super().chat(system=system, user=user, on_delta=on_delta, **kw)

    monkeypatch.setattr(content_writer, "LLMClient", Worse)
    output, _errors, _ = content_writer.run_content_writer("write a blog post", job_id="t-content-2",
                                                           config=Config.from_env(), request="a blog post")
    assert output.startswith("# Energy") and "| Draft 1 ✅ |" in output and "| Draft 3" not in output


def test_the_top_pages_benchmark_becomes_a_check():
    top = Benchmark("solar panels", pages=5, words=900, phrases=["free quote", "power bill", "battery storage",
                                                                 "roof space"], titles=[])
    brief = dataclasses.replace(BRIEF, benchmark=top)
    finding = next(f for f in check_draft(GOOD, brief) if f.label == "Covers what top pages cover")
    assert finding.ok and finding.detail.startswith("2 of 4 phrases")          # free quote, power bill
    assert "battery storage" in finding.detail
    assert "median 900 words" in brief.text() and "never copy" in brief.text()
