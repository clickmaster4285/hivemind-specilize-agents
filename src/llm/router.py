"""One short model call before the tools: should this message search the web, ask to clarify, or go to the
model's own tool choice? And is the user learning a topic (then a quiz suits the answer)?

It replaces the keyword lists that pre-routed messages (ISSUES_LOG O51): they missed "how much time to download
GTA 6" (answered from memory, a guessed size) and sent "15% of 2400" or "compare my site with …" to the web.
The model judges meaning; the prompt is a few hundred tokens, so the call takes about a second.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.core.config import Config
from src.core.conversation import QUESTION_CHARS

logger = logging.getLogger(__name__)

SEARCH, CLARIFY, MODEL = "search", "clarify", "model"
ROUTE_MAX_TOKENS = 120     # one small JSON object; caps a runaway reply

ROUTER_SYSTEM = """You sort a chat message before it is answered. Current date: {now}. Your training data is
older than this date: anything may have changed since.

Reply with ONE JSON object and nothing else:
{{"route": "search" or "clarify" or "model", "query": "...", "news": true or false, "learning": true or false,
"calculation": true or false}}

route:
- "search": the answer depends on facts that may have changed since your training or that must be looked up:
  news and recent events, real people (who someone is, their life, work, roles, what they did), companies,
  films, shows and games (plot, cast, release), prices, sizes, specs or release details of products and
  software, latest versions, laws, rules and policies, statistics, schedules. A question that needs such a fact
  for a calculation (a download time, a cost) is "search" too.
- "clarify": ONLY when the user asks you to recommend or choose something for them (what to buy, which one to
  pick, how to start) and gives no budget, purpose or size. Never for any other question, never when the message
  names a concrete target such as an IP address, a website or a file.
  A short or unclear message ("name of the lead actress", "who is she", "what did she do", or an answer like
  "Spider-Man: Brand New Day") continues the previous message: join the two and route the joined question.
- "model": everything else: arithmetic on numbers the user gave, writing, code, greetings, questions about you,
  actions on the user's own devices, servers or websites (scan, ping, audit, compare sites), making a file.
query: for "search", short search words: spelling fixed, a follow-up's subject filled in from the previous
message. Otherwise "".
news: true when the question is about recent events (the last days, weeks or months).
learning: true when the user wants to learn or understand a topic (an explanation, a lesson, how something
works); false for news, quick facts, tasks and chat.
calculation: true when the answer needs a number worked out (a download or travel time, a cost, a total, a
conversion between units)."""


@dataclass(frozen=True)
class Route:
    route: str = MODEL         # MODEL = the normal tool choice decides (also when the router fails)
    query: str = ""            # search words, for SEARCH
    news: bool = False
    learning: bool = False
    calculation: bool = False  # the answer needs a number worked out: the calculate tool is asked for (O57)


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def parse_route(raw: str) -> Route:
    """The model's reply → Route; anything unusable → Route() (the model's own tool choice)."""
    match = re.search(r"\{.*\}", raw or "", re.S)          # the JSON object, without stray prose or fences
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        return Route()
    route = str(data.get("route") or "").strip().lower()
    if route not in (SEARCH, CLARIFY, MODEL):
        return Route()
    return Route(route, str(data.get("query") or "").strip() if route == SEARCH else "",
                 _flag(data.get("news")), _flag(data.get("learning")), _flag(data.get("calculation")))


def previous_question(conversation: list[dict[str, str]] | None) -> str:
    """The user's previous message, cut to its subject: what a follow-up ("who is she") refers to."""
    return next((str(m.get("content") or "")[:QUESTION_CHARS] for m in reversed(conversation or [])
                 if m.get("role") == "user"), "")


def route_message(query: str, config: Config, conversation: list[dict[str, str]] | None = None, *,
                  chat: Callable[..., str] | None = None) -> Route:
    """Ask the fast model where the message goes. Never raises: a failed call leaves it to the tool choice."""
    from src.core.clock import current_time_text
    from src.llm.llm_client import LLMClient

    previous = previous_question(conversation)
    user = (f"Previous message: {previous}\n\n" if previous else "") + f"Message: {query}"
    try:
        raw = (chat or LLMClient(config).chat)(
            system=ROUTER_SYSTEM.format(now=current_time_text()), user=user,
            model=config.model_fast or config.llm_model, max_tokens=ROUTE_MAX_TOKENS, max_retries=0,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 — routing is a hint; the tool choice still answers
        logger.info("router failed, the tool choice decides: %s", exc)
        return Route()
    route = parse_route(raw)
    logger.info("router: %s", route)
    return route
