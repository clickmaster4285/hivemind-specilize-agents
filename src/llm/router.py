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
from dataclasses import dataclass, field
from typing import Any

from src.core.config import Config
from src.core.conversation import QUESTION_CHARS

logger = logging.getLogger(__name__)

TOOL, SEARCH, CLARIFY, MODEL = "tool", "search", "clarify", "model"
ROUTE_MAX_TOKENS = 300     # one JSON object with a tool's arguments (a blog post's request, a URL); caps a runaway
TOOL_LINE_CHARS = 160      # of a tool's description in the router's list: its first sentence says what it does
PARAM_HINT_CHARS = 70      # an argument's own description, shown when this short: its form ("docx, pdf, … xlsx")
# Tools not in the router's list: web search and clarify are routes of their own; calculate runs inside an answer.
NOT_LISTED = frozenset({"web_search", "ask_clarification", "calculate"})
# Listed (so the router knows it exists and does not name a near one) but never run on its word: it changes a
# server, so only the full tool choice, with every rule in view, picks it.
NOT_RUN = frozenset({"operate_server"})

ROUTER_SYSTEM = """You sort a chat message before it is answered. Your training data is older than the current
date (at the end): anything may have changed since.

The assistant's tools (* = required argument):
{tools}

Reply with ONE JSON object and nothing else:
{{"route": "tool" or "search" or "clarify" or "model", "tool": "", "args": {{}}, "query": "",
"news": true or false, "learning": true or false, "calculation": true or false}}

route:
- First: "tool" when the message plainly asks for the job of ONE tool listed above, even when it needs facts
  that change (weather, prices, rankings): the tool gets them itself. Such jobs: checking, auditing, analysing,
  scanning or comparing a named website, URL, IP address, device, server, recorder or file (a site audit reads
  the site itself; a web search only finds pages about it); the weather; keyword and ranking research (who
  ranks on Google for a search term, where a site ranks); making a file (Word, PDF, Excel, PowerPoint); a
  diagram or a picture; marketing copy or a blog post; questions about the assistant itself (its model, its
  knowledge base, what it can do). Only when the user wants the job DONE now: asking how to do something
  yourself (steps, a guide, "how do I configure …") is not a tool's job but "search". "tool": the exact name; "args": each argument as a short string taken from
  the message, "" when the message does not say. E.g. "tell me the issues in this website from seo perspective
  https://example.pk/" → {{"route": "tool", "tool": "site_audit", "args": {{"url": "https://example.pk/"}}}};
  "put the prices of 5 phones in an Excel file" → "tool" create_document (the file is the job, not the prices).
  Not sure which tool → "model".
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
- "model": everything else: arithmetic on numbers the user gave, other writing, code, greetings, a tool's job
  when you are not sure which tool.
query: for "search", short search words: spelling fixed, a follow-up's subject filled in from the previous
message. Otherwise "".
news: true when the question is about recent events (the last days, weeks or months), such as a controversy,
scandal, case or announcement about a person or company.
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
    tool: str = ""             # for TOOL: the tool's name; pre_route checks it is offered and its arguments given
    args: dict[str, str] = field(default_factory=dict)


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _arg_text(value: Any) -> str:
    """A tool argument as the tools take it, a string: a list the model wrote ["a.com", "b.com"] → "a.com, b.com"."""
    if isinstance(value, list):
        return ", ".join(_arg_text(v) for v in value if _arg_text(v))
    return "" if value is None else str(value).strip()


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
    if route not in (TOOL, SEARCH, CLARIFY, MODEL):
        return Route()
    raw_args = data.get("args") if route == TOOL and isinstance(data.get("args"), dict) else {}
    args = {str(k): _arg_text(v) for k, v in raw_args.items() if _arg_text(v)}
    return Route(route, str(data.get("query") or "").strip() if route == SEARCH else "",
                 _flag(data.get("news")), _flag(data.get("learning")), _flag(data.get("calculation")),
                 str(data.get("tool") or "").strip() if route == TOOL else "", args)


def tool_lines(tools: list[dict[str, Any]]) -> str:
    """The router's list of tools it may name: `name(arg*: its form, arg): what it does`, from the tool-choice
    schemas, so a specialist's tools and what is switched on are the same in both. A short argument description
    is shown, so the router writes values as the tool takes them (xlsx, not "Excel")."""
    lines = []
    for schema in tools:
        fn = schema.get("function", {})
        if fn.get("name") in NOT_LISTED:
            continue
        params = fn.get("parameters", {})
        required = set(params.get("required", []))
        args = "; ".join(f"{p}{'*' if p in required else ''}" + (f": {d}" if (d := spec.get("description", ""))
                                                                  and len(d) <= PARAM_HINT_CHARS else "")
                         for p, spec in params.get("properties", {}).items())
        purpose = fn.get("description", "").split(". ")[0][:TOOL_LINE_CHARS]
        lines.append(f"- {fn.get('name')}({args}): {purpose}")
    return "\n".join(lines) or "(none)"


def previous_question(conversation: list[dict[str, str]] | None) -> str:
    """The user's previous message, cut to its subject: what a follow-up ("who is she") refers to."""
    return next((str(m.get("content") or "")[:QUESTION_CHARS] for m in reversed(conversation or [])
                 if m.get("role") == "user"), "")


def route_message(query: str, config: Config, conversation: list[dict[str, str]] | None = None, *,
                  tools: list[dict[str, Any]] | None = None, chat: Callable[..., str] | None = None) -> Route:
    """Ask the fast model where the message goes. tools: the schemas the tool choice would offer (the router may
    name one of them). Never raises: a failed call leaves it to the tool choice."""
    from src.core.clock import date_line
    from src.llm.llm_client import LLMClient

    previous = previous_question(conversation)
    user = (f"Previous message: {previous}\n\n" if previous else "") + f"Message: {query}"
    try:
        raw = (chat or LLMClient(config).chat)(
            # the date last, so the fixed rules stay a cached prefix (O52)
            system=ROUTER_SYSTEM.format(tools=tool_lines(tools or [])) + f"\n\n{date_line()}", user=user,
            model=config.model_fast or config.llm_model, max_tokens=ROUTE_MAX_TOKENS, max_retries=0,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 — routing is a hint; the tool choice still answers
        logger.info("router failed, the tool choice decides: %s", exc)
        return Route()
    route = parse_route(raw)
    logger.info("router: %s", route)
    return route
