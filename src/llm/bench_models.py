"""Deep comparison of chat models on what actually differs between them — so choosing or routing a model
is a data decision, not a guess. Four dimensions:

  1. Diagrams, REALLY validated — each generated Mermaid block is parsed headless
     (frontend/scripts/validate_mermaid.mjs), as written AND after the app's sanitizer, so you get both
     "raw valid %" and "valid after safety net %", plus node count, emoji-on-nodes, classDef/clutter.
  2. Answer quality — a fixed judge model scores each prose answer 1-5 on correctness / structure /
     clarity / completeness (the dimension you'd risk losing by moving off the general model).
  3. Variance / consistency — several runs per prompt; reports per-prompt pass-rate (all vs any valid)
     and min/median/max latency, so a "good on average but flaky" model is exposed.
  4. Hinglish + instruction-adherence — English and Hinglish prompts; adherence to the diagram rules
     (<=7 nodes, emoji on nodes, no classDef, no IPs inside nodes).

    python -m src.llm.bench_models                                   # all installed candidates
    python -m src.llm.bench_models --models qwen3.5:9b,qwen2.5-coder:7b --runs 3
    python -m src.llm.bench_models --judge-model qwen3.5:9b --out doc/model_bench.md
    python -m src.llm.bench_models --dry-run                         # print the plan, call nothing

This is a long LLM eval — run it yourself; Claude runs only pytest. Prose answers here are un-grounded
(no tools), so judge scores are a RELATIVE signal between models, not the app's grounded quality. After a
run, `ollama ps` shows GPU/CPU placement (the 14B spills to CPU on an 8 GB card)."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.core.clock import current_time_text
from src.core.config import Config
from src.llm import assistant
from src.llm.llm_client import LLMClient
from src.tools.answer_options import generate_options
from src.tools.quiz import generate_quiz
from src.tools.suggestions import generate_suggestions

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
VALIDATOR = FRONTEND / "scripts" / "validate_mermaid.mjs"

DEFAULT_MODELS = ["qwen3.5:9b", "qwen2.5-coder:7b", "qwen2.5-coder:14b"]

# (id, lang, prompt). Each should yield ONE small, valid, emoji-labelled Mermaid diagram.
DIAGRAM_PROMPTS = [
    ("office-net", "en", "Explain how an office server connects to the PCs and how data flows between "
                         "them, with a diagram."),
    ("web-arch", "en", "Show the architecture of a simple web app with nginx, an API service and a "
                       "Postgres database, with a diagram."),
    ("boot-flow", "en", "Explain the PC boot sequence from power-on to the desktop, with a diagram."),
    ("vlan-seg", "en", "Draw how three VLANs on a switch connect through a router/firewall to the internet."),
    ("hinglish-net", "hi", "Office ka server aur PCs kaise connect hote hain aur data kaise flow karta "
                           "hai, ek diagram ke saath samjhao."),
]

# (id, lang, question) for the judged prose quality dimension.
QUALITY_PROMPTS = [
    ("tcp-udp", "en", "Explain the difference between TCP and UDP, and when to use each."),
    ("dns", "en", "How does DNS resolve a domain name? Explain it step by step."),
    ("raid", "hi", "RAID 5 aur RAID 10 me kya difference hai? Simple words me batao."),
    ("slow-pc", "hi", "Ek Windows computer bahut slow ho raha hai — troubleshoot kaise karu?"),
]

# (question, answer) pairs to exercise the JSON generators (options / quiz / suggestions).
QA_PAIRS = [
    ("What are the main parts of a motherboard?",
     "A motherboard has a CPU socket, chipset, RAM (DIMM) slots, PCIe expansion slots, SATA and M.2 "
     "storage connectors, power connectors, a BIOS/UEFI chip, onboard audio/network, and a rear I/O panel."),
    ("How do I set up a small office network?",
     "Connect each PC and the server to a network switch over Ethernet, uplink the switch to a "
     "router/firewall, assign private IPs in 192.168.1.0/24, and share the internet through the router."),
]

CLARIFY_PROMPTS = ["I want to buy a laptop for work.", "Recommend a backup strategy for my files."]

JUDGE_SYSTEM = (
    "You are a strict technical evaluator. Score the ASSISTANT ANSWER to the USER QUESTION on four axes, "
    "each an integer 1-5 (5 = excellent): correctness (facts right), structure (headings/steps/tables "
    "used well), clarity (easy to follow), completeness (covers the key points). If the question is in "
    "Hinglish, a good answer replies in Hinglish. Reply with ONLY a JSON object: "
    '{"correctness":n,"structure":n,"clarity":n,"completeness":n} — no other text.'
)

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U00002190-\U000021FF"
    "\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F]"
)
IPV4_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


def _mermaid_block(text: str) -> str | None:
    m = re.search(r"```mermaid\s*(.*?)```", text, re.S | re.I)
    return m.group(1).strip() if m else None


def _node_labels(code: str) -> list[str]:
    out = []
    for raw in re.findall(r'[\[({]+\s*("?)([^"\]\)}|]+?)\1\s*[\])}]+', code):
        label = raw[1].strip()
        if label and not label.startswith("-") and ":" not in label[:3]:
            out.append(label)
    return out


@dataclass
class DiagramScore:
    pid: str = ""
    gid: str = ""   # global id ("model|pid|run") used to match the headless validator's results
    lang: str = "en"
    has_block: bool = False
    nodes: int = 0
    emoji_pct: float = 0.0
    edge_breakers: int = 0
    classdef: bool = False
    clutter: bool = False
    ip_in_node: bool = False
    secs: float = 0.0
    raw_valid: bool | None = None  # filled in by the headless validator
    san_valid: bool | None = None


def score_diagram(pid: str, lang: str, text: str, secs: float) -> tuple[DiagramScore, str | None]:
    code = _mermaid_block(text)
    if not code:
        return DiagramScore(pid=pid, lang=lang, secs=secs), None
    labels = _node_labels(code)
    nodes = len(labels)
    emoji_nodes = sum(1 for lbl in labels if EMOJI_RE.search(lbl[:3]))
    score = DiagramScore(
        pid=pid, lang=lang, has_block=True, nodes=nodes,
        emoji_pct=(emoji_nodes / nodes * 100) if nodes else 0.0,
        edge_breakers=len(re.findall(r"\|[^|]*[()\[\]{}][^|]*\|", code)),
        classdef="classdef" in code.lower(),
        clutter=bool(re.search(r"\bsubgraph\b", code, re.I)) or "legend" in code.lower(),
        ip_in_node=any(IPV4_RE.search(lbl) for lbl in labels),
        secs=secs,
    )
    return score, code


@dataclass
class QualityScore:
    pid: str = ""
    lang: str = "en"
    axes: dict[str, int] = field(default_factory=dict)  # correctness/structure/clarity/completeness
    secs: float = 0.0
    judged: bool = False


@dataclass
class ModelResult:
    model: str
    diagram: list[DiagramScore] = field(default_factory=list)
    diagram_samples: dict[str, str] = field(default_factory=dict)   # pid -> first raw block
    quality: list[QualityScore] = field(default_factory=list)
    struct_success: dict[str, list[bool]] = field(default_factory=dict)
    struct_secs: list[float] = field(default_factory=list)
    processor: str = "?"


def _ollama_processor(model: str) -> str:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=15).stdout
    except Exception:  # noqa: BLE001
        return "?"
    for line in out.splitlines():
        if model in line:
            m = re.search(r"(\d+%(?:/\d+%)?\s+(?:CPU|GPU)(?:/(?:CPU|GPU))?)", line)
            return m.group(1) if m else "loaded"
    return "not loaded"


def validate_diagrams(blocks: list[tuple[str, str]]) -> dict[str, tuple[bool, bool]]:
    """blocks = [(id, code)] -> {id: (raw_valid, san_valid)}. Empty if the validator can't run."""
    if not blocks or not VALIDATOR.exists():
        return {}
    payload = [{"id": bid, "code": code} for bid, code in blocks]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh)
        tmp = fh.name
    try:
        proc = subprocess.run(["node", str(VALIDATOR), tmp], cwd=str(FRONTEND),
                              capture_output=True, text=True, timeout=180)
    except Exception as exc:  # noqa: BLE001
        print(f"  [validator unavailable: {exc}] falling back to heuristic edge-breakers only")
        return {}
    finally:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
    if proc.returncode != 0:
        print(f"  [validator error] {proc.stderr.strip().splitlines()[-1] if proc.stderr else proc.returncode}")
        return {}
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("  [validator gave no JSON] skipping render-validation")
        return {}
    return {r["id"]: (bool(r["rawValid"]), bool(r["sanValid"])) for r in rows}


