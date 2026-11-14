"""Does chat recall (ISSUES_LOG O63) bring back what a long chat's summary dropped? A memory eval (v2).

A scripted chat of ~30 exchanges, as a real one goes: facts come up in passing inside questions (an IP, a camera
channel, a serial number, a port), some only in the assistant's own answers (a command, a name it suggested),
and one changes later (the internet speed). The chat is folded the way the app folds it, round after round
(`fold_count`, `summarize`: each round rewrites the summary, so details wear away), and the folded exchanges are
stored for recall (`chat_recall.remember`, CPU). Then each probe is answered twice by the chat model: with the
summary alone (before O63) and with the summary plus the recalled pieces (after). An answer is right when it
holds every expected value. Unrelated probes show the meaning score a message gets when nothing should come back;
"Fact's piece" is the score of the best piece that holds the answer: where MIN_SIMILARITY must sit.

    python -m src.llm.bench_recall              # the chat's answers are written once (~12 min) and reused
    python -m src.llm.bench_recall --fresh      # write them again

v1 (2026-11-14: 11 exchanges, one summary pass, every fact flagged "note for later") was a tie at 6/6: the
summary kept everything. Run it when nobody is chatting: it uses the chat model on the GPU. Its pieces are
stored as user 0 (no real account) and deleted at the end. Every answer goes to logs/bench_recall.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pgvector import Vector

from src.brain.db import connect
from src.brain.embed import embed_query
from src.core.config import Config
from src.core.conversation import SUMMARY_ROLE, estimate_tokens, format_conversation_block
from src.jobs import chat_recall
from src.jobs.chat_memory import fold_count, history_budget, summarize
from src.llm.llm_client import LLMClient

BENCH_USER = 0                   # no real account has id 0: the bench's pieces never mix with a user's
CHAT_FILE = Path("logs/bench_recall_chat.json")
OUT_FILE = Path("logs/bench_recall.md")
ANSWER_TOKENS = 700              # each written answer: a real long IT answer, so the chat folds many times
PROBE_TOKENS = 300               # a probe's answer: one or two facts

# The chat, in order: (the user's message, the assistant's answer or None = the chat model writes it). A given
# answer holds a detail only the assistant said. The facts sit in the first two thirds, so all are folded.
SCRIPT: list[tuple[str, str | None]] = [
    ("We just mounted the new Hikvision NVR (DS-7616, it's on 192.168.10.45). How should I set the recording "
     "retention for 16 cameras with two 4 TB disks?", None),
    ("Explain in detail how DNS works, with the record types.", None),
    ("Channel 7 is our parking camera and it keeps going offline at night. What could cause that?", None),
    ("Suggest a naming scheme for our servers. We have a domain controller, a file server and two hypervisors "
     "in the Lahore office.",
     "Use **site-role-number**, upper case, so a name says where a machine is and what it does:\n\n"
     "| Machine | Name |\n|---|---|\n| Domain controller | LHR-DC-01 |\n| File server | LHR-FS-01 |\n"
     "| Hypervisor 1 | LHR-HV-01 |\n| Hypervisor 2 | LHR-HV-02 |\n\nKeep names under 15 characters (the NetBIOS "
     "limit), never reuse a retired name, and record each one in your inventory with its IP and serial."),
    ("How do I set up a MikroTik firewall for a small office? Give the full steps.", None),
    ("The backup job I scheduled runs at 02:30 and writes to \\\\nas01\\backups, but last night's log says "
     "'access denied'. Why would that happen?", None),
    ("Explain the difference between RAID 1, RAID 5 and RAID 10, with pros and cons.", None),
    ("Our internet is a 50 Mbps PTCL line and video calls lag in the afternoon. How do I set up QoS on the "
     "MikroTik?", None),
    ("How does the TLS handshake work, step by step?", None),
    ("Give me the exact MikroTik command to add the CCTV VLAN on bridge1.",
     "Add the VLAN interface on the bridge:\n\n```\n/interface vlan add name=vlan30-cctv vlan-id=30 "
     "interface=bridge1\n/ip address add address=192.168.30.1/24 interface=vlan30-cctv\n```\n\nThen give it a "
     "DHCP server (`/ip dhcp-server setup`, interface vlan30-cctv) and tag VLAN 30 on the switch port that goes "
     "to the NVR's PoE switch."),
    ("Write a guide to backing up a Linux server with rsync and cron.", None),
    ("For the switch: VLAN 30 is the cameras and VLAN 40 is the guest Wi-Fi. How do I stop guests from reaching "
     "the cameras?", None),
    ("Explain how VLANs and trunk ports work on a managed switch.", None),
    ("The APC SMT1500 UPS (serial AS1234567890) beeps every 5 minutes. What does that mean?", None),
    ("What are the best practices for Active Directory password policies?", None),
    ("I moved the NVR's web interface to port 8443 because 443 clashed with the reverse proxy. How do I update "
     "the firewall rule?", None),
    ("How do I monitor disk health on Linux servers with smartctl?", None),
    ("My manager Sana wants the weekly IT report every Friday by 4 PM. What should a good weekly report have?",
     None),
    ("Explain how Wi-Fi roaming works between access points.", None),
    ("The HP printer on the 2nd floor (192.168.20.31) shows offline only for the Windows 11 laptops. Why?", None),
    ("Since we moved to a 100 Mbps Nayatel fibre line last week, do I still need those QoS rules?", None),
    ("How should I plan patching for Windows servers each month?", None),
    ("Explain what a reverse proxy does, with nginx as the example.", None),
    ("How do I set up email alerts from a Hikvision NVR?", None),
    ("What should a small office check every month on its servers?", None),
    ("Explain the 3-2-1 backup rule and how to test restores.", None),
    ("How do I find which device is using the most bandwidth on a MikroTik?", None),
    ("Write a short checklist for onboarding a new employee's laptop.", None),
]


@dataclass(frozen=True)
class Probe:
    question: str
    expected: tuple[str, ...]    # every one must be in the answer (lower case, thousands commas removed)
    related: bool = True         # False: nothing in the chat answers it, nothing should be recalled


PROBES = [
    Probe("What was the IP address of our NVR?", ("192.168.10.45",)),
    Probe("Which camera channel covers the parking lot?", ("channel 7",)),
    Probe("What time does the nightly backup run, and where does it write?", ("2:30", "nas01")),
    Probe("Which VLAN is the guest Wi-Fi on?", ("40",)),
    Probe("What speed is our office internet now?", ("100 mbps",)),
    Probe("When does Sana want the weekly report?", ("friday", "4")),
    Probe("What is the serial number of our UPS?", ("as1234567890",)),
    Probe("Which port did we move the NVR's web interface to?", ("8443",)),
    Probe("What is the IP of the printer on the 2nd floor?", ("192.168.20.31",)),
    Probe("What name did you suggest for the domain controller?", ("lhr-dc-01",)),
    Probe("In your MikroTik command, what was the CCTV VLAN interface called?", ("vlan30-cctv",)),
    Probe("Write a haiku about the rain.", (), related=False),
    Probe("What is 15% of 2400?", ("360",), related=False),
]

ANSWER_SYSTEM = ("You are MulteAgent, a local IT assistant. Answer the user's latest message. For facts about the "
                 "user's own setup, use only the chat so far; when it does not say, say you do not know.")


@dataclass
class Folded:
    summary: str
    folded: list[list[dict[str, str]]]   # the folded exchanges, oldest first
    recent: list[list[dict[str, str]]]   # the exchanges the prompt still carries word for word
    rounds: int


@dataclass
class Outcome:
    variant: str
    probe: Probe
    answer: str
    seconds: float
    right: bool
    pieces: int = 0              # recalled
    recalled_fact: bool = False  # a recalled piece holds the expected values
    best_score: float = 0.0      # the best meaning score over all the chat's pieces, threshold or not
    fact_score: float = 0.0      # the best score of a piece that holds the expected values
    recall_ms: float = 0.0       # embedding the message (CPU) + the search


def _normal(text: str) -> str:
    return text.lower().replace(",", "")


def _holds(probe: Probe, text: str) -> bool:
    return all(value in _normal(text) for value in probe.expected)


def scripted_chat(config: Config, fresh: bool) -> list[list[dict[str, str]]]:
    """The chat's exchanges: SCRIPT with the chat model's answers where none is given, kept in CHAT_FILE and
    written again when SCRIPT changes."""
    if CHAT_FILE.exists() and not fresh:
        kept = json.loads(CHAT_FILE.read_text(encoding="utf-8"))
        if isinstance(kept, dict) and kept.get("script") == [message for message, _ in SCRIPT]:
            return kept["exchanges"]
    client = LLMClient(config)
    exchanges = []
    for n, (message, given) in enumerate(SCRIPT, 1):
        print(f"writing the chat {n}/{len(SCRIPT)}…", flush=True)
        answer = given or client.chat(system=ANSWER_SYSTEM, user=message, max_tokens=ANSWER_TOKENS,
                                      temperature=0.2).strip()
        exchanges.append([{"role": "user", "content": message}, {"role": "assistant", "content": answer}])
    CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_FILE.write_text(json.dumps({"script": [m for m, _ in SCRIPT], "exchanges": exchanges}, indent=1),
                         encoding="utf-8")
    return exchanges


def _tokens(exchange: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(t["content"]) for t in exchange)


def fold_like_the_app(config: Config, exchanges: list[list[dict[str, str]]],
                      summarize_fn=summarize) -> Folded:
    """The chat folded as chat_memory folds it: before each next message (and before the probe), the oldest
    exchanges go into the summary when `fold_count` says so."""
    budget = history_budget(config)
    summary, folded, recent, rounds = "", [], [], 0
    for exchange in [*exchanges, None]:
        fold = fold_count(estimate_tokens(summary), [_tokens(e) for e in recent], budget)
        if fold:
            rounds += 1
            print(f"fold round {rounds}: {fold} exchanges into the summary…", flush=True)
            summary = summarize_fn(summary, [t for e in recent[:fold] for t in e], config=config)
            folded, recent = folded + recent[:fold], recent[fold:]
        if exchange is not None:
            recent.append(exchange)
    return Folded(summary, folded, recent, rounds)


def _ask(client: LLMClient, conversation: list[dict[str, str]], question: str) -> tuple[str, float]:
    started = time.monotonic()
    answer = client.chat(system=ANSWER_SYSTEM, max_tokens=PROBE_TOKENS, temperature=0.0,
                         user=f"{format_conversation_block(conversation)}\n\n## Latest message\n{question}")
    return answer.strip(), time.monotonic() - started


def _scores(config: Config, thread: str, vector: list[float], probe: Probe) -> tuple[float, float]:
    """(the best meaning score over all the chat's pieces, the best of those holding the probe's answer)."""
    with connect(config, account_id=str(BENCH_USER)) as conn:
        rows = conn.execute("SELECT content, 1 - (embedding <=> %s) FROM brain.chat_passages WHERE thread_id = %s",
                            (Vector(vector), thread)).fetchall()
    best = max((float(score) for _, score in rows), default=0.0)
    fact = max((float(score) for content, score in rows if probe.related and _holds(probe, content)), default=0.0)
    return best, fact


def run(config: Config, chat: Folded) -> tuple[list[Outcome], int, float]:
    """→ (every probe's outcome in both variants, pieces stored, seconds to store them)."""
    thread = f"bench-recall-{uuid.uuid4().hex[:8]}"
    start = datetime.now(timezone.utc) - timedelta(days=1)
    job_ids = [f"{thread}-{n}" for n in range(len(chat.folded))]
    recent = [t for e in chat.recent for t in e]
    budget = history_budget(config)
    room = min(chat_recall.recall_budget(budget),
               budget - estimate_tokens(chat.summary) - sum(estimate_tokens(t["content"]) for t in recent))
    client, outcomes = LLMClient(config), []
    try:
        stored_at = time.monotonic()
        pieces = sum(chat_recall.remember(config, user_id=BENCH_USER, thread_id=thread, job_id=job_id,
                                          asked_at=start + timedelta(minutes=n), question=exchange[0]["content"],
                                          answer=exchange[1]["content"])
                     for n, (job_id, exchange) in enumerate(zip(job_ids, chat.folded, strict=True)))
        store_seconds = time.monotonic() - stored_at
        for probe in PROBES:
            print(f"probe: {probe.question}", flush=True)
            memory = [{"role": SUMMARY_ROLE, "content": chat.summary}, *recent]
            answer, seconds = _ask(client, memory, probe.question)
            outcomes.append(Outcome("summary only", probe, answer, seconds, _holds(probe, answer)))
            began = time.monotonic()
            vector = embed_query(probe.question, config, task=chat_recall.RECALL_TASK)
            recalled = chat_recall.recall(config, user_id=BENCH_USER, thread_id=thread, job_ids=job_ids,
                                          message=probe.question, budget=room, embed=lambda *_a, **_k: vector)
            recall_ms = (time.monotonic() - began) * 1000
            with_recall = (f"{chat.summary}\n\n{chat_recall.recall_block(recalled)}" if recalled else chat.summary)
            answer, seconds = _ask(client, [{"role": SUMMARY_ROLE, "content": with_recall}, *recent], probe.question)
            best, fact = _scores(config, thread, vector, probe)
            outcomes.append(Outcome("summary + recall", probe, answer, seconds, _holds(probe, answer), len(recalled),
                                    probe.related and any(_holds(probe, r.content) for r in recalled), best, fact,
                                    recall_ms))
    finally:
        with connect(config, account_id=str(BENCH_USER)) as conn:
            conn.execute("DELETE FROM brain.chat_passages WHERE thread_id = %s", (thread,))
    return outcomes, pieces, store_seconds


def _mark(flag: bool) -> str:
    return "✓" if flag else "✗"


def report(chat: Folded, outcomes: list[Outcome], pieces: int, store_seconds: float) -> str:
    lines = [f"# Chat recall eval v2 (O63), {datetime.now():%Y-%m-%d %H:%M}", "",
             f"{len(chat.folded)} of {len(chat.folded) + len(chat.recent)} exchanges folded in {chat.rounds} rounds; "
             f"summary {estimate_tokens(chat.summary)} tokens. {pieces} pieces stored in {store_seconds:.1f} s (CPU). "
             f"MIN_SIMILARITY = {chat_recall.MIN_SIMILARITY}.", "",
             "| Probe | Fact in the summary | Summary only | Summary + recall | Recalled the fact | Pieces "
             "| Fact's piece | Best (any) | Recall ms |",
             "|---|---|---|---|---|---|---|---|---|"]
    by_probe: dict[str, dict[str, Outcome]] = {}
    for o in outcomes:
        by_probe.setdefault(o.probe.question, {})[o.variant] = o
    for question, pair in by_probe.items():
        before, after = pair["summary only"], pair["summary + recall"]
        related = before.probe.related
        in_summary = _mark(_holds(before.probe, chat.summary)) if related else "–"
        recalled = _mark(after.recalled_fact) if related else "–"
        fact = f"{after.fact_score:.2f}" if related else "–"
        lines.append(f"| {question} | {in_summary} | {_mark(before.right)} | {_mark(after.right)} | {recalled} "
                     f"| {after.pieces} | {fact} | {after.best_score:.2f} | {after.recall_ms:.0f} |")
    for variant in ("summary only", "summary + recall"):
        mine = [o for o in outcomes if o.variant == variant and o.probe.related]
        lines.append(f"\n**{variant}**: {sum(o.right for o in mine)}/{len(mine)} right, answer median "
                     f"{statistics.median(o.seconds for o in mine):.1f} s")
    lines += ["", "## The summary", "", chat.summary, "", "## Answers", ""]
    for o in outcomes:
        lines += [f"### {o.probe.question} ({o.variant}: {'right' if o.right else 'wrong'})", "", o.answer, ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="write the scripted chat's answers again")
    parser.add_argument("--out", type=Path, default=OUT_FILE, help="where the table and every answer go")
    args = parser.parse_args(argv)
    config = Config.from_env()
    chat = fold_like_the_app(config, scripted_chat(config, args.fresh))
    text = report(chat, *run(config, chat))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text.split("\n## The summary")[0])
    print(f"\nEvery answer: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
