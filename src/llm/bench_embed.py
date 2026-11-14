"""Where the embedding model should run (RESOURCES.md section 5): the CPU, as the app runs it (src/brain/embed.py),
or the GPU. For each placement: load time, where Ollama put it, GPU memory it takes, one search query, a batch of
document chunks (adding a document), and a query while the chat model is writing (does one wait for the other?).

    python -m src.llm.bench_embed                 # both placements
    python -m src.llm.bench_embed --runs 20       # more repeats for steadier medians

Run it when nobody is chatting. The chat model is loaded at LLM_NUM_CTX (as the app uses it) and left loaded; the
embedding model is unloaded at the end, and the app's next search loads it on the CPU again.
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time
from dataclasses import dataclass

from src.brain.chunking import TARGET_TOKENS
from src.brain.embed import BATCH_SIZE, CPU_ONLY
from src.core.config import Config
from src.llm.bench_resources import (CHARS_PER_TOKEN, FILLER, LOAD_SETTLE_SECONDS, OUTPUT_TOKENS, _gpu, _loaded,
                                     _post, _unload)

QUERY_RUNS = 10            # single-query timings per placement: enough for a steady median, a few seconds on the CPU
BATCH_RUNS = 3             # batches of BATCH_SIZE chunks: each is the app's real unit of work when adding a document
BUSY_QUERIES = 5           # queries sent while the chat model writes OUTPUT_TOKENS tokens
QUERY = "Instruct: Given an IT question, retrieve documentation passages that answer it\nQuery: how do I reset the NVR"
PLACEMENTS = {"CPU (the app)": CPU_ONLY, "GPU": {}}


@dataclass
class Result:
    name: str
    load_s: float = 0.0
    on_gpu: str = ""
    gpu_mb: int | None = None
    query_ms: float = 0.0
    chunk_ms: float = 0.0
    busy_query_ms: float = 0.0
    busy_chat_tps: float = 0.0


def _embed(config: Config, texts: str | list[str], options: dict) -> float:
    """Seconds for one embedding call."""
    t = time.perf_counter()
    _post(config, "/api/embed", {"model": config.model_embed, "input": texts, "truncate": True, "options": options})
    return time.perf_counter() - t


def _chat(config: Config, out: dict) -> None:
    """The chat model writes OUTPUT_TOKENS tokens at the app's context size; its speed goes into out."""
    think = {"think": config.llm_think} if config.llm_think is not None else {}
    done = _post(config, "/api/generate", {
        "model": config.llm_model, "stream": False, **think,
        "prompt": "In three short paragraphs, describe a small office network and what could fail first.",
        "options": {"num_ctx": config.llm_num_ctx, "num_predict": OUTPUT_TOKENS, "temperature": 0}})
    out["tps"] = done.get("eval_count", 0) / max(done.get("eval_duration", 1) / 1e9, 1e-9)


def chat_alone(config: Config) -> float:
    out: dict = {}
    _chat(config, out)
    return out["tps"]


def measure(config: Config, name: str, options: dict, runs: int) -> Result:
    res = Result(name)
    _unload(config, config.model_embed)
    time.sleep(LOAD_SETTLE_SECONDS)
    before = _gpu()
    res.load_s = _embed(config, QUERY, options)
    time.sleep(LOAD_SETTLE_SECONDS)
    after = _gpu()
    if before and after:
        res.gpu_mb = after[0] - before[0]
    model = next((m for m in _loaded(config) if m["name"] == config.model_embed), {})
    res.on_gpu = f"{model.get('size_vram', 0) / max(model.get('size', 1), 1):.0%}" if model else "not loaded"

    res.query_ms = statistics.median(_embed(config, QUERY, options) for _ in range(runs)) * 1000
    chunk = (FILLER * (TARGET_TOKENS * CHARS_PER_TOKEN // len(FILLER) + 1))[:TARGET_TOKENS * CHARS_PER_TOKEN]
    batch = [f"{i}. {chunk}" for i in range(BATCH_SIZE)]          # numbered: no two inputs alike
    res.chunk_ms = statistics.median(_embed(config, batch, options) for _ in range(BATCH_RUNS)) * 1000 / BATCH_SIZE

    out: dict = {}
    writer = threading.Thread(target=_chat, args=(config, out))
    writer.start()
    time.sleep(LOAD_SETTLE_SECONDS)                                 # let the chat model start writing
    busy = [_embed(config, QUERY, options) for _ in range(BUSY_QUERIES)]
    writer.join()
    res.busy_query_ms = statistics.median(busy) * 1000
    res.busy_chat_tps = out.get("tps", 0.0)
    return res


def report(results: list[Result], alone_tps: float) -> str:
    rows = [
        ("Load time", lambda r: f"{r.load_s:.1f} s"),
        ("Share on the GPU (ollama ps)", lambda r: r.on_gpu),
        ("GPU memory it takes", lambda r: f"{r.gpu_mb} MB" if r.gpu_mb is not None else "no nvidia-smi"),
        ("One search query (median)", lambda r: f"{r.query_ms:.0f} ms"),
        (f"Per document chunk (~{TARGET_TOKENS} tokens, batches of {BATCH_SIZE})", lambda r: f"{r.chunk_ms:.0f} ms"),
        ("Search query while the chat model writes", lambda r: f"{r.busy_query_ms:.0f} ms"),
        (f"Chat speed meanwhile (alone: {alone_tps:.1f} tok/s)", lambda r: f"{r.busy_chat_tps:.1f} tok/s"),
    ]
    lines = ["| | " + " | ".join(r.name for r in results) + " |", "|---|" + "---|" * len(results)]
    lines += [f"| {label} | " + " | ".join(cell(r) for r in results) + " |" for label, cell in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=QUERY_RUNS, help="single-query timings per placement")
    args = parser.parse_args(argv)
    config = Config.from_env()
    print(f"chat model {config.llm_model} alone at {config.llm_num_ctx} …", flush=True)
    alone = chat_alone(config)
    results = []
    for name, options in PLACEMENTS.items():
        print(f"embedding model {config.model_embed} on the {name} …", flush=True)
        results.append(measure(config, name, options, args.runs))
    _unload(config, config.model_embed)
    print()
    print(report(results, alone))
    print("\nThe embedding model is unloaded; the app's next search loads it on the CPU again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
