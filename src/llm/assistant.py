"""The assistant decides by itself: answer directly, or use a tool — native tool calling.

Each tool is ONE entry in TOOLS: a name, a one-line description and its parameters. The model reads
the user's message and either answers (streamed straight to the chat) or calls a tool; the worker
then runs that tool's existing code (src/worker.py). A new tool = a new entry here + a runner —
no intent routes, no example sentences.

Measured: python -m src.llm.assistant --eval   (data/assistant/eval.yaml)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from src.jobs import live
from src.core.config import ROOT, Config
from src.core.conversation import split_summary
from src.llm.diagram_rules import DIAGRAM_RULES, MATH_RULE, NETDIAGRAM_RULES
from src.llm.router import CLARIFY, SEARCH, Route, route_message

if TYPE_CHECKING:
    from src.llm.specialists import Specialist

logger = logging.getLogger(__name__)

EVAL_FILE = ROOT / "data" / "assistant" / "eval.yaml"
# a missed case shows the start of the model's own reply: enough to tell "asked in prose" from
# "answered from memory", short enough to keep one miss on two lines
REPLY_PREVIEW_CHARS = 160


@dataclass(frozen=True)
class Tool:
    name: str
    intent: str                      # the worker branch that does the work
    description: str
    params: dict[str, str] = field(default_factory=dict)      # parameter → description (all strings)
    required: tuple[str, ...] = ()
    available: Callable[[Config], bool] = lambda config: True

    def schema(self, description: str | None = None) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": description or self.description,
            "parameters": {"type": "object", "required": list(self.required),
                           "properties": {k: {"type": "string", "description": v} for k, v in self.params.items()}},
        }}


# the SEO tools' search market (src/tools/market.py): only what the user said — the site decides otherwise
MARKET_ARG = ("two-letter country code of the market the user named (PK for Pakistan, GB for the UK, US, …), "
              "'worldwide' if they said global or worldwide; empty if they named none")

TOOLS: tuple[Tool, ...] = (
    Tool("search_knowledge_base", "kb",
         "Search MulteAgent's own trusted documents ({sources}) and answer with citations. Use when the question "
         "is specifically about those documents' topics. Not for the latest version, release or news of "
         "something — the documents are a snapshot; use web_search for those.",
         {"query": "the question"}, ("query",), lambda c: c.brain_enabled),
    Tool("web_search", "web_search",
         "Search the internet and answer WITH SOURCE LINKS. Prefer this over your own memory for substantive "
         "factual, technical or how-to questions where citing current, authoritative sources makes the answer "
         "trustworthy — e.g. 'how to configure a firewall', 'best practices for securing nginx', 'compare X vs Y', "
         "specs, security guidance — and for anything recent (news, prices, releases, today's events) or that may "
         "have changed since your training (people or companies in the news, a product, game or version you know "
         "only as upcoming). Not for "
         "greetings, opinions, or creative writing, and not when the user wants the result as a file "
         "(create_document).",
         {"query": "search words",
          "news": "'yes' when the question is about news or recent events (something that happened in the last "
                  "days, weeks or months — a scandal, an announcement, a launch, an election); empty otherwise"},
         ("query",), lambda c: bool(c.web_search_url)),
    Tool("get_weather", "weather", "Current weather and a 3-day forecast for a place.",
         {"place": "city name; empty = the user's own city"}),
    Tool("network_scan", "network_scan",
         "Actually scan the local private network now and list the devices (IP, name, MAC, open ports) as a table.",
         {"subnet": "e.g. 192.168.1.0/24; empty = this computer's network"}, (), lambda c: c.network_scan_enabled),
    Tool("ping_host", "host_check",
         "Actually ping ONE device now and check it is up / running / online: replies and response time, "
         "its name, MAC address and manufacturer, open common ports. Use for 'ping 192.168.1.20', 'is the "
         "printer up', 'check 10.0.0.5 is running'. Never answer 'I cannot ping' — call this.",
         {"host": "the device's IP address (exactly as the user wrote it) or name"}, ("host",),
         lambda c: c.network_scan_enabled),
    Tool("security_check", "security_check",
         "Actually check security now: open ports and the services/versions behind them, HTTPS certificates, "
         "risky services (RDP, Telnet, databases, SMB), and for this PC its Windows Firewall, Remote Desktop and "
         "SMBv1. Returns findings with risk and fixes. Use for 'is this PC/server secure', 'which ports are open "
         "on X', 'find risky ports' — not for just listing devices (network_scan), and not for the user's own "
         "Linux server ('my server', 'my web server', a server from Settings → Devices): server_audit logs in "
         "and checks its firewall from the inside.",
         {"target": "an IP (192.168.1.10), a network (192.168.1.0/24), 'this pc', or empty = the whole local network"},
         (), lambda c: c.security_scan_enabled),
    Tool("server_audit", "server_audit",
         "Log in to one of the user's Linux servers (added this session in Settings → Devices) and audit it "
         "read-only: ufw / iptables firewall, open ports with programs, SSH settings (root and password login), "
         "missing security updates, failed SSH logins and fail2ban. Use for 'audit my server', 'check ufw on "
         "web01', 'is my Linux server hardened'.",
         {"server": "the server's name or IP as the user added it; empty = the only one added"},
         (), lambda c: c.security_scan_enabled),
    Tool("nvr_cameras", "nvr_cameras",
         "Log in to a camera recorder (NVR/DVR, Hikvision or Dahua) added this session in Settings → Devices and "
         "list ALL its cameras as a table: channel, camera name, IP address, model, online/offline. Read-only. "
         "Use for 'tell me all the cameras on the NVR', 'which cameras are offline', 'access the NVR at "
         "192.168.1.64 and list the cameras'. Never tell the user to open the NVR's web page — call this.",
         {"nvr": "the recorder's name or IP as the user wrote it; empty = the only one added"},
         (), lambda c: c.security_scan_enabled),
    Tool("network_device", "network_device",
         "Log in to a router or switch (MikroTik) added this session in Settings → Devices and read it, read-only: "
         "model, firmware, uptime, CPU and memory, every interface/port and whether it is up, the devices connected "
         "(DHCP leases with IP, MAC and host name), the ARP table and the recent log. Use for 'check the router', "
         "'who is connected to the mikrotik', 'which ports are down on the switch', 'show the router log'.",
         {"device": "the router's or switch's name or IP as the user wrote it; empty = the only one added"},
         (), lambda c: c.security_scan_enabled),
    Tool("diagnose_server", "server_diagnose",
         "Log in to one of the user's Linux servers (Settings → Devices) and INVESTIGATE a problem read-only: "
         "the agent runs its own read-only commands (service status, logs, disk, config, processes), reads the "
         "output and keeps going until it can explain the issue, then reports the cause and the fix. Use for "
         "'log in to web01 and check why nginx is down', 'why is the server slow', 'is the disk full on db01'. "
         "It cannot change anything (read-only); applying a fix is a separate confirmed step.",
         {"server": "the server's name or IP as the user added it; empty = the only one added"},
         (), lambda c: c.security_scan_enabled),
    Tool("operate_server", "server_operate",
         "Log in to one of the user's Linux servers (Settings → Devices) and DO WORK that may change it: "
         "run a command or script, CREATE or edit files and folders, WRITE content into a file on the server, "
         "restart or install a service, edit config, apply a fix. Each changing command is gated by the server's "
         "mode (read-only, ask-first, or trusted) — truly destructive commands are always blocked, and every "
         "change is logged. Use whenever the user wants something done ON a server, e.g. 'create a folder in "
         "/var/www on web01', 'create index.html and write Hello World in that folder', 'run deploy.sh', "
         "'restart nginx', 'install htop'. When the user is already working on a server, keep using this for "
         "follow-up file and command tasks — do NOT use create_document, which only makes a local download.",
         {"server": "the server's name or IP as the user added it; empty = the only one added",
          "task": "what to do on the server (a command, a file to create/run, or the change to make)"}, ("task",),
         lambda c: c.security_scan_enabled),
    Tool("generate_image", "image_gen", "Draw a picture: photo, illustration, artwork, thumbnail, wallpaper.",
         {"prompt": "what to draw"}, ("prompt",), lambda c: c.model_image_path is not None),
    Tool("draw_diagram", "diagram",
         "Draw or change a diagram with boxes, arrows and exact labels: architecture, flowchart, network schematic, "
         "sequence, hierarchy, org chart.", {"request": "what the diagram shows"}, ("request",)),
    Tool("site_audit", "site_audit",
         "Actually read a PUBLIC website now (politely, up to a few dozen pages) and audit its SEO and content: "
         "titles, meta descriptions, headings, broken pages, image alt text, mobile, canonical, structured data, "
         "robots.txt and sitemap → a maturity score per area and the fixes in order of impact. Use for 'audit "
         "example.com', 'check the SEO of my website', 'how mature is this site', 'what should this site improve'.",
         {"url": "the website address exactly as the user wrote it, e.g. example.com",
          "track": "only when the user asks to track / monitor the site: re-audit every N days (7 = weekly, "
                   "30 = monthly); empty otherwise"}, ("url",)),
    Tool("seo_projects", "seo_projects",
         "Show the websites the user has audited or tracks, with each one's SEO score trend over time and what "
         "changed (fixed and new issues); can stop tracking a site. Use for 'show my SEO projects', 'how is my "
         "site's SEO trending', 'stop tracking example.com'.",
         {"untrack": "a website to stop tracking; empty otherwise"}),
    Tool("compare_sites", "compare_sites",
         "Compare the user's website with competitors: audits each site, scores them side by side, lists what "
         "competitors do better and the topics they cover that the user's site doesn't (content gap), with pages to "
         "write. Competitors can be named, or found from the sites ranking for a keyword. Use for 'compare mysite.com "
         "with rival.com', 'what are my competitors doing better', 'content gap vs the sites ranking for X'.",
         {"url": "the user's own website", "competitors": "comma-separated competitor websites; empty if none named",
          "keyword": "a keyword to find competitors by when none are named; empty otherwise",
          "market": MARKET_ARG}, ("url",)),
    Tool("keyword_research", "keyword_research",
         "Keyword research from live search results (approximate, no search volume): who ranks for a keyword, where "
         "a given website ranks for it, keyword ideas people search, an estimated difficulty and what the top pages "
         "cover. Use for 'keyword ideas for X', 'where does mysite.com rank for Y', 'how hard is it to rank for Z', "
         "'who ranks for Z'.",
         {"keywords": "comma-separated keywords exactly as the user wrote them (at most 5)",
          "domain": "the user's website if they named one, e.g. mysite.com; empty if none",
          "market": MARKET_ARG}, ("keywords",),
         lambda c: bool(c.web_search_url)),
    Tool("write_content", "content_write",
         "Write a full piece of marketing or web copy — blog post, article, landing page, product or service page, "
         "email, newsletter, ad or social post — in rounds: brief, draft, checks (keywords, length, readability, meta "
         "title and description) and an editor's review, then improved drafts; shows every draft's score. Also "
         "rewrites and improves an existing web page's copy when given its address. Not for stories, poems, code "
         "or a quick slogan (answer those directly).",
         {"request": "what to write: topic, audience, goal, length if said",
          "keywords": "comma-separated search keywords the user wants targeted; empty if none",
          "url": "address of an existing page to improve; empty for new content",
          "market": MARKET_ARG}, ("request",)),
    Tool("create_document", "doc_gen",
         "Create a downloadable Word, PDF, PowerPoint or Excel file for the user to save on their own computer. "
         "Use whenever the user asks for the result as a file (a PDF, a spreadsheet, 'into Excel', a deck), even "
         "when its content needs facts or prices — the file is made with them. This is NOT for creating files on a server — to create or write a file on a Linux server, use "
         "operate_server instead.",
         {"format": "docx, pdf, pptx, xlsx, html, md or txt", "request": "what the file should contain",
          "source": "'previous_answer' when the user wants an answer already given in this chat turned into the "
                    "file as it is ('this', 'that', 'the above', 'your answer' as a PDF); otherwise 'new'"},
         ("format", "request")),
    Tool("about_multeagent", "about",
         "Live facts about this assistant: what is in its knowledge base ('your brain', your data, your sources), "
         "its model, and what it can do. Use whenever the user asks about you — you do not know your sources by heart."),
    Tool("ask_clarification", "clarify",
         "Ask the user 1–2 short multiple-choice questions FIRST — but ONLY when the request is genuinely ambiguous "
         "or missing key information needed to answer well (unstated budget, scale, platform, goal, audience, "
         "experience level). Do NOT use for clear questions, greetings, or anything you can reasonably answer. "
         "After the user ticks their choices you will answer the original question using them.",
         {"questions": 'a JSON array of 1-2 items, each {"question": "...", "options": ["choice A", "choice B", ...]} '
          "with 2-4 short options per question"}, ("questions",)),
)
BY_NAME = {t.name: t for t in TOOLS}


def wants_news(args: dict[str, Any]) -> bool:
    """web_search's `news` argument (the schema asks for 'yes'): search the news too (ISSUES_LOG O47)."""
    return str(args.get("news") or "").strip().lower() == "yes"

