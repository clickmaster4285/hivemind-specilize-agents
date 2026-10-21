"""Domain specialists — L2 (doc/BACKLOG.md). They replaced the Optimist / Cynic / Consensus debate.

A specialist is DATA, not code: a name, what it covers (read by the picker), its instructions, the
tools it may use and the knowledge-base collections it searches first. They live in
agents/specialists.json and are edited in the UI (Settings → Specialists); a new field = a new entry.

Per question (src/worker.py):
  pick()      one tiny model call reads the "covers" lines and replies with one id, or "general"
              (no specialist). Skipped when the user pinned a specialist in the chat bar.
  decide()    the specialist's instructions join the assistant's prompt; only its tools are offered
              (src/assistant.py) — network_scan is IT-only.
  answers     knowledge-base answers search its collections first, then everything (src/brain/answer.py).

Measured: python -m src.specialists --eval   (data/specialists/eval.yaml)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config import ROOT, Config

logger = logging.getLogger(__name__)

STORE_FILE = ROOT / "agents" / "specialists.json"
EVAL_FILE = ROOT / "data" / "specialists" / "eval.yaml"
COLLECTIONS = ("it", "development", "marketing", "general")      # brain.collections slugs
GENERAL = "general"                                                # the picker's "no specialist"
# tools that probe or act on machines, not only read text — the IT specialist only
ACTION_TOOLS = frozenset({"network_scan", "security_check", "server_audit", "diagnose_server",
                          "operate_server", "ping_host"})
# tools added after agents/specialists.json files already existed (files saved before "seen_tools")
ADDED_LATER = frozenset({"security_check", "server_audit", "diagnose_server", "operate_server", "ping_host"})
PICK_MAX_TOKENS = 6

PICK_SYSTEM = """Pick the specialist for the user's message. Specialists:
{menu}
- general: anything else — greetings, small talk, general knowledge, creative writing, weather, news,
  pictures, cooking, sport, questions about you.
Reply with only the id: {ids}."""


@dataclass
class Specialist:
    id: str
    name: str
    covers: str                                   # what it handles — the picker decides from this line
    instructions: str                             # joins the system prompt when it answers
    tools: list[str] = field(default_factory=list)          # assistant tool names it may use
    collections: list[str] = field(default_factory=list)    # brain collections searched first
    enabled: bool = True
    sort_order: int = 0
    accent: str = "#6b8cae"

    def to_dict(self) -> dict[str, Any]:
        # seen_tools: the tools that existed when this was saved — a tool added to the code later is new
        return {**asdict(self), "seen_tools": tool_names()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Specialist:
        names = tool_names()
        known = set(names)
        tools = [str(t) for t in data.get("tools") or [] if str(t) in known]
        # tools that did not exist when this specialist was saved: a built-in specialist gets them when its
        # defaults include them (the IT specialist gets each new IT tool); a tool an admin unchecked stays off
        seen = data.get("seen_tools")
        seen = set(seen) if isinstance(seen, list) else known - ADDED_LATER
        default = next((d for d in _defaults_by_id() if d.id == str(data["id"])), None)
        for name in names:
            if name not in seen and name not in tools and default is not None and name in default.tools:
                tools.append(name)
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "")).strip(),
            covers=str(data.get("covers", "")).strip(),
            instructions=str(data.get("instructions", "")).strip(),
            tools=tools,
            collections=[str(c) for c in data.get("collections") or [] if str(c) in COLLECTIONS],
            enabled=bool(data.get("enabled", True)),
            sort_order=int(data.get("sort_order", 0)),
            accent=str(data.get("accent") or "#6b8cae"),
        )

    def prompt_block(self) -> str:
        """What the model reads when this specialist answers."""
        return f"You answer as MulteAgent's {self.name} specialist ({self.covers}).\n{self.instructions}".strip()


def tool_names() -> list[str]:
    from src.assistant import TOOLS

    return [t.name for t in TOOLS]


def _defaults_by_id() -> list[Specialist]:
    """The built-in specialists' tool lists, for granting tools added after a file was saved."""
    return default_specialists()


