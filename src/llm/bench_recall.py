"""Does chat recall (ISSUES_LOG O63) bring back what a long chat's summary dropped? A memory eval.

A scripted chat states facts early (an IP, a camera channel, a backup time, a VLAN decision, a fact that changes
later), then long IT questions push them out of the history budget. The app's own code folds every exchange but
the last into a summary (`summarize`) and stores the folded ones for recall (`chat_recall.remember`, embedding
on the CPU). Then each probe question is answered twice by the chat model: with the summary alone (before O63)
and with the summary plus the recalled pieces (after). An answer is right when it holds every expected value.
Unrelated probes show the meaning score a message gets when nothing should come back (for MIN_SIMILARITY).

    python -m src.llm.bench_recall              # the chat's answers are written once and reused
    python -m src.llm.bench_recall --fresh      # write the chat's answers again (~5 min)

Run it when nobody is chatting: it uses the chat model on the GPU. Its pieces are stored as user 0 (no real
account) and deleted at the end. Every answer goes to logs/bench_recall.md.
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
from src.jobs.chat_memory import history_budget, summarize
from src.llm.llm_client import LLMClient

BENCH_USER = 0                   # no real account has id 0: the bench's pieces never mix with a user's
CHAT_FILE = Path("logs/bench_recall_chat.json")
OUT_FILE = Path("logs/bench_recall.md")
ANSWER_TOKENS = 700              # each scripted answer: a real long IT answer, so the chat outgrows the budget
PROBE_TOKENS = 300               # a probe's answer: one or two facts

# The chat, in order. Facts are said early; the internet speed changes later (the newer one must win).
SCRIPT = [
    "Our main NVR is a Hikvision DS-7616 at 192.168.10.45, and camera channel 7 covers the parking lot. Just "
    "keep that in mind, I will ask about it later.",
    "Explain in detail how DNS works, with the record types.",
    "Note for later: the nightly backup job runs at 02:30 and writes to the NAS share \\\\nas01\\backups.",
    "How do I set up a MikroTik firewall for a small office? Give the full steps.",
    "Our office internet is a 50 Mbps PTCL line. Decision: VLAN 30 for the CCTV network and VLAN 40 for guests.",
    "Explain the difference between RAID 1, RAID 5 and RAID 10, with pros and cons.",
    "Update: we upgraded the office internet to a 100 Mbps Nayatel fibre line.",
    "How does the TLS handshake work, step by step?",
    "My manager Sana wants the weekly IT report every Friday by 4 PM.",
    "Write a guide to backing up a Linux server with rsync and cron.",
    "Explain how VLANs and trunk ports work on a managed switch.",
    "What should a small office check every month on its servers?",
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
    Probe("Which VLAN did we decide on for the guests?", ("vlan 40",)),
    Probe("What speed is our office internet now?", ("100 mbps",)),
    Probe("When does Sana want the weekly report?", ("friday", "4")),
    Probe("Write a haiku about the rain.", (), related=False),
    Probe("What is 15% of 2400?", ("360",), related=False),
]

ANSWER_SYSTEM = ("You are MulteAgent, a local IT assistant. Answer the user's latest message. For facts about the "
                 "user's own setup, use only the chat so far; when it does not say, say you do not know.")


@dataclass
class Outcome:
    variant: str
    probe: Probe
    answer: str
    seconds: float
    right: bool
    pieces: int = 0              # recalled
    best_score: float = 0.0      # the best meaning score over all the chat's pieces, threshold or not
    recall_ms: float = 0.0       # embedding the message (CPU) + the search


def _normal(text: str) -> str:
    return text.lower().replace(",", "")


def _right(probe: Probe, answer: str) -> bool:
    return all(value in _normal(answer) for value in probe.expected)


def scripted_chat(config: Config, fresh: bool) -> list[dict[str, str]]:
    """The chat's turns: the model's own answers to SCRIPT, written once and kept in CHAT_FILE."""
    if CHAT_FILE.exists() and not fresh:
        return json.loads(CHAT_FILE.read_text(encoding="utf-8"))
    client = LLMClient(config)
    turns = []
    for n, message in enumerate(SCRIPT, 1):
        print(f"writing the chat {n}/{len(SCRIPT)}…", flush=True)
        answer = client.chat(system=ANSWER_SYSTEM, user=message, max_tokens=ANSWER_TOKENS, temperature=0.2)
        turns += [{"role": "user", "content": message}, {"role": "assistant", "content": answer.strip()}]
    CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_FILE.write_text(json.dumps(turns, indent=1), encoding="utf-8")
    return turns


def _ask(client: LLMClient, conversation: list[dict[str, str]], question: str) -> tuple[str, float]:
    started = time.monotonic()
    answer = client.chat(system=ANSWER_SYSTEM, max_tokens=PROBE_TOKENS, temperature=0.0,
                         user=f"{format_conversation_block(conversation)}\n\n## Latest message\n{question}")
    return answer.strip(), time.monotonic() - started


def _best_score(config: Config, thread: str, vector: list[float]) -> float:
    """The best meaning score over all the chat's pieces, threshold or not: where MIN_SIMILARITY sits."""
    with connect(config, account_id=str(BENCH_USER)) as conn:
        row = conn.execute("SELECT max(1 - (embedding <=> %s)) FROM brain.chat_passages WHERE thread_id = %s",
                           (Vector(vector), thread)).fetchone()
    return float(row[0] or 0.0)