SYSTEM = """{identity}

Current date and time: {now}.

You do NOT know by heart: what your knowledge base contains, today's facts (weather, prices, news),
or the local network. Nor anything that may have changed since your training: news and events, people
and companies in the news, products, games and software (versions, release dates, sizes, prices), laws,
rankings and statistics. If your answer would rest on such a fact, or you know the thing only as upcoming
or expected (compare with the date above), call web_search first. For those, call the tool. For a substantive factual, technical or how-to question,
prefer to cite sources rather than answer from memory: use search_knowledge_base when it is about your
knowledge-base documents, otherwise web_search (it answers with source links). Use a tool also for an
action (network scan), a file, a picture or a diagram: when the user asks for a diagram, flowchart or
schematic itself, call draw_diagram. Keep greetings, chit-chat, opinions and short creative writing
(a story, a poem, a few slogans) direct (no tool); a full blog post, article or other marketing piece
goes to write_content.
Never say you ran a command, created or changed a file, or did anything on a server, computer or network
unless a tool actually did it and returned a result. If you do not have the tool for what is asked, say so
plainly and what the user should do — do not pretend to have done it or invent command output.
Call at most one tool. When you answer directly: follow the user's intent (greeting → short and warm,
story → write it), reply in the user's language (English or Hinglish), never put images or image links
in the answer, and never output TOOL lines or XML tags.
Match the answer's depth to the question. For a substantive or how-to question, structure it for clarity:
short headings, bullet or numbered steps, a Markdown table for comparisons or structured data, and a
Mermaid diagram (a ```mermaid fenced code block) to illustrate an architecture, flow, process or
hierarchy inside an answer — use each only where it genuinely helps. A simple question gets a sentence or two. Never force a table or
diagram where plain prose is clearer.
To denote a component, status or step lightly, use a relevant emoji inline — 🖥️ server, 🗄️ database,
📁 file, ⚙️ process, 🌐 network, 🔒 security, 📦 package, ✅/⚠️/❌ status, → flow. Prefer a few apt icons
over a diagram when a diagram would be overkill.
""" + MATH_RULE + "\n" + DIAGRAM_RULES