def default_specialists() -> list[Specialist]:
    reading = [t for t in tool_names() if t not in ACTION_TOOLS]
    return [
        Specialist(
            id="it", name="IT", sort_order=10, accent="#3b82c4",
            covers="infrastructure and administration: Windows/Linux servers, Active Directory, DNS, DHCP, "
                   "networks and IP addresses, routers, switches, firewalls, VPN, Wi-Fi, printers, backups, "
                   "cybersecurity, troubleshooting computers; and logging into the user's servers over SSH to "
                   "run commands or create and edit files and folders on them (anything to be done ON a server)",
            instructions=(
                "- Give exact commands, cmdlets, paths and settings, and say which OS or version they apply to "
                "when it matters.\n"
                "- Check before you change: suggest read-only checks (status, logs, test commands) first. For a "
                "command that changes or deletes something, say what it changes and how to undo it.\n"
                "- Name security risks plainly (open ports, weak passwords, missing patches) and the safe default.\n"
                "- You CAN log in to Linux servers over SSH and run commands on them — but only servers the user "
                "has added in Settings → Servers, where the login is kept for this session only. Never ask for or "
                "accept a password or key in the chat; if the user sends one, tell them to add the server in "
                "Settings instead, then you can connect. On an added server you can run read-only checks "
                "(server_audit), diagnose problems (diagnose_server), and make changes (operate_server) — changes "
                "follow the server's mode (ask-first or trusted).\n"
                "- You cannot open an RDP / remote-desktop session or control a Windows GUI — you work over SSH "
                "only. That means Linux servers, or a Windows host that has the OpenSSH server enabled. If the user "
                "asks for RDP or desktop control, say that plainly and offer the SSH path instead.\n"
                "- The credential-free tools (ping_host, security_check, network_scan) only reach the local / "
                "private network (10.x, 172.16–31.x, 192.168.x). For a public server such as a VPS, do not offer "
                "those — have the user add its login in Settings and use the SSH audit instead.\n"
                "- Never help get into systems or accounts the user does not own or manage."
            ),
            tools=tool_names(), collections=["it", "general"],
        ),
        Specialist(
            id="development", name="Development", sort_order=20, accent="#7a5cc4",
            covers="writing and fixing software: programming languages, code, errors and exceptions, libraries, "
                   "APIs, databases and SQL, architecture, Git, testing, Docker, CI/CD, DevOps",
            instructions=(
                "- Show working code in the user's language and framework; keep examples minimal and runnable.\n"
                "- For an error: name the most likely cause first, then the fix.\n"
                "- Mention the version a feature needs when it matters (for example Python 3.11+).\n"
                "- Prefer the standard library and well-known tools; point out security problems you notice "
                "(secrets in code, SQL injection, unchecked input).\n"
                "- You write code and advice for the user's own computer. You cannot log into servers or run "
                "commands, create files or make changes on them — that is handled by logging into the server "
                "(the IT side). If the user asks to do something ON a server, never claim you did it; say it "
                "needs the server login and hand it over."
            ),
            tools=reading, collections=["development", "general"],
        ),
        Specialist(
            id="marketing", name="Marketing", sort_order=30, accent="#c45c8a",
            covers="promoting a business: campaigns, ads, social media, copywriting, slogans, branding, SEO, "
                   "email marketing, content plans, audiences, analytics",
            instructions=(
                "- Be concrete: write ready-to-use copy, headlines or post text, not only advice; for short copy "
                "offer 2-3 variants.\n"
                "- Tie advice to the goal, audience, budget and channel; when one that matters is missing, say "
                "which assumption you made.\n"
                "- Budgets, benchmarks and prices change: present such numbers as estimates.\n"
                "- No false claims, fake reviews or misleading urgency."
            ),
            tools=reading, collections=["marketing", "general"],
        ),
    ]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "specialist"


