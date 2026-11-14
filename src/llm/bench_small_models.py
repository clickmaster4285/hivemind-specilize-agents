"""Can a small model on the CPU write the follow-ups that now hold the GPU after each answer (ISSUES_LOG O52)?
Runs the app's own suggestions, quiz and chat-summary code (same prompts, same parsing) on the chat model (GPU,
the baseline) and on small models on the CPU; times each and checks each output is usable. Every output is
written to a Markdown file to read side by side.

    python -m src.llm.bench_small_models                                    # the three default small models
    python -m src.llm.bench_small_models --models qwen3:1.7b,qwen2.5:1.5b --runs 3

Pull the models first (`ollama pull qwen3:1.7b` …). Run it when nobody is chatting. The chat model stays loaded at
LLM_NUM_CTX; the small models are unloaded at the end.
"""

from __future__ import annotations

import argparse
import re
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests

from src.brain.embed import CPU_ONLY
from src.core.config import Config
from src.jobs.chat_memory import summarize
from src.llm.bench_embed import _chat, chat_alone
from src.llm.bench_resources import LOAD_SETTLE_SECONDS, REQUEST_TIMEOUT, _unload
from src.tools.quiz import generate_quiz
from src.tools.suggestions import generate_suggestions

DEFAULT_MODELS = "qwen3:1.7b,qwen3.5:0.8b,qwen2.5:1.5b"
RUNS = 2                         # each task per model: the median of 2 evens out one slow call, ~5 min in all
SUGGESTIONS = 3                  # what the app asks for (generate_suggestions' default)
QUIZ_QUESTIONS = 4               # what the app asks for (generate_quiz's default)
SUGGESTION_WORDS = (3, 12)       # the prompt asks 3-9 words; up to 12 still fits a chip
OUT_FILE = Path("logs/bench_small_models.md")

QUESTION = "How does DNS work?"
ANSWER = """## What DNS does
DNS turns a name like `example.com` into an IP address the computer can connect to.

## The steps of a lookup
1. The browser asks the operating system's **resolver**, which checks its cache first.
2. If the name is not cached, the resolver asks a **root server**, which points to the `.com` **TLD server**.
3. The TLD server points to the domain's **authoritative server**.
4. The authoritative server returns the **A record** (IPv4) or **AAAA record** (IPv6).
5. The resolver caches the answer for the record's **TTL** (for example 3600 seconds) and returns it.

## Common record types
- **A / AAAA**: name to IPv4 / IPv6 address
- **CNAME**: an alias for another name
- **MX**: the mail servers of a domain
- **TXT**: free text, used for SPF and domain verification

## When it goes wrong
A wrong or stale record sends users to the old server until the TTL runs out; `nslookup` or `dig` shows what
a resolver returns."""

CHAT = [
    ("user", "We have a Quadro P4000 with 8 GB. Which context size should the 9B model use?"),
    ("assistant", "Measured: 12288 fits fully on the GPU (5.88 GB); 16384 spills 12% to the CPU. Use 12288."),
    ("user", "Set it then. Also, our NVR is a Hikvision DS-7608 at 192.168.1.64. Can you list its cameras?"),
    ("assistant", "LLM_NUM_CTX=12288 is set. The NVR at 192.168.1.64 has 6 cameras online; channel 3 is offline."),
    ("user", "Why is channel 3 offline? It is the parking camera."),
    ("assistant", "Channel 3 (parking) answers ping but refuses the stream login: its password was changed. "
                  "Re-enter it in the NVR under Camera Management."),
    ("user", "OK. Next week I want to move the knowledge base to the new server, still open: which disk?"),
    ("assistant", "Noted. For the move, the knowledge base needs ~2 GB plus room to grow; an SSD is best."),
]
SUMMARY_FACTS = ("12288", "P4000", "192.168.1.64", "channel 3", "password")   # what a usable summary keeps
GOAL_HEADING = re.compile(r"\*\*Goal:?\*\*")   # the heading SUMMARY_SYSTEM asks for; a colon inside is fine


@dataclass
class Task:
    seconds: list[float] = field(default_factory=list)
    tokens_per_s: list[float] = field(default_factory=list)
    usable: list[bool] = field(default_factory=list)
    sample: str = ""


@dataclass
class ModelResult:
    model: str
    where: str
    tasks: dict[str, Task] = field(default_factory=dict)
    error: str = ""
    chat_tps_meanwhile: float | None = None   # the chat model's speed while this one summarises on the CPU