_sources_cache: dict[str, Any] = {"at": 0.0, "text": ""}
_sources_lock = threading.Lock()


def _source_titles(config: Config) -> str:
    """Knowledge-base source titles for the tool description (cached 5 min): a new source teaches
    the model when to search it, with no code change."""
    with _sources_lock:
        if time.monotonic() - _sources_cache["at"] < 300 and _sources_cache["text"]:
            return _sources_cache["text"]
    text = "IT and programming documentation"
    try:
        from src.brain.db import connect

        with connect(config) as conn:
            titles = [r[0] for r in conn.execute("SELECT title FROM brain.source_status WHERE enabled ORDER BY title")]
        if titles:
            text = "; ".join(titles)
    except Exception as exc:  # noqa: BLE001 — the tool still works with a generic description
        logger.info("knowledge base titles unavailable: %s", exc)
    with _sources_lock:
        _sources_cache.update(at=time.monotonic(), text=text)
    return text


def tool_schemas(config: Config, allowed: list[str] | None = None) -> list[dict[str, Any]]:
    """allowed = a specialist's tools (None = every tool); about_multeagent is always offered."""
    out = []
    for tool in TOOLS:
        if not tool.available(config):
            continue
        if allowed is not None and tool.name not in allowed and tool.name not in ("about_multeagent", "ask_clarification"):
            continue
        desc = tool.description.format(sources=_source_titles(config)) if "{sources}" in tool.description else None
        out.append(tool.schema(desc))
    return out


