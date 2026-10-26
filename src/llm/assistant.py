"""The assistant decides by itself: answer directly, or use a tool — native tool calling.

Each tool is ONE entry in TOOLS: a name, a one-line description and its parameters. The model reads
the user's message and either answers (streamed straight to the chat) or calls a tool; the worker
then runs that tool's existing code (src/worker.py). A new tool = a new entry here + a runner —
no intent routes, no example sentences.

Measured: python -m src.llm.assistant --eval   (data/assistant/eval.yaml)
"""

from __future__ import annotations

import argparse
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

if TYPE_CHECKING:
    from src.llm.specialists import Specialist

logger = logging.getLogger(__name__)

EVAL_FILE = ROOT / "data" / "assistant" / "eval.yaml"


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


TOOLS: tuple[Tool, ...] = (
    Tool("search_knowledge_base", "kb",
         "Search MulteAgent's own trusted documents ({sources}) and answer with citations. Use when the question "
         "is specifically about those documents' topics.",
         {"query": "the question"}, ("query",), lambda c: c.brain_enabled),
    Tool("web_search", "web_search",
         "Search the internet and answer WITH SOURCE LINKS. Prefer this over your own memory for substantive "
         "factual, technical or how-to questions where citing current, authoritative sources makes the answer "
         "trustworthy — e.g. 'how to configure a firewall', 'best practices for securing nginx', 'compare X vs Y', "
         "specs, security guidance — and for anything recent (news, prices, releases, today's events). Not for "
         "greetings, opinions, or creative writing.",
         {"query": "search words"}, ("query",), lambda c: bool(c.web_search_url)),
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
         "on X', 'find risky ports' — not for just listing devices (network_scan).",
         {"target": "an IP (192.168.1.10), a network (192.168.1.0/24), 'this pc', or empty = the whole local network"},
         (), lambda c: c.security_scan_enabled),
    Tool("server_audit", "server_audit",
         "Log in to one of the user's Linux servers (added this session in Settings → Servers) and audit it "
         "read-only: ufw / iptables firewall, open ports with programs, SSH settings (root and password login), "
         "missing security updates, failed SSH logins and fail2ban. Use for 'audit my server', 'check ufw on "
         "web01', 'is my Linux server hardened'.",
         {"server": "the server's name or IP as the user added it; empty = the only one added"},
         (), lambda c: c.security_scan_enabled),
    Tool("diagnose_server", "server_diagnose",
         "Log in to one of the user's Linux servers (Settings → Servers) and INVESTIGATE a problem read-only: "
         "the agent runs its own read-only commands (service status, logs, disk, config, processes), reads the "
         "output and keeps going until it can explain the issue, then reports the cause and the fix. Use for "
         "'log in to web01 and check why nginx is down', 'why is the server slow', 'is the disk full on db01'. "
         "It cannot change anything (read-only); applying a fix is a separate confirmed step.",
         {"server": "the server's name or IP as the user added it; empty = the only one added"},
         (), lambda c: c.security_scan_enabled),
    Tool("operate_server", "server_operate",
         "Log in to one of the user's Linux servers (Settings → Servers) and DO WORK that may change it: "
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
    Tool("create_document", "doc_gen",
         "Create a downloadable Word, PDF, PowerPoint or Excel file for the user to save on their own computer. "
         "This is NOT for creating files on a server — to create or write a file on a Linux server, use "
         "operate_server instead.",
         {"format": "docx, pdf, pptx or xlsx", "request": "what the file should contain"}, ("format", "request")),
    Tool("about_multeagent", "about",
         "Live facts about this assistant: what is in its knowledge base ('your brain', your data, your sources), "
         "its model, and what it can do. Use whenever the user asks about you — you do not know your sources by heart."),
)
BY_NAME = {t.name: t for t in TOOLS}

SYSTEM = """{identity}

Current date and time: {now}.

You do NOT know by heart: what your knowledge base contains, today's facts (weather, prices, news),
or the local network. For those, call the tool. For a substantive factual, technical or how-to question,
prefer to cite sources rather than answer from memory: use search_knowledge_base when it is about your
knowledge-base documents, otherwise web_search (it answers with source links). Use a tool also for an
action (network scan), a file, a picture or a diagram. Keep greetings, chit-chat, opinions and creative
writing direct (no tool).
Never say you ran a command, created or changed a file, or did anything on a server, computer or network
unless a tool actually did it and returned a result. If you do not have the tool for what is asked, say so
plainly and what the user should do — do not pretend to have done it or invent command output.
Call at most one tool. When you answer directly: follow the user's intent (greeting → short and warm,
story → write it), reply in the user's language (English or Hinglish), never put images or image links
in the answer, and never output TOOL lines or XML tags.
Match the answer's depth to the question. For a substantive or how-to question, structure it for clarity:
short headings, bullet or numbered steps, a Markdown table for comparisons or structured data, and a
Mermaid diagram (a ```mermaid fenced code block) for an architecture, flow, process or hierarchy — use
each only where it genuinely helps. A simple question gets a sentence or two. Never force a table or
diagram where plain prose is clearer."""

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
        if allowed is not None and tool.name not in allowed and tool.name != "about_multeagent":
            continue
        desc = tool.description.format(sources=_source_titles(config)) if "{sources}" in tool.description else None
        out.append(tool.schema(desc))
    return out