def run(config: Config, turns: list[dict[str, str]]) -> tuple[str, list[Outcome], int, float]:
    """→ (the summary, every probe's outcome in both variants, pieces stored, seconds to store them)."""
    folded, recent = turns[:-2], turns[-2:]
    print(f"summarising {len(folded) // 2} exchanges…", flush=True)
    summary = summarize("", folded, config=config)
    thread = f"bench-recall-{uuid.uuid4().hex[:8]}"
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    job_ids = [f"{thread}-{n}" for n in range(len(folded) // 2)]
    budget = history_budget(config)
    room = min(chat_recall.recall_budget(budget),
               budget - estimate_tokens(summary) - sum(estimate_tokens(t["content"]) for t in recent))
    client, outcomes = LLMClient(config), []
    try:
        stored_at = time.monotonic()
        pieces = sum(chat_recall.remember(config, user_id=BENCH_USER, thread_id=thread, job_id=job_id,
                                          asked_at=start + timedelta(minutes=n), question=folded[2 * n]["content"],
                                          answer=folded[2 * n + 1]["content"])
                     for n, job_id in enumerate(job_ids))
        store_seconds = time.monotonic() - stored_at
        for probe in PROBES:
            print(f"probe: {probe.question}", flush=True)
            answer, seconds = _ask(client, [{"role": SUMMARY_ROLE, "content": summary}, *recent], probe.question)
            outcomes.append(Outcome("summary only", probe, answer, seconds, _right(probe, answer)))
            began = time.monotonic()
            vector = embed_query(probe.question, config, task=chat_recall.RECALL_TASK)
            recalled = chat_recall.recall(config, user_id=BENCH_USER, thread_id=thread, job_ids=job_ids,
                                          message=probe.question, budget=room, embed=lambda *_a, **_k: vector)
            recall_ms = (time.monotonic() - began) * 1000
            memory = f"{summary}\n\n{chat_recall.recall_block(recalled)}" if recalled else summary
            answer, seconds = _ask(client, [{"role": SUMMARY_ROLE, "content": memory}, *recent], probe.question)
            outcomes.append(Outcome("summary + recall", probe, answer, seconds, _right(probe, answer), len(recalled),
                                    _best_score(config, thread, vector), recall_ms))
    finally:
        with connect(config, account_id=str(BENCH_USER)) as conn:
            conn.execute("DELETE FROM brain.chat_passages WHERE thread_id = %s", (thread,))
    return summary, outcomes, pieces, store_seconds


def report(summary: str, outcomes: list[Outcome], pieces: int, store_seconds: float) -> str:
    lines = [f"# Chat recall eval (O63), {datetime.now():%Y-%m-%d %H:%M}", "",
             f"{pieces} pieces stored for the folded exchanges in {store_seconds:.1f} s (CPU). "
             f"MIN_SIMILARITY = {chat_recall.MIN_SIMILARITY}.", "",
             "| Probe | Fact in the summary | Summary only | Summary + recall | Pieces | Best score (any) | Recall ms |",
             "|---|---|---|---|---|---|---|"]
    by_probe: dict[str, dict[str, Outcome]] = {}
    for o in outcomes:
        by_probe.setdefault(o.probe.question, {})[o.variant] = o
    for question, pair in by_probe.items():
        before, after = pair["summary only"], pair["summary + recall"]
        in_summary = "–" if not before.probe.related else ("yes" if _right(before.probe, summary) else "no")
        lines.append(f"| {question} | {in_summary} | {'✓' if before.right else '✗'} | {'✓' if after.right else '✗'} "
                     f"| {after.pieces} | {after.best_score:.2f} | {after.recall_ms:.0f} |")
    for variant in ("summary only", "summary + recall"):
        mine = [o for o in outcomes if o.variant == variant and o.probe.related]
        lines.append(f"\n**{variant}**: {sum(o.right for o in mine)}/{len(mine)} right, answer median "
                     f"{statistics.median(o.seconds for o in mine):.1f} s")
    lines += ["", "## The summary", "", summary, "", "## Answers", ""]
    for o in outcomes:
        lines += [f"### {o.probe.question} ({o.variant}: {'right' if o.right else 'wrong'})", "", o.answer, ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="write the scripted chat's answers again")
    parser.add_argument("--out", type=Path, default=OUT_FILE, help="where the table and every answer go")
    args = parser.parse_args(argv)
    config = Config.from_env()
    turns = scripted_chat(config, args.fresh)
    text = report(*run(config, turns))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text.split("\n## The summary")[0])
    print(f"\nEvery answer: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
