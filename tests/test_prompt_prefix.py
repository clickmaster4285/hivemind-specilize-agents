"""The date goes last in the long system prompts (ISSUES_LOG O52): Ollama reuses the cached start of a prompt, and
a time that changes every minute near the top made it read the whole tool-choice prompt again (17 s, not 1.6 s)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.core.clock import current_date_text, date_line
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


def test_the_router_prompt_has_the_date_alone_at_the_top():
    """The router keeps the date near the top (at the end it tipped an Excel request to web search) and only the
    date, which changes once a day, so the prompt's start is still reused between messages."""
    seen: dict = {}

    def chat(**kwargs):
        seen.update(kwargs)
        return '{"route": "model"}'

    route_message("hi", CFG, chat=chat)
    first_line = seen["system"].splitlines()[0]
    assert f"Current date: {current_date_text()}." in first_line
    assert DATE_LABEL not in seen["system"] and not re.search(r"\d{1,2}:\d{2}", seen["system"])


def test_the_date_alone_has_no_time():
    noon = datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc)
    assert current_date_text(noon, "Asia/Karachi") == "Saturday, 14 November 2026"
