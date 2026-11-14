"""bench_small_models without Ollama: the app's own suggestion, quiz and summary code runs through the bench's
caller; usable outputs are counted and every output lands in the report."""

from __future__ import annotations

import json

from src.core.config import Config
from src.llm import bench_small_models as bench

GOOD = {
    "suggest": "What is a TTL in DNS?\nHow do I use dig?\nWhat does a CNAME record do?",
    "multiple-choice": json.dumps([{"question": f"Q{i}?", "options": ["a", "b", "c"], "correct": 1} for i in range(4)]),
    "memory": "**Goal**\n- 12288 on the P4000; NVR 192.168.1.64, channel 3 password changed",
}


def fake(self, *, system: str, user: str, model=None, max_tokens=None, temperature=0.2, **_):
    self.tokens_per_s = 10.0
    return next((text for key, text in GOOD.items() if key in system), "OK")   # "OK": the load call


def test_usable_outputs_are_counted_and_reported(monkeypatch):
    monkeypatch.setattr(bench.Caller, "__call__", fake)
    result = bench.measure(Config.from_env(), "tiny", {}, "CPU", runs=2)
    assert not result.error
    assert all(task.usable == [True, True] for task in result.tasks.values())
    table = bench.report([result])
    assert "| tiny | CPU |" in table and "2/2" in table and "10 tok/s" in table
    assert "kept 5/5 facts" in bench.outputs([result])


def test_a_summary_that_drops_facts_is_not_usable(monkeypatch):
    monkeypatch.setitem(GOOD, "memory", "**Goal**\n- fix the camera")
    monkeypatch.setattr(bench.Caller, "__call__", fake)
    result = bench.measure(Config.from_env(), "tiny", {}, "CPU", runs=1)
    assert result.tasks["summary"].usable == [False]
