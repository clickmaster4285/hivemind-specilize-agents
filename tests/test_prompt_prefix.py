"""What changes every message goes last in the tool-choice prompt (ISSUES_LOG O52): Ollama reuses the prompt it read
before up to the first changed token, and this model's template puts the tools and the whole chat after the first
system message — a minute in the date there made it read 8,800 tokens again (23 s, not 1.7 s; measured 2026-11-19)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.core.clock import current_date_text, date_line
from src.core.config import Config
from src.llm.assistant import messages_for
from src.llm.router import route_message

CFG = Config.from_env()
DATE_LABEL = "Current date and time:"


def test_the_date_line_names_the_time():
    assert date_line("Saturday, 14 November 2026, 10:01 AM") == f"{DATE_LABEL} Saturday, 14 November 2026, 10:01 AM."


def test_the_tool_choice_prompt_puts_the_date_and_the_chats_work_just_before_the_question():
    turns = [{"role": "user", "content": "deploy it"}, {"role": "assistant", "content": "Deployed."}]
    msgs = messages_for("did it work?", CFG, turns, CFG.llm_model, chat_work="This chat is working on uk server.")
    assert DATE_LABEL not in msgs[0]["content"] and "working on" not in msgs[0]["content"]   # the cached start
    assert [m["role"] for m in msgs[-4:]] == ["user", "assistant", "system", "user"]
    assert DATE_LABEL in msgs[-2]["content"] and "This chat is working on uk server." in msgs[-2]["content"]
    assert msgs[-1] == {"role": "user", "content": "did it work?"}


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
