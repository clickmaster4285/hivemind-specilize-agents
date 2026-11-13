"""What the chat model costs at each context size (ISSUES_LOG O58): GPU memory, how much of the model sits on the
CPU, the model server's CPU and RAM while it works, its speed, and whether the embedding model still fits beside
it. Each size is loaded straight through Ollama, so .env and the app need no change between runs.

    python -m src.llm.bench_resources                       # 8192, then 16384
    python -m src.llm.bench_resources --ctx 8192,12288,16384
    python -m src.llm.bench_resources --prompt-tokens 3000  # a shorter prompt (it must fit the smallest size)

Run it when nobody is chatting: the app's own calls use LLM_NUM_CTX and would make Ollama reload the model in
between. Both models are unloaded at the end; the app's next call loads the chat model at its own size again.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass, field

import psutil
import requests

from src.core.config import Config

SAMPLE_SECONDS = 0.5        # how often GPU and CPU are read while the model works
PROMPT_TOKENS = 6000        # a long chat with sources fills ~6-7k: what the context is for
OUTPUT_TOKENS = 200         # long enough to measure generation speed, short enough to keep a run under a minute
CHARS_PER_TOKEN = 4         # rough English size of a token, to build a prompt of about PROMPT_TOKENS
REQUEST_TIMEOUT = 600       # seconds: a model spilling to the CPU at a large context is slow, not stuck
LOAD_SETTLE_SECONDS = 2     # after an unload, before the next reading: the GPU memory is given back with a delay
FILLER = ("The office network has a router, two switches, ten PCs and a camera recorder. Each site connects to "
          "the head office over a VPN tunnel, and the servers keep nightly backups for thirty days. ")


@dataclass
class Run:
    ctx: int
    load_s: float = 0.0
    size_gb: float = 0.0
    gpu_gb: float = 0.0
    prompt_tps: float = 0.0
    gen_tps: float = 0.0
    total_s: float = 0.0
    gpu_mem_mb: list[int] = field(default_factory=list)
    gpu_util: list[int] = field(default_factory=list)
    gpu_watts: list[float] = field(default_factory=list)
    cpu_pct: list[float] = field(default_factory=list)
    ram_gb: list[float] = field(default_factory=list)
    embed_s: float = 0.0
    embed_placement: str = ""
    chat_still_loaded: bool = False


def _gpu() -> tuple[int, int, float] | None:
    """(memory used MB, utilisation %, watts) of the first GPU, or None without nvidia-smi."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu,power.draw",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        mem, util, watts = (v.strip() for v in out.splitlines()[0].split(","))
        return int(mem), int(util), float(watts)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _model_servers() -> list[psutil.Process]:
    """Ollama's model runners (llama-server / ollama runner): where the model's CPU work and memory show up."""
    found = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        line = " ".join(proc.info.get("cmdline") or [])
        if "llama-server" in (proc.info.get("name") or "") or "llama-server" in line or " runner " in f" {line} ":
            found.append(proc)
    return found


def _sample(run: Run, stop: threading.Event) -> None:
    procs = _model_servers()
    for p in procs:
        p.cpu_percent(None)                       # the first reading only starts the count
    while not stop.wait(SAMPLE_SECONDS):
        if (gpu := _gpu()) is not None:
            run.gpu_mem_mb.append(gpu[0])
            run.gpu_util.append(gpu[1])
            run.gpu_watts.append(gpu[2])
        cpu = ram = 0.0
        for p in procs:
            try:
                cpu += p.cpu_percent(None)
                ram += p.memory_info().rss / 1e9
            except psutil.Error:
                continue
        run.cpu_pct.append(cpu)
        run.ram_gb.append(ram)


