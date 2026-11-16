"""bench_router_dates without Ollama: each date reaches the router's prompt, and flips are reported."""

from __future__ import annotations

from src.core import clock
from src.llm import bench_router_dates as bench
from src.llm.router import SEARCH, TOOL, Route


def test_each_date_reaches_the_router_and_flips_are_reported(monkeypatch, tmp_path):
    seen: list[str] = []

    def route(text, config, conversation=None, *, tools=None, **_):
        today = clock.current_date_text()              # what the router's prompt would carry
        seen.append(today)
        if "Excel" in text:                            # flips on the Monday, like the real case
            return Route(route=SEARCH) if today.startswith("Monday") else Route(route=TOOL, tool="create_document")
        return Route()

    monkeypatch.setattr(bench, "route_message", route)
    monkeypatch.setattr(bench, "REPORT", tmp_path / "r.md")
    assert bench.main() == 0
    assert {s.split(",")[0] for s in seen} == {f"{d:%A}" for d in bench.DATES}
    report = (tmp_path / "r.md").read_text()
    flips = report.split("## Route changes with the date (1)")[1].split("##")[0]
    assert "Excel" in flips and "**web_search**" in flips


def test_verdicts():
    assert bench.verdict(Route(route=TOOL, tool="site_audit")) == "site_audit"
    assert bench.verdict(Route(route=SEARCH)) == "web_search" and bench.verdict(Route()) == bench.LEFT_TO_TOOL_CHOICE
    assert bench.wrong("web_search", "create_document") and not bench.wrong("web_search", ["web_search", "x"])
    assert not bench.wrong(bench.LEFT_TO_TOOL_CHOICE, "create_document")