def messages_for(
    query: str, config: Config, conversation: list[dict[str, str]] | None, model: str,
    specialist: Specialist | None = None,
) -> list[dict]:
    from src.tools.about import identity_line
    from src.core.clock import current_time_text

    identity = identity_line(config, model)
    if specialist is not None:
        # before the tool rules, so the rules are what the model reads last (a persona placed after
        # them made it answer from memory instead of searching or drawing — eval 25/27 → see BACKLOG)
        identity += (f"\n\n{specialist.prompt_block()}\nThe tool rules below always apply to you too: when a "
                     "tool can do the thing or look it up, call the tool instead of explaining how to do it.")
    system = SYSTEM.format(identity=identity, now=current_time_text())
    msgs = [{"role": "system", "content": system}]
    for turn in (conversation or [])[-8:]:
        msgs.append({"role": turn["role"], "content": turn["content"][:4000]})
    msgs.append({"role": "user", "content": query})
    return msgs


@dataclass
class Decision:
    answered: bool                       # True: the model answered directly (already streamed + emitted)
    intent: str = "chat"                 # else: which worker branch runs the tool
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# A deterministic nudge so clearly factual / how-to / technical questions SEARCH and cite sources instead
# of being answered from memory (a local model does not always pick web_search on its own). Conservative:
# it fires only for question shapes that benefit from authoritative sources, and skips anything that is
# really an action, a local/personal question, code writing, or creative work.
_WEB_Q = re.compile(
    r"^\s*(how\s+(to|do|does|can|should|would)\b|what\s+(is|are|does|'s)\b|why\s+(is|are|does|do)\b|"
    r"when\s+(did|was|is|will)\b|which\b.*\b(better|best)\b|compare\b|difference\s+between\b|"
    r"best\s+practices?\b|steps?\s+to\b|guide\s+to\b|explain\s+how\b|is\s+it\s+(safe|possible|better|worth)\b|"
    r"pros\s+and\s+cons\b|should\s+i\b)",
    re.I,
)
_WEB_KW = re.compile(r"\b(best\s+practices?|step[- ]by[- ]step|\bvs\b|latest\s+version|current\s+version|how\s+to)\b", re.I)
_WEB_SKIP = re.compile(
    r"\b(you|your|yourself|multeagent)\b|"                                   # about the assistant
    r"\bmy\s+(ip|network|server|pc|computer|machine|laptop|wifi|router)\b|"  # local / personal
    r"\b(log\s*in|login|ssh|rdp|mstsc|connect\s+to)\b|"                      # server / remote actions
    r"\b(write|create|generate|fix|debug|refactor)\b.{0,30}\b(code|function|script|program|class|component|regex|sql|query)\b|"
    r"\b(story|poem|joke|lyrics|image|picture|photo|diagram|flow\s*chart|draw|paint)\b|"
    r"\b(translate|calculate|solve|remember)\b|\bmy\s+name\b|"
    r"\d{1,3}(\.\d{1,3}){3}",                                                # an IP address → local / network tool
    re.I,
)


def _should_web_search(query: str, config: Config, specialist: "Specialist | None") -> bool:
    """True when the message is a clear factual / how-to question that should be answered with web sources."""
    if not config.web_search_url:
        return False
    if specialist is not None and "web_search" not in specialist.tools:
        return False
    q = (query or "").strip()
    if len(q) < 15 or _WEB_SKIP.search(q):
        return False
    return bool(_WEB_Q.match(q) or _WEB_KW.search(q))