def _post(config: Config, path: str, body: dict) -> dict:
    resp = requests.post(f"{config.llm_base_url}{path}", json=body, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _loaded(config: Config) -> list[dict]:
    return requests.get(f"{config.llm_base_url}/api/ps", timeout=10).json().get("models") or []


def _unload(config: Config, model: str) -> None:
    try:
        _post(config, "/api/generate", {"model": model, "keep_alive": 0})
    except requests.RequestException:
        pass


def measure(config: Config, ctx: int, prompt_tokens: int, output_tokens: int) -> Run:
    run = Run(ctx)
    for model in (config.llm_model, config.model_embed):
        _unload(config, model)
    time.sleep(LOAD_SETTLE_SECONDS)
    think = {"think": config.llm_think} if config.llm_think is not None else {}

    t = time.perf_counter()
    _post(config, "/api/generate", {"model": config.llm_model, "prompt": "Hi", "stream": False, **think,
                                    "options": {"num_ctx": ctx, "num_predict": 1}})
    run.load_s = time.perf_counter() - t
    chat = next((m for m in _loaded(config) if m["name"] == config.llm_model), {})
    run.size_gb, run.gpu_gb = chat.get("size", 0) / 1e9, chat.get("size_vram", 0) / 1e9

    prompt = (FILLER * (prompt_tokens * CHARS_PER_TOKEN // len(FILLER) + 1))[:prompt_tokens * CHARS_PER_TOKEN]
    prompt += "\n\nIn three short paragraphs, describe this network and what could fail first."
    stop = threading.Event()
    sampler = threading.Thread(target=_sample, args=(run, stop), daemon=True)
    sampler.start()
    t = time.perf_counter()
    done = _post(config, "/api/generate", {"model": config.llm_model, "prompt": prompt, "stream": False, **think,
                                           "options": {"num_ctx": ctx, "num_predict": output_tokens,
                                                       "temperature": 0}})
    run.total_s = time.perf_counter() - t
    stop.set()
    sampler.join()
    run.prompt_tps = done.get("prompt_eval_count", 0) / max(done.get("prompt_eval_duration", 1) / 1e9, 1e-9)
    run.gen_tps = done.get("eval_count", 0) / max(done.get("eval_duration", 1) / 1e9, 1e-9)

    t = time.perf_counter()
    _post(config, "/api/embed", {"model": config.model_embed, "input": "how do I reset the camera recorder"})
    run.embed_s = time.perf_counter() - t
    models = {m["name"]: m for m in _loaded(config)}
    embed = models.get(config.model_embed, {})
    on_gpu = embed.get("size_vram", 0) / max(embed.get("size", 1), 1)
    run.embed_placement = f"{on_gpu:.0%} GPU" if embed else "not loaded"
    run.chat_still_loaded = config.llm_model in models
    return run


def _peak(values: list) -> str:
    return f"{max(values):.0f}" if values else "-"


def _avg(values: list) -> str:
    return f"{statistics.mean(values):.0f}" if values else "-"


def report(runs: list[Run], gpu_total_mb: int | None) -> str:
    rows = [
        ("Model size loaded (GB)", lambda r: f"{r.size_gb:.2f}"),
        ("  of it on the GPU (GB)", lambda r: f"{r.gpu_gb:.2f}"),
        ("  of it on the CPU", lambda r: f"{1 - r.gpu_gb / r.size_gb:.0%}" if r.size_gb else "-"),
        ("Load time (s)", lambda r: f"{r.load_s:.1f}"),
        (f"GPU memory peak (MB{f' of {gpu_total_mb}' if gpu_total_mb else ''})", lambda r: _peak(r.gpu_mem_mb)),
        ("GPU busy, average (%)", lambda r: _avg(r.gpu_util)),
        ("GPU power, average (W)", lambda r: _avg(r.gpu_watts)),
        ("Model server CPU, average (% of one core)", lambda r: _avg(r.cpu_pct)),
        ("Model server CPU, peak", lambda r: _peak(r.cpu_pct)),
        ("Model server RAM, peak (GB)", lambda r: f"{max(r.ram_gb):.1f}" if r.ram_gb else "-"),
        ("Reading the prompt (tokens/s)", lambda r: f"{r.prompt_tps:.0f}"),
        ("Writing the answer (tokens/s)", lambda r: f"{r.gen_tps:.1f}"),
        ("Whole request (s)", lambda r: f"{r.total_s:.1f}"),
        ("Embedding call (s)", lambda r: f"{r.embed_s:.1f}"),
        ("Embedding model placed", lambda r: r.embed_placement),
        ("Chat model still loaded after it", lambda r: "yes" if r.chat_still_loaded else "NO (pushed out)"),
    ]
    width = max(len(name) for name, _ in rows)
    lines = [f"{'Context':<{width}}  " + "  ".join(f"{r.ctx:>12}" for r in runs)]
    lines += [f"{name:<{width}}  " + "  ".join(f"{fn(r):>12}" for r in runs) for name, fn in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ctx", default="8192,16384", help="context sizes to compare, comma-separated")
    parser.add_argument("--prompt-tokens", type=int, default=PROMPT_TOKENS)
    parser.add_argument("--output-tokens", type=int, default=OUTPUT_TOKENS)
    args = parser.parse_args(argv)
    config = Config.from_env()
    sizes = [int(c) for c in args.ctx.split(",") if c.strip()]
    if args.prompt_tokens + args.output_tokens > min(sizes):
        parser.error(f"--prompt-tokens + --output-tokens must fit the smallest context ({min(sizes)})")
    gpu = _gpu()
    runs = []
    for ctx in sizes:
        print(f"measuring {config.llm_model} at {ctx} tokens of context …", flush=True)
        runs.append(measure(config, ctx, args.prompt_tokens, args.output_tokens))
    for model in (config.llm_model, config.model_embed):
        _unload(config, model)
    total = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True).stdout.strip() if gpu else ""
    print()
    print(report(runs, int(total) if total.isdigit() else None))
    print("\nBoth models are unloaded; the app's next call loads the chat model at LLM_NUM_CTX again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
