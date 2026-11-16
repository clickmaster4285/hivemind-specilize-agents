"""Does the router's choice depend on the date at the top of its prompt? (ISSUES_LOG O72)

Two eval cases passed on Saturday 14 November 2026 and were routed wrong on Monday 16 November, with nothing else in
the router changed: the Excel table (→ web search) and keyword research for a named site (→ site audit). This runs
the router alone (src/llm/router.py, the app's own prompt and tool list) on every assistant eval message, once per
date, and reports the cases whose route changes with the date and the cases routed wrong on every date.

    python -m src.llm.bench_router_dates

Run it when nobody is chatting: it uses the chat model on the GPU (~1.5 s a call, ~5 min for 40 cases × 5 dates).
Report: logs/bench_router_dates.md.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from unittest import mock

import yaml

from src.core import clock
from src.core.config import Config
from src.llm import skills
from src.llm.assistant import EVAL_FILE, tool_schemas
from src.llm.router import CLARIFY, SEARCH, TOOL, Route, route_message
from src.llm.specialists import default_specialists

# Five days in a row around the change: the 14th (the eval passed) to the 16th (it missed) and two after
DATES = [datetime(2026, 11, day) for day in range(14, 19)]
REPORT = Path("logs/bench_router_dates.md")
LEFT_TO_TOOL_CHOICE = "(tool choice)"     # the router gave no verdict: not right or wrong on its own


def verdict(route: Route) -> str:
    """The router's choice in the eval's terms: a tool name, or LEFT_TO_TOOL_CHOICE."""
    if route.route == TOOL and route.tool:
        return route.tool
    return {SEARCH: "web_search", CLARIFY: "ask_clarification"}.get(route.route, LEFT_TO_TOOL_CHOICE)


def wrong(choice: str, wanted: str | list[str] | None) -> bool:
    """A verdict the router gave that the eval does not accept (no verdict is never wrong here)."""
    if choice == LEFT_TO_TOOL_CHOICE:
        return False
    return choice not in (wanted if isinstance(wanted, list) else [wanted])


def main() -> int:
    config = Config.from_env()
    cases = yaml.safe_load(EVAL_FILE.read_text(encoding="utf-8"))["cases"]
    built_in = {s.name: s for s in skills.catalog(config)}
    specialists = default_specialists()

    def tools_for(case: dict) -> list[dict]:
        """As the app: every tool, or only the skill's own tools when the case applies a skill (/keyword-plan)."""
        skill = built_in.get(case.get("skill", ""))
        if skill is None:
            return tool_schemas(config, None)
        applied = skills.apply(skill, next((c for c in specialists if c.id == skill.specialist), None))
        return tool_schemas(config, applied.tools if applied else None)

    rows: list[tuple[dict, list[str]]] = []
    started = time.perf_counter()
    for case in cases:
        conversation = [{"role": "user", "content": case["previous"]}] if case.get("previous") else None
        choices = []
        for day in DATES:
            with mock.patch.object(clock, "current_date_text", lambda *a, _day=day, **k: f"{_day:%A, %d %B %Y}"):
                choices.append(verdict(route_message(case["text"], config, conversation, tools=tools_for(case))))
        rows.append((case, choices))
        print(f"{' | '.join(choices)}  ←  {case['text'][:60]}", flush=True)

    flips = [(c, ch) for c, ch in rows if len(set(ch)) > 1]
    always_wrong = [(c, ch) for c, ch in rows if all(wrong(x, c.get("tool")) for x in ch)]
    head = "| Message | want | " + " | ".join(f"{d:%a %d}" for d in DATES) + " |"
    rule = "|---|---|" + "---|" * len(DATES)

    def table(subset: list[tuple[dict, list[str]]]) -> list[str]:
        return [head, rule] + [f"| {c['text'][:70]} | {c.get('tool')} | " + " | ".join(
            f"**{x}**" if wrong(x, c.get("tool")) else x for x in ch) + " |" for c, ch in subset]

    lines = [f"# Router vs the date in its prompt (O72), {datetime.now():%Y-%m-%d %H:%M}", "",
             f"{len(rows)} eval messages × {len(DATES)} dates in {time.perf_counter() - started:.0f} s. "
             f"Bold = a verdict the eval does not accept; {LEFT_TO_TOOL_CHOICE} = no verdict, the tool choice decides.",
             "", f"## Route changes with the date ({len(flips)})", "", *table(flips),
             "", f"## Wrong on every date ({len(always_wrong)})", "", *table(always_wrong),
             "", "## Every message", "", *table(rows)]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:lines.index("## Every message")]))
    print(f"\nEvery message: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