def decide(
    query: str,
    *,
    job_id: str,
    config: Config,
    conversation: list[dict[str, str]] | None = None,
    on_progress: Callable[[dict], None] | None = None,
    specialist: Specialist | None = None,
) -> Decision:
    """One model call with the tools: a direct answer streams into the chat; a tool call returns.
    With a specialist: its instructions join the prompt and only its tools are offered."""
    from src.jobs.chat_runner import friendly_error
    from src.llm.llm_client import LLMClient, LLMError, LLMStopped

    emit = on_progress or (lambda event: None)
    model = config.model_fast or config.llm_model
    agent = {"agent_id": "assistant", **speaker(specialist), "role": "Direct reply", "stage": "chat", "model": model}
    started = time.perf_counter()
    opened = {"done": False}

    if _should_web_search(query, config, specialist):
        logger.info("assistant: routing a factual question to web_search (nudge)")
        return Decision(False, "web_search", "web_search", {"query": query})

    def on_delta(text: str) -> None:
        if not opened["done"]:                      # the bubble appears with the first word
            opened["done"] = True
            emit({"type": "session_start", "mode": "chat", "query": query})
            emit({"type": "agent_start", **agent})
            emit({"type": "status", "agent_id": "assistant", "message": "Writing…"})
        live.set_text(job_id, "assistant", text)

    emit({"type": "status", "message": "Thinking…"})
    try:
        text, calls = LLMClient(config).chat_tools(
            messages=messages_for(query, config, conversation, model, specialist),
            tools=tool_schemas(config, specialist.tools if specialist else None), model=model,
            on_delta=on_delta, should_continue=lambda: not live.is_stopped(job_id),
        )
    except LLMStopped as exc:
        return _finish_direct(emit, agent, job_id, query, opened, started,
                              (exc.partial + "\n\n_— stopped —_") if exc.partial else "_Stopped._", [], "Stopped")
    except LLMError as exc:
        return _finish_direct(emit, agent, job_id, query, opened, started, friendly_error(exc, config), [str(exc)], "Error")
    allowed = set(specialist.tools) | {"about_multeagent"} if specialist else None
    for call in calls:
        tool = BY_NAME.get(call["name"])
        if tool and tool.available(config) and (allowed is None or tool.name in allowed):
            live.finish_agent(job_id, "assistant")
            logger.info("assistant chose %s %s", tool.name, call["arguments"])
            return Decision(False, tool.intent, tool.name, dict(call["arguments"] or {}))
    if not text:            # neither text nor a usable tool: let the knowledge-base answer path handle it
        return Decision(False, "kb", "search_knowledge_base", {"query": query})
    return _finish_direct(emit, agent, job_id, query, opened, started, text, [], "Direct reply")


def speaker(specialist: Specialist | None) -> dict[str, str]:
    """Name and colour of the chat bubble: the specialist's, or the plain assistant's."""
    if specialist is None:
        return {"name": "Assistant", "accent": "#c4a35a"}
    return {"name": f"{specialist.name} specialist", "accent": specialist.accent}


def _finish_direct(emit, agent, job_id, query, opened, started, output, errors, role) -> Decision:
    if not opened["done"]:
        emit({"type": "session_start", "mode": "chat", "query": query})
        emit({"type": "agent_start", **agent})
    live.finish_agent(job_id, "assistant")
    elapsed = time.perf_counter() - started
    emit({"type": "agent_done", **{**agent, "role": role}, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return Decision(True, errors=errors)


# ── evaluation: does the model pick the right tool? ─────────────────────
def evaluate(config: Config | None = None, *, specialists: bool = False) -> dict:
    """specialists=True: each case first goes through the specialist picker, and the model then sees
    that specialist's instructions and tools — the path real chats take (src/worker.py)."""
    from src.llm.llm_client import LLMClient
    from src.llm.specialists import default_specialists, pick

    config = config or Config.from_env()
    cases = yaml.safe_load(EVAL_FILE.read_text(encoding="utf-8"))["cases"]
    model = config.model_fast or config.llm_model
    client = LLMClient(config)
    choices = default_specialists()
    rows = []
    for case in cases:
        t = time.perf_counter()
        sp = pick(case["text"], config=config, specialists=choices) if specialists else None
        _, calls = client.chat_tools(messages=messages_for(case["text"], config, None, model, sp),
                                     tools=tool_schemas(config, sp.tools if sp else None), model=model,
                                     temperature=0.0)
        got = calls[0]["name"] if calls else None
        rows.append({**case, "got": got, "specialist": sp.id if sp else None, "args": calls[0]["arguments"] if calls else {},
                     "seconds": round(time.perf_counter() - t, 1)})
    for r in rows:
        allowed = r.get("tool")
        r["ok"] = r["got"] in allowed if isinstance(allowed, list) else r["got"] == allowed
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
            print(f"  want {r.get('tool')!s:22} got {r['got']!s:22} | {r['text'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