def messages_for(
    query: str, config: Config, conversation: list[dict[str, str]] | None, model: str,
    specialist: Specialist | None = None,
    refine_note: str | None = None,
) -> list[dict]:
    from src.tools.about import identity_line
    from src.core.clock import current_time_text

    identity = identity_line(config, model)
    if specialist is not None:
        # before the tool rules, so the rules are what the model reads last (a persona placed after
        # them made it answer from memory instead of searching or drawing — eval 25/27 → see BACKLOG)
        identity += (f"\n\n{specialist.prompt_block()}\nThe tool rules below always apply to you too: when a "
                     "tool can do the thing or look it up, call the tool instead of explaining how to do it.")
    # NETDIAGRAM_RULES carries JSON braces, so it is appended AFTER .format (never inside a .format template)
    system = SYSTEM.format(identity=identity, now=current_time_text()) + NETDIAGRAM_RULES
    msgs = [{"role": "system", "content": system}]
    summary, turns = split_summary(conversation or [])
    if summary:          # a long chat's earlier part, folded into a summary (BACKLOG L12) — always kept
        msgs.append({"role": "system", "content": f"Summary of the earlier part of this chat:\n{summary['content']}"})
    for turn in turns[-8:]:
        msgs.append({"role": turn["role"], "content": turn["content"][:4000]})
    # a "Try again, better" regenerate carries the user's reason as a hidden instruction on the question
    content = f"{query}\n\n{refine_note}" if refine_note else query
    msgs.append({"role": "user", "content": content})
    return msgs