def judge_answer(question: str, answer: str, judge: LLMClient, judge_model: str) -> dict[str, int]:
    if not answer.strip():
        return {}
    try:
        raw = judge.chat(system=JUDGE_SYSTEM, user=f"USER QUESTION:\n{question}\n\nASSISTANT ANSWER:\n"
                         f"{answer[:3000]}", model=judge_model, temperature=0.0, max_retries=0,
                         max_tokens=120)
    except Exception as exc:  # noqa: BLE001
        print(f"    [judge failed: {exc}]")
        return {}
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for k in ("correctness", "structure", "clarity", "completeness"):
        v = data.get(k)
        if isinstance(v, (int, float)) and 1 <= v <= 5:
            out[k] = int(v)
    return out


def bench_model(model: str, config: Config, runs: int, judge_model: str) -> tuple[ModelResult, list]:
    res = ModelResult(model=model)
    client = LLMClient(config, model=model)
    judge = LLMClient(config, model=judge_model)
    identity = f"You are MulteAgent, a local IT assistant (model: {model})."
    system = assistant.SYSTEM.format(identity=identity, now=current_time_text())
    blocks: list[tuple[str, str]] = []  # (global id, code) for the headless validator

    # 1) Diagrams
    for pid, lang, prompt in DIAGRAM_PROMPTS:
        for r in range(runs):
            t0 = time.monotonic()
            try:
                text = client.chat(system=system, user=prompt, temperature=0.2, max_retries=0)
            except Exception as exc:  # noqa: BLE001
                print(f"    [{model}] diagram {pid} run {r + 1} failed: {exc}")
                res.diagram.append(DiagramScore(pid=pid, lang=lang, secs=time.monotonic() - t0))
                continue
            score, code = score_diagram(pid, lang, text, time.monotonic() - t0)
            if code:
                score.gid = f"{model}|{pid}|{r}"
                blocks.append((score.gid, code))
                res.diagram_samples.setdefault(pid, code)
            res.diagram.append(score)
            print(f"    [{model}] diagram {pid} r{r + 1}: {'block' if code else 'NO block'} ({score.secs:.0f}s)")

    # 2) Quality (judged prose)
    for pid, lang, question in QUALITY_PROMPTS:
        for r in range(runs):
            t0 = time.monotonic()
            try:
                answer = client.chat(system=system, user=question, temperature=0.3, max_retries=0)
            except Exception as exc:  # noqa: BLE001
                print(f"    [{model}] quality {pid} run {r + 1} failed: {exc}")
                res.quality.append(QualityScore(pid=pid, lang=lang, secs=time.monotonic() - t0))
                continue
            axes = judge_answer(question, answer, judge, judge_model)
            res.quality.append(QualityScore(pid=pid, lang=lang, axes=axes,
                                            secs=time.monotonic() - t0, judged=bool(axes)))
            print(f"    [{model}] quality {pid} r{r + 1}: {axes or 'judge-miss'}")

    # 3) Structured JSON reliability
    for path in ("options", "quiz", "suggestions", "clarify"):
        res.struct_success[path] = []
    for q, a in QA_PAIRS:
        for _ in range(runs):
            t0 = time.monotonic()
            res.struct_success["options"].append(bool(generate_options(q, client_chat=client.chat, model=model)))
            res.struct_success["quiz"].append(bool(generate_quiz(q, a, client_chat=client.chat, model=model)))
            res.struct_success["suggestions"].append(
                bool(generate_suggestions(q, a, client_chat=client.chat, model=model)))
            res.struct_secs.append(time.monotonic() - t0)
    for q in CLARIFY_PROMPTS:
        for _ in range(runs):
            try:
                res.struct_success["clarify"].append(bool(assistant._ask_clarifying(q, config, model)))
            except Exception:  # noqa: BLE001
                res.struct_success["clarify"].append(False)

    res.processor = _ollama_processor(model)
    return res, blocks