class Caller:
    """The model call the app's code receives as `client_chat` / `chat`: Ollama's /api/chat with the placement
    options and the app's context size. Keeps the speed of the last call."""

    def __init__(self, config: Config, options: dict):
        self.config, self.options, self.tokens_per_s = config, options, 0.0

    def __call__(self, *, system: str, user: str, model: str | None = None, max_tokens: int | None = None,
                 temperature: float = 0.2, **_ignored) -> str:
        options = {**self.options, "temperature": temperature, "num_ctx": self.config.llm_num_ctx}
        if max_tokens:
            options["num_predict"] = max_tokens
        body: dict = {"model": model, "stream": False, "options": options,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.config.llm_think is not None:
            body["think"] = self.config.llm_think
        if self.config.llm_keep_alive:
            body["keep_alive"] = self.config.llm_keep_alive
        url = f"{self.config.llm_base_url}/api/chat"
        resp = requests.post(url, json=body, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 400 and "think" in body:      # a model without thinking: send it without the flag
            body.pop("think")
            resp = requests.post(url, json=body, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        done = resp.json()
        self.tokens_per_s = done.get("eval_count", 0) / max(done.get("eval_duration", 1) / 1e9, 1e-9)
        return (done.get("message") or {}).get("content") or ""


def _suggestions(call: Caller, model: str) -> tuple[bool, str]:
    out = generate_suggestions(QUESTION, ANSWER, client_chat=call, model=model, n=SUGGESTIONS)
    low, high = SUGGESTION_WORDS
    ok = len(out) == SUGGESTIONS and all(low <= len(s.split()) <= high for s in out)
    return ok, "\n".join(f"- {s}" for s in out) or "(nothing usable)"


def _quiz(call: Caller, model: str) -> tuple[bool, str]:
    out = generate_quiz(QUESTION, ANSWER, client_chat=call, model=model, n=QUIZ_QUESTIONS)
    lines = []
    for q in out:
        lines.append(f"- {q['question']}")
        lines += [f"  {'✓' if i == q['correct'] else ' '} {o}" for i, o in enumerate(q["options"])]
    return len(out) == QUIZ_QUESTIONS, "\n".join(lines) or "(nothing usable)"


def _summary(call: Caller, model: str, config: Config) -> tuple[bool, str]:
    turns = [{"role": r, "content": c} for r, c in CHAT]
    text = summarize("", turns, config=config, model=model, chat=call)
    plain = re.sub(r"(?<=\d),(?=\d{3})", "", text).lower()        # "12,288" keeps the fact "12288"
    kept = sum(fact.lower() in plain for fact in SUMMARY_FACTS)
    return bool(GOAL_HEADING.search(text)) and kept == len(SUMMARY_FACTS), f"(kept {kept}/{len(SUMMARY_FACTS)} facts)\n\n{text}"


def measure(config: Config, model: str, options: dict, where: str, runs: int) -> ModelResult:
    result = ModelResult(model, where)
    call = Caller(config, options)
    tasks: dict[str, Callable[[], tuple[bool, str]]] = {
        "suggestions": lambda: _suggestions(call, model),
        "quiz": lambda: _quiz(call, model),
        "summary": lambda: _summary(call, model, config),
    }
    try:
        call(system="Reply OK.", user="OK", model=model, max_tokens=1)       # load it, outside the timings
        for name, run in tasks.items():
            task = result.tasks.setdefault(name, Task())
            for _ in range(runs):
                t = time.perf_counter()
                ok, text = run()
                task.seconds.append(time.perf_counter() - t)
                task.tokens_per_s.append(call.tokens_per_s)
                task.usable.append(ok)
                task.sample = task.sample or text
        if options is CPU_ONLY:
            result.chat_tps_meanwhile = _while_chatting(config, lambda: _summary(call, model, config))
    except requests.RequestException as exc:
        result.error = str(exc)
    return result


def _while_chatting(config: Config, work: Callable[[], object]) -> float:
    """The chat model's speed (GPU) while `work` runs on the CPU: does the small model slow the answer?"""
    out: dict = {}
    writer = threading.Thread(target=_chat, args=(config, out))
    writer.start()
    time.sleep(LOAD_SETTLE_SECONDS)                                 # let the chat model start writing
    work()
    writer.join()
    return out.get("tps", 0.0)


def report(results: list[ModelResult], alone_tps: float | None = None) -> str:
    names = ("suggestions", "quiz", "summary")
    alone = f" (alone: {alone_tps:.1f} tok/s)" if alone_tps else ""
    lines = ["| Model | Where | " + " | ".join(f"{n}: time, usable" for n in names)
             + f" | Speed | Chat speed while it summarises{alone} |",
             "|---|---|" + "---|" * (len(names) + 2)]
    for r in results:
        if r.error:
            lines.append(f"| {r.model} | {r.where} | failed: {r.error[:80]} |" + " |" * (len(names) + 1))
            continue
        cells = []
        for n in names:
            t = r.tasks[n]
            cells.append(f"{statistics.median(t.seconds):.1f} s, {sum(t.usable)}/{len(t.usable)}")
        speed = statistics.median(s for t in r.tasks.values() for s in t.tokens_per_s)
        meanwhile = f"{r.chat_tps_meanwhile:.1f} tok/s" if r.chat_tps_meanwhile is not None else "—"
        lines.append(f"| {r.model} | {r.where} | " + " | ".join(cells) + f" | {speed:.0f} tok/s | {meanwhile} |")
    return "\n".join(lines)


def outputs(results: list[ModelResult], alone_tps: float | None = None) -> str:
    parts = [f"# Small models for suggestions, quiz and summaries\n\nQuestion: {QUESTION}\n\n"
             f"{report(results, alone_tps)}\n\n\"usable\" checks the format only: read the quiz answers below.\n"]
    for name in ("suggestions", "quiz", "summary"):
        parts.append(f"\n## {name.title()}\n")
        for r in results:
            if not r.error:
                parts.append(f"\n### {r.model} ({r.where})\n\n{r.tasks[name].sample}\n")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default=DEFAULT_MODELS, help="small models to try on the CPU, comma-separated")
    parser.add_argument("--runs", type=int, default=RUNS, help="times each task runs per model")
    parser.add_argument("--out", type=Path, default=OUT_FILE, help="where every output is written")
    args = parser.parse_args(argv)
    config = Config.from_env()
    plan = [(config.llm_model, {}, "GPU (now)")]
    plan += [(m.strip(), CPU_ONLY, "CPU") for m in args.models.split(",") if m.strip()]
    print(f"{config.llm_model} alone …", flush=True)
    alone = chat_alone(config)
    results = []
    for model, options, where in plan:
        print(f"{model} on the {where} …", flush=True)
        results.append(measure(config, model, options, where, args.runs))
        if options is CPU_ONLY:
            _unload(config, model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(outputs(results, alone), encoding="utf-8")
    print()
    print(report(results, alone))
    print(f"\nEvery output, side by side: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