CLARIFY_SYSTEM = """The user's request below may be missing key information needed to answer it well.
If so, ask 1–2 SHORT multiple-choice questions to pin down what matters (e.g. budget, scale, platform,
goal, experience level, use case). If the request is already clear enough to answer well, ask nothing.

Output ONLY a JSON array, nothing else:
[{"question": "What is your budget?", "options": ["under $1k", "$1-3k", "$3k+"]}]
Use 2-4 short options per question, at most 2 questions. If no clarification is needed, output exactly: []"""

def _ask_clarifying(query: str, config: Config, model: str) -> list[dict[str, Any]]:
    """A dedicated call that returns multiple-choice clarifying questions (or [] if the query is clear)."""
    from src.llm.llm_client import LLMClient

    try:
        raw = LLMClient(config).chat(system=CLARIFY_SYSTEM, user=query, model=model,
                                     max_tokens=300, max_retries=0, temperature=0.2)
    except Exception as exc:  # noqa: BLE001 — clarification is optional; fall back to a normal answer
        logger.info("clarify check failed: %s", exc)
        return []
    match = re.search(r"\[.*\]", raw, re.S)      # pull the JSON array out of any stray prose/fences
    return _parse_clarify(match.group(0) if match else raw)


def _parse_clarify(args: Any) -> list[dict[str, Any]]:
    """Validate the model's clarify tool args → [{question, options:[...]}], or [] if unusable."""
    raw = args.get("questions") if isinstance(args, dict) else args
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()
        opts = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()][:5]
        if q and len(opts) >= 2:
            out.append({"question": q[:200], "options": opts})
    return out


@dataclass
class Decision:
    answered: bool                       # True: the model answered directly (already streamed + emitted)
    intent: str = "chat"                 # else: which worker branch runs the tool
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    learning: bool = False               # the user is learning a topic: the answer gets a quiz (router.py)


def _web_allowed(config: Config, specialist: Specialist | None) -> bool:
    return bool(config.web_search_url) and (specialist is None or "web_search" in specialist.tools)


def pre_route(route: Route, query: str, config: Config, specialist: Specialist | None, *,
              clarify_ok: bool = True) -> Decision | None:
    """Act on the router's verdict before the model sees the tools; None = let the model choose.
    decide() and evaluate() share it, so the eval measures the path real chats take."""
    if route.route == SEARCH and _web_allowed(config, specialist):
        logger.info("assistant: the router sends this to web_search")
        args = {"query": route.query or query} | ({"news": "yes"} if route.news else {})
        return Decision(False, "web_search", "web_search", args, learning=route.learning)
    # an under-specified recommendation: multiple-choice questions first (the model is unreliable at calling
    # a tool for this, so a dedicated call writes the choices)
    if route.route == CLARIFY and clarify_ok:
        questions = _ask_clarifying(query, config, config.model_fast or config.llm_model)
        if questions:
            logger.info("assistant: asking %d clarifying questions", len(questions))
            return Decision(False, "clarify", "ask_clarification", {"questions": questions})
    return None