class SpecialistStore:
    """agents/specialists.json (local data, not in Git); created with the defaults on first use."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or STORE_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save_all(default_specialists())

    def load_all(self) -> list[Specialist]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return sorted((Specialist.from_dict(item) for item in raw), key=lambda s: (s.sort_order, s.name.lower()))

    def enabled(self) -> list[Specialist]:
        return [s for s in self.load_all() if s.enabled]

    def save_all(self, specialists: list[Specialist]) -> None:
        payload = [s.to_dict() for s in sorted(specialists, key=lambda s: (s.sort_order, s.name.lower()))]
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def get(self, specialist_id: str) -> Specialist | None:
        return next((s for s in self.load_all() if s.id == specialist_id), None)

    @staticmethod
    def _check(s: Specialist) -> None:
        if not s.name:
            raise ValueError("Name is required.")
        if not s.covers:
            raise ValueError("'Covers' is required: the assistant picks a specialist from it.")
        if s.id in {GENERAL, "auto", "none"}:
            raise ValueError(f"'{s.id}' is reserved.")

    def create(self, data: dict[str, Any]) -> Specialist:
        specialists = self.load_all()
        new_id = _slug(str(data.get("id") or data.get("name") or ""))
        if any(s.id == new_id for s in specialists) or new_id in {GENERAL, "auto", "none"}:
            new_id = f"{new_id}-{uuid.uuid4().hex[:6]}"
        specialist = Specialist.from_dict({
            **data, "id": new_id,
            "sort_order": data.get("sort_order") or max((s.sort_order for s in specialists), default=0) + 10,
        })
        self._check(specialist)
        self.save_all([*specialists, specialist])
        return specialist

    def update(self, specialist_id: str, data: dict[str, Any]) -> Specialist:
        specialists = self.load_all()
        for i, current in enumerate(specialists):
            if current.id == specialist_id:
                updated = Specialist.from_dict({**current.to_dict(), **data, "id": specialist_id})
                self._check(updated)
                specialists[i] = updated
                self.save_all(specialists)
                return updated
        raise KeyError(f"Specialist not found: {specialist_id}")

    def delete(self, specialist_id: str) -> None:
        specialists = self.load_all()
        remaining = [s for s in specialists if s.id != specialist_id]
        if len(remaining) == len(specialists):
            raise KeyError(f"Specialist not found: {specialist_id}")
        self.save_all(remaining)

    def reset_defaults(self) -> list[Specialist]:
        specialists = default_specialists()
        self.save_all(specialists)
        return specialists


# ── choosing one ─────────────────────────────────────────────────────────
def pick_prompt(specialists: list[Specialist]) -> str:
    return PICK_SYSTEM.format(
        menu="\n".join(f"- {s.id}: {s.name} — {s.covers}" for s in specialists),
        ids=", ".join([*(s.id for s in specialists), GENERAL]),
    )


def parse_pick(reply: str, specialists: list[Specialist]) -> Specialist | None:
    words = re.findall(r"[a-z0-9-]+", reply.lower())
    return next((s for s in specialists if words and s.id == words[0]), None)


def pick(
    query: str,
    *,
    config: Config,
    conversation: list[dict[str, str]] | None = None,
    specialists: list[Specialist] | None = None,
) -> Specialist | None:
    """The specialist for this message, or None (general). Never raises: no pick = the plain assistant."""
    from src.brain.answer import search_text
    from src.llm_client import LLMClient, LLMError

    choices = specialists if specialists is not None else SpecialistStore().enabled()
    if not choices:
        return None
    try:
        reply = LLMClient(config).chat(
            system=pick_prompt(choices), user=search_text(query, conversation)[:1500],
            model=config.model_fast or config.llm_model, temperature=0.0,
            max_tokens=PICK_MAX_TOKENS, max_retries=0,
        )
    except LLMError as exc:
        logger.warning("specialist pick failed: %s", exc)
        return None
    return parse_pick(reply, choices)


def resolve(
    choice: str | None,
    query: str,
    *,
    config: Config,
    conversation: list[dict[str, str]] | None = None,
) -> tuple[Specialist | None, str]:
    """The chat bar's choice: "auto" (default) = pick(); "none" = no specialist; an id = that one.
    Returns (specialist, how) with how = auto | chosen | none."""
    choice = (choice or "auto").strip().lower()
    if choice in {"none", GENERAL}:
        return None, "none"
    store = SpecialistStore()
    if choice != "auto":
        chosen = store.get(choice)
        if chosen is not None:
            return chosen, "chosen"
    return pick(query, config=config, conversation=conversation, specialists=store.enabled()), "auto"


# ── evaluation: does the picker choose the right field? ─────────────────
def evaluate(config: Config | None = None) -> dict[str, Any]:
    config = config or Config.from_env()
    cases = yaml.safe_load(EVAL_FILE.read_text(encoding="utf-8"))["cases"]
    specialists = default_specialists()
    rows = []
    for case in cases:
        started = time.perf_counter()
        got = pick(case["text"], config=config, specialists=specialists)
        rows.append({**case, "got": got.id if got else GENERAL, "seconds": round(time.perf_counter() - started, 2)})
    for row in rows:
        row["ok"] = row["got"] == row["want"]
    return {"total": len(rows), "right": sum(r["ok"] for r in rows), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval", action="store_true")
    args = parser.parse_args(argv)
    if not args.eval:
        parser.error("use --eval")
    result = evaluate()
    times = sorted(r["seconds"] for r in result["rows"])
    print(f"right {result['right']}/{result['total']}, median {times[len(times) // 2]} s, max {times[-1]} s")
    for r in result["rows"]:
        if not r["ok"]:
            print(f"  want {r['want']:<12} got {r['got']:<12} | {r['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
