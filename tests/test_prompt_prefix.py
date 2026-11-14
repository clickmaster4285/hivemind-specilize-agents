"""The date goes last in the long system prompts (ISSUES_LOG O52): Ollama reuses the cached start of a prompt, and
a time that changes every minute near the top made it read the whole tool-choice prompt again (17 s, not 1.6 s)."""

from __future__ import annotations

from src.core.clock import date_line
from src.core.config import Config
from src.llm.assistant import messages_for
from src.llm.router import route_message

CFG = Config.from_env()
DATE_LABEL = "Current date and time:"


def _date_only_at_the_end(system: str) -> None:
    head, _, tail = system.rpartition("\n\n")
    assert DATE_LABEL not in head
    assert tail.startswith(DATE_LABEL)


def test_the_date_line_names_the_time():
    assert date_line("Saturday, 14 November 2026, 10:01 AM") == f"{DATE_LABEL} Saturday, 14 November 2026, 10:01 AM."


def test_the_tool_choice_prompt_ends_with_the_date():
    _date_only_at_the_end(messages_for("hi", CFG, None, CFG.llm_model)[0]["content"])


def test_the_router_prompt_ends_with_the_date():
    seen: dict = {}

    def chat(**kwargs):
        seen.update(kwargs)
        return '{"route": "model"}'

    route_message("hi", CFG, chat=chat)
    _date_only_at_the_end(seen["system"])