def decide(
    query: str,
    *,
    job_id: str,
    config: Config,
    conversation: list[dict[str, str]] | None = None,
    on_progress: Callable[[dict], None] | None = None,
    specialist: Specialist | None = None,
    clarify_ok: bool = True,
    refine: str | None = None,
) -> Decision:
    """One model call with the tools: a direct answer streams into the chat; a tool call returns.
    With a specialist: its instructions join the prompt and only its tools are offered.
    clarify_ok=False (a follow-up to an approach/clarify) drops the ask_clarification tool, so the
    model routes normally (web search still available) but cannot ask to clarify again."""
    from src.jobs.chat_runner import build_refine_note, friendly_error
    from src.llm.llm_client import LLMClient, LLMError, LLMStopped

    emit = on_progress or (lambda event: None)
    model = config.model_fast or config.llm_model
    agent = {"agent_id": "assistant", **speaker(specialist), "role": "Direct reply", "stage": "chat", "model": model}
    started = time.perf_counter()
    opened = {"done": False}

    emit({"type": "status", "message": "Thinking…"})
    route = route_message(query, config, conversation)
    routed = pre_route(route, query, config, specialist, clarify_ok=clarify_ok)
    if routed is not None:
        return routed

    def on_delta(text: str) -> None:
        if not opened["done"]:                      # the bubble appears with the first word
            opened["done"] = True
            emit({"type": "session_start", "mode": "chat", "query": query})
            emit({"type": "agent_start", **agent})
            emit({"type": "status", "agent_id": "assistant", "message": "Writing…"})
        live.set_text(job_id, "assistant", text)

    tools = tool_schemas(config, specialist.tools if specialist else None)
    if not clarify_ok:
        tools = [t for t in tools if t.get("function", {}).get("name") != "ask_clarification"]
    try:
        text, calls = LLMClient(config).chat_tools(
            messages=messages_for(query, config, conversation, model, specialist,
                                  refine_note=build_refine_note(refine) if refine else None),
            tools=tools, model=model,
            on_delta=on_delta, should_continue=lambda: not live.is_stopped(job_id),
        )
    except LLMStopped as exc:
        return _finish_direct(emit, agent, job_id, query, opened, started,
                              (exc.partial + "\n\n_— stopped —_") if exc.partial else "_Stopped._", [],
                              "Stopped", config)
    except LLMError as exc:
        return _finish_direct(emit, agent, job_id, query, opened, started, friendly_error(exc, config),
                              [str(exc)], "Error", config)
    allowed = set(specialist.tools) | {"about_multeagent", "ask_clarification"} if specialist else None
    for call in calls:
        tool = BY_NAME.get(call["name"])
        if tool and tool.available(config) and (allowed is None or tool.name in allowed):
            if tool.name == "ask_clarification":
                questions = _parse_clarify(call["arguments"])
                if not questions:
                    continue                      # malformed clarify → ignore and fall through
                live.finish_agent(job_id, "assistant")
                logger.info("assistant chose ask_clarification (%d questions)", len(questions))
                return Decision(False, "clarify", "ask_clarification", {"questions": questions})
            live.finish_agent(job_id, "assistant")
            logger.info("assistant chose %s %s", tool.name, call["arguments"])
            return Decision(False, tool.intent, tool.name, dict(call["arguments"] or {}), learning=route.learning)
    if calls:               # it wanted a tool we can't run: its text was only a preface cut off mid-sentence
        logger.info("assistant: unusable tool call(s) %s — answering normally", [c["name"] for c in calls])
    if not text or calls:   # no usable answer: the knowledge-base path answers (its bubble replaces this one)
        live.finish_agent(job_id, "assistant")
        return Decision(False, "kb", "search_knowledge_base", {"query": query}, learning=route.learning)
    return _finish_direct(emit, agent, job_id, query, opened, started, text, [], "Direct reply", config,
                          learning=route.learning)