def _avg(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def _pct(flags: list[bool]) -> float:
    return (sum(flags) / len(flags) * 100) if flags else 0.0


def _attach_validation(res: ModelResult, val: dict[str, tuple[bool, bool]]) -> None:
    for score in res.diagram:
        if score.gid and score.gid in val:
            score.raw_valid, score.san_valid = val[score.gid]


def report(results: list[ModelResult], runs: int, judge_model: str) -> str:
    lines: list[str] = []
    w = lines.append
    w(f"# Model comparison  (runs/prompt = {runs}, judge = `{judge_model}`)\n")

    w("## 1. Diagrams — real render validation + quality\n")
    w("| Model | block% | raw-valid% | valid-after-sanitize% | avg nodes | emoji% | classDef% | clutter% | avg s | GPU/CPU |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        d = r.diagram
        got = [x for x in d if x.has_block]
        raw = [x.raw_valid for x in got if x.raw_valid is not None]
        san = [x.san_valid for x in got if x.san_valid is not None]
        w(f"| `{r.model}` | {_pct([x.has_block for x in d]):.0f} | "
          f"{_pct(raw):.0f} | {_pct(san):.0f} | {_avg([x.nodes for x in got]):.1f} | "
          f"{_avg([x.emoji_pct for x in got]):.0f} | {_pct([x.classdef for x in got]):.0f} | "
          f"{_pct([x.clutter for x in got]):.0f} | {_avg([x.secs for x in d]):.1f} | {r.processor} |")
    w("")

    w("## 2. Answer quality — judge 1-5 (overall + by language)\n")
    w("| Model | correct | structure | clarity | complete | overall | EN overall | HI overall | judged% |")
    w("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        qs = [q for q in r.quality if q.judged]
        def axis(name, items=qs):
            return _avg([q.axes[name] for q in items if name in q.axes])
        overall = _avg([v for q in qs for v in q.axes.values()])
        en = _avg([v for q in qs if q.lang == "en" for v in q.axes.values()])
        hi = _avg([v for q in qs if q.lang == "hi" for v in q.axes.values()])
        w(f"| `{r.model}` | {axis('correctness'):.1f} | {axis('structure'):.1f} | {axis('clarity'):.1f} | "
          f"{axis('completeness'):.1f} | {overall:.2f} | {en:.2f} | {hi:.2f} | "
          f"{_pct([q.judged for q in r.quality]):.0f} |")
    w("")

    w("## 3. Consistency — per-diagram-prompt render pass-rate (valid-after-sanitize)\n")
    pids = [p[0] for p in DIAGRAM_PROMPTS]
    w("| Model | " + " | ".join(pids) + " | all-pass% |")
    w("|---|" + "|".join(["---"] * len(pids)) + "|---|")
    for r in results:
        cells = []
        per_prompt_allpass = []
        for pid in pids:
            runs_v = [x.san_valid for x in r.diagram if x.pid == pid and x.san_valid is not None]
            if not runs_v:
                cells.append("–")
                continue
            cells.append(f"{sum(runs_v)}/{len(runs_v)}")
            per_prompt_allpass.append(all(runs_v))
        w(f"| `{r.model}` | " + " | ".join(cells) + f" | {_pct(per_prompt_allpass):.0f} |")
    w("")

    w("## 4. Structured JSON success rate + latency spread\n")
    w("| Model | options | quiz | suggestions | clarify | struct avg s | diagram min/med/max s |")
    w("|---|---|---|---|---|---|---|")
    for r in results:
        s = r.struct_success
        dsec = sorted(x.secs for x in r.diagram if x.secs)
        spread = f"{dsec[0]:.0f}/{statistics.median(dsec):.0f}/{dsec[-1]:.0f}" if dsec else "–"
        w(f"| `{r.model}` | {_pct(s.get('options', [])):.0f}% | {_pct(s.get('quiz', [])):.0f}% | "
          f"{_pct(s.get('suggestions', [])):.0f}% | {_pct(s.get('clarify', [])):.0f}% | "
          f"{_avg(r.struct_secs):.1f} | {spread} |")
    w("")

    w("## 5. Instruction-adherence on diagrams (higher = follows the rules)\n")
    w("| Model | <=7 nodes% | emoji-on-nodes% | no-classDef% | no-IP-in-node% |")
    w("|---|---|---|---|---|")
    for r in results:
        got = [x for x in r.diagram if x.has_block]
        w(f"| `{r.model}` | {_pct([x.nodes <= 7 for x in got]):.0f} | {_avg([x.emoji_pct for x in got]):.0f} | "
          f"{_pct([not x.classdef for x in got]):.0f} | {_pct([not x.ip_in_node for x in got]):.0f} |")
    w("")

    w("## Diagram samples (first block per prompt — paste into mermaid.live to eyeball)\n")
    for r in results:
        w(f"### `{r.model}`\n")
        if not r.diagram_samples:
            w("_no mermaid block produced_\n")
        for pid, block in r.diagram_samples.items():
            w(f"**{pid}**\n")
            w("```mermaid")
            w(block)
            w("```\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", help="comma-separated model tags (default: the installed candidates)")
    parser.add_argument("--runs", type=int, default=2, help="repeats per prompt (default 2)")
    parser.add_argument("--judge-model", default="qwen3.5:9b", help="fixed judge model for quality scores")
    parser.add_argument("--out", help="write the full markdown report (incl. samples) to this path")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit without calling any model")
    args = parser.parse_args(argv)

    models = [m.strip() for m in args.models.split(",")] if args.models else DEFAULT_MODELS
    config = Config.from_env()

    print(f"Models: {', '.join(models)}  |  judge: {args.judge_model}")
    print(f"Runs/prompt: {args.runs}  |  diagrams: {len(DIAGRAM_PROMPTS)}  |  quality: {len(QUALITY_PROMPTS)}  "
          f"|  QA: {len(QA_PAIRS)}  |  clarify: {len(CLARIFY_PROMPTS)}")
    print(f"Validator: {'found' if VALIDATOR.exists() else 'MISSING (heuristics only)'} @ {VALIDATOR}")
    if args.dry_run:
        print("\n[dry-run] no models called.")
        return 0

    results: list[ModelResult] = []
    for model in models:
        print(f"\n=== {model} ===")
        res, blocks = bench_model(model, config, args.runs, args.judge_model)
        print(f"  validating {len(blocks)} diagram(s) headless…")
        _attach_validation(res, validate_diagrams(blocks))
        results.append(res)

    md = report(results, args.runs, args.judge_model)
    print("\n" + md)
    if args.out:
        Path(args.out).write_text(md + "\n", encoding="utf-8")
        print(f"\nSaved report to {args.out}")
    print("\nTip: `ollama ps` for live GPU/CPU placement; "
          "`LLM_MODEL=<model> python -m src.llm.assistant --eval` for tool-selection quality.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