def speaker(specialist: Specialist | None) -> dict[str, str]:
    """Name and colour of the chat bubble: the specialist's, or the plain assistant's."""
    from src.llm.specialists import GENERAL

    if specialist is None or specialist.id == GENERAL:
        return {"name": "Assistant", "accent": "#c4a35a"}
    return {"name": f"{specialist.name} specialist", "accent": specialist.accent}


def _finish_direct(emit, agent, job_id, query, opened, started, output, errors, role, config, *,
                   learning: bool = False) -> Decision:
    if not opened["done"]:
        emit({"type": "session_start", "mode": "chat", "query": query})
        emit({"type": "agent_start", **agent})
    live.finish_agent(job_id, "assistant")
    elapsed = time.perf_counter() - started
    emit({"type": "agent_done", **{**agent, "role": role}, "elapsed_seconds": elapsed, "output": output})
    if not errors and role == "Direct reply":   # a real answer gets the same extras as the chat runner's
        from src.jobs.chat_runner import emit_answer_extras
        from src.llm.llm_client import LLMClient

        emit_answer_extras(query, output, job_id=job_id, config=config, model=agent["model"],
                           client=LLMClient(config), on_progress=emit, quiz=learning)
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return Decision(True, errors=errors)


# ── evaluation: does the model pick the right tool? ─────────────────────
def evaluate(config: Config | None = None, *, specialists: bool = False) -> dict:
    """specialists=True: each case first goes through the specialist picker, and the model then sees
    that specialist's instructions and tools — the path real chats take (src/worker.py)."""
    from src.llm import skills
    from src.llm.llm_client import LLMClient
    from src.llm.specialists import default_specialists, pick

    config = config or Config.from_env()
    built_in = {s.name: s for s in skills.catalog(config)}
    cases = yaml.safe_load(EVAL_FILE.read_text(encoding="utf-8"))["cases"]
    model = config.model_fast or config.llm_model
    client = LLMClient(config)
    choices = default_specialists()
    rows = []
    for case in cases:
        t = time.perf_counter()
        sp = pick(case["text"], config=config, specialists=choices) if specialists else None
        # skill: a built-in skill applied as /name would (its specialist answers, its instructions and tools)
        skill = built_in.get(case.get("skill", ""))
        if skill is not None:
            sp = skills.apply(skill, next((c for c in choices if c.id == skill.specialist), sp))
        routed = pre_route(route_message(case["text"], config), case["text"], config, sp)
        if routed is not None:
            got, args, reply = routed.tool, routed.args, ""
        else:
            reply, calls = client.chat_tools(messages=messages_for(case["text"], config, None, model, sp),
                                             tools=tool_schemas(config, sp.tools if sp else None), model=model,
                                             temperature=0.0)
            got, args = (calls[0]["name"], calls[0]["arguments"]) if calls else (None, {})
        rows.append({**case, "got": got, "specialist": sp.id if sp else None, "args": args,
                     "pre_routed": routed is not None, "reply": (reply or "").strip(),
                     "seconds": round(time.perf_counter() - t, 1)})
    for r in rows:
        allowed = r.get("tool")
        r["ok"] = r["got"] in allowed if isinstance(allowed, list) else r["got"] == allowed
        # expect: arguments the call must carry ("" = left empty), compared without case
        r["ok"] = r["ok"] and all(str(r["args"].get(k) or "").strip().lower() == str(v).lower()
                                  for k, v in (r.get("expect") or {}).items())
    return {"total": len(rows), "right": sum(r["ok"] for r in rows), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--specialists", action="store_true", help="pick a specialist first, as real chats do")
    args = parser.parse_args(argv)
    if not args.eval:
        parser.error("use --eval")
    result = evaluate(specialists=args.specialists)
    times = sorted(r["seconds"] for r in result["rows"])
    print(f"right {result['right']}/{result['total']}, median {times[len(times) // 2]} s")
    for r in result["rows"]:
        if not r["ok"]:
            print(f"  want {r.get('tool')!s:22} got {r['got']!s:22} | {r['text'][:70]}"
                  + (f" | want {r['expect']} got {r['args']}" if r.get("expect") else "")
                  + (" | pre-routed" if r["pre_routed"] else "")
                  + (f"\n      replied: {' '.join(r['reply'].split())[:REPLY_PREVIEW_CHARS]}" if r["reply"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
