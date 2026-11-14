# Hardware and resources — what uses what, and the limits

What this machine has, what each part of MulteAgent uses of it, what each chat-model context size costs
(measured), and the limits that follow. Measured on 2026-11-13. Problem history: [ISSUES_LOG.md](ISSUES_LOG.md)
O58. Re-measure after any change of model, driver or Ollama version (see [How to measure](#how-to-measure)).

## 1. The machine

| Resource | What it is | Free in normal use |
|---|---|---|
| **GPU** | NVIDIA Quadro P4000: **8 GB**, Pascal (2017), 105 W limit, PCIe 3.0 ×16. Also drives the desktop (GNOME, Chrome) | ~1 GB with the chat model and the embedding model loaded |
| **CPU** | Intel Xeon Gold 6138: **20 cores / 40 threads**, 2.0–3.7 GHz, AVX-512 | ~20 threads while the chat model writes; nearly all of it otherwise |
| **RAM** | **45 GB** (+ 8 GB swap) | ~23 GB |
| **Disk** | **468 GB** NVMe | ~304 GB |

**The GPU is the bottleneck.** Every model call goes through the one 8 GB card, one request at a time
(`LLM_SLOTS=1`). CPU, RAM and disk have plenty to spare.

## 2. What each part uses

| Part | GPU | CPU | RAM | Disk |
|---|---|---|---|---|
| Chat model `qwen3.5:9b` (Q4_K_M, through Ollama) | 5.7–5.9 GB (table in section 3) | ~8 cores average while it works, ~17 at peak; ~0 when idle | ~1.4 GB | 6.6 GB |
| Embedding model `qwen3-embedding:0.6b` (knowledge base, example router) | none: the app runs it on the CPU (`CPU_ONLY` in `src/brain/embed.py`); on the GPU it would take ~207 MB (section 3b) | a few cores while it embeds | small | 0.6 GB |
| Image service, SDXL (`src/images/image_service.py`) | the whole card while it runs: it unloads the chat model first (`IMAGE_UNLOAD_OLLAMA`) and itself after 10 idle minutes (`IMAGE_IDLE_UNLOAD_MINUTES`) | low | a few GB while loaded | checkpoint not measured |
| API (FastAPI, `web/`) | — | low | < 300 MB | — |
| UI (Next.js) | — | low | ~0.9 GB | 1.5 GB (`node_modules` + build) |
| Postgres / SearXNG / pgAdmin / Portainer (Docker) | — | ~0 | 0.2 / 0.3 / 0.3 / 0.1 GB | 2 GB images + 0.6 GB data |
| Python packages (`.venv`, mostly torch for images) | — | — | — | 5.9 GB |

The chat model is kept loaded for 30 minutes after its last call (`LLM_KEEP_ALIVE=30m`).

## 3. Context size: measured

`python -m src.llm.bench_resources --ctx 8192,12288,16384`, 2026-11-13: each size loaded straight through
Ollama, a ~6,000-token prompt and a 200-token answer, then one embedding call.

| Context (`LLM_NUM_CTX`) | 8192 | **12288** | 16384 |
|---|---|---|---|
| Chat model size loaded | 5.74 GB | 5.88 GB | 6.69 GB |
| … on the GPU | 5.74 GB | 5.88 GB | 5.89 GB |
| … on the CPU | 0% | 0% | **12%** |
| Load time | 3.4 s | 6.5 s | 4.7 s |
| GPU memory peak (of 8192 MB; the embedding call was on the GPU, not where the app runs it) | 7124 MB | 7257 MB | 7269 MB |
| GPU busy, average | 95% | 95% | 89% |
| GPU power, average | 97 W | 96 W | 93 W |
| Model server CPU, average (% of one core) | 843 | 838 | 1271 |
| Model server CPU, peak | 1708 | 1684 | 2004 |
| Model server RAM, peak | 1.4 GB | 1.4 GB | 1.5 GB |
| Speed, embedding placement | not recorded (the rest of the table was not pasted) | | |

**Current setting: 12288** (the user's `.env`, 2026-11-13). Chat history may use 35% of it
(`CONTEXT_HISTORY_SHARE`): ~4,300 tokens, about 5 exchanges before older ones are summarised (it was ~2,870 at
8192, about 3).

What the numbers say:

- **12288 costs 140 MB more than 8192 and stays fully on the GPU.** 16384 costs another 810 MB and does not
  fit: 12% of the model moves to the CPU, which then works every token (~13 cores average, ~20 at peak), and
  the GPU waits on it (89% busy instead of 95%).
- **Ollama puts at most ~5.9 GB of this model on the card**, even with the embedding model unloaded (the
  benchmark unloads both before each size). The rest of the 8 GB is kept for its working buffers and the
  desktop. 12288 (5.88 GB) is right at that ceiling.
- **The CPU works even when the whole model is on the GPU**: ~8 cores average at 8192. Whether that is
  llama.cpp threads waiting on the GPU or parts of this hybrid model (24 of its 32 layers are not classic
  attention layers) computed on the CPU is not known.

### Why the first estimate was wrong

The first estimate (2026-11-13) said 16384 needed only ~256 MB more. It counted only the memory that holds the
context (the KV cache: 8 of the 32 layers keep one, ~32 KB per token). The working buffers the GPU uses while
it computes grow with the context too; measured, 16384 cost ~950 MB more than 8192, not 256 MB.

### What happened at 16384 in use

With `LLM_NUM_CTX=16384` (2026-11-13), `ollama ps` showed the chat model at 6.7 GB with 16% on the CPU and the
card 7 of 8 GB full. The model server ran on ~9 CPU cores, the start-up warm call timed out after 60 s, and
the embedding model no longer fitted beside the chat model: a test that needed an embedding waited 14 minutes.

### 3b. Embedding model: CPU or GPU (measured)

`python -m src.llm.bench_embed`, 2026-11-14, chat model loaded at 12288:

| | CPU (the app) | GPU |
|---|---|---|
| Load time | 1.7 s | 7.2 s |
| Share on the GPU (`ollama ps`) | 0% | 10% |
| GPU memory it takes | 0 MB | 207 MB |
| One search query (median) | 78 ms | 69 ms |
| Per document chunk (~450 tokens, batches of 16) | 727 ms | 219 ms |
| Search query while the chat model writes | 118 ms | 276 ms |
| Chat speed meanwhile (alone: 21.2 tok/s) | 20.9 tok/s | 20.6 tok/s |

- **The CPU is the right place, and the app already uses it** (it always has, since the knowledge base was built).
  A search query costs the same (~80 ms), is faster than the GPU while a chat answer is being written, and the
  chat model's speed does not change.
- **Adding documents is ~3× slower on the CPU**: ~0.7 s per chunk, so a 100-chunk document takes ~73 s instead of
  ~22 s. It runs in the background and never holds the GPU.
- **Loading it on the GPU made Ollama reload the chat model**, and the chat model came back 12% on the CPU (next
  section). Earlier figures that counted ~1.4 GB for the embedding model measured it on the GPU, where the app
  never runs it.

### 3c. Flash attention + q8_0 context cache (measured)

`python -m src.llm.bench_resources --ctx 12288,16384`, 2026-11-14, the user's runs: Ollama defaults, then
`OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` (systemd override), then defaults again after
`systemctl revert` (the third run matched the first).

| | Defaults 12288 | **FA + q8_0 12288** | Defaults 16384 | **FA + q8_0 16384** |
|---|---|---|---|---|
| Model size loaded | 5.88 GB | 5.71 GB | 6.02 GB | 5.80 GB |
| … on the CPU | 0% | 0% | 0% | 0% |
| GPU memory peak (of 8192 MB) | 7143–7193 MB | 6975 MB | 7275–7312 MB | 7061 MB |
| Reading the prompt | 431–432 tok/s | 434 tok/s | 423–427 tok/s | 428 tok/s |
| Writing the answer | 23.3–23.5 tok/s | 23.1 tok/s | 23.4 tok/s | 23.0 tok/s |
| Model server CPU peak (% of one core) | 1694–1698 | 1540 | 1692–1712 | 1548 |

- **It works on this Pascal card** and saves **~170 MB at 12288, ~220–250 MB at 16384**, at the same speed
  (writing ~1% slower, within noise). The saving is small because only 8 of the model's 32 layers keep a context
  cache.
- **16384 fitted fully on the GPU in all three runs today**, also without the change (6.02 GB, 0% CPU). On
  2026-11-13 it loaded at 6.69 GB with 12% on the CPU. How much the desktop holds on the card when the model
  loads decides it (section 4); the ~220 MB that FA + q8_0 frees is margin against that.
- The eval with FA + q8_0 on: **35/38, median 3.8 s** (was 36/38, 5.2 s). The prompt change of the same day
  (the date goes last, O52) was also in, so a miss cannot be put down to either one; a rerun with the defaults
  tells them apart. The user reverted to the defaults after the run.

### 3d. A small model on the CPU for suggestions, quiz and summaries (measured)

`python -m src.llm.bench_small_models`, 2026-11-14: the app's own suggestion, quiz and summary code, 2 runs each;
all outputs in `logs/bench_small_models.md`. The quiz column is the answers read by hand, not only the format.

| Model | Suggestions | Quiz | Summary | Chat speed meanwhile (alone ~23.5 tok/s) |
|---|---|---|---|---|
| **qwen3.5:9b, GPU (now)** | 1.6–1.8 s, good | 8.0–8.6 s, all answers right | 10 s, all 5 facts | — |
| qwen3:1.7b, CPU | 1.2–1.3 s, gives 1 of 3 | 14 s, **wrong answers marked right** | 11–12 s, drops a fact | 20.6 tok/s (−12%) |
| qwen3.5:0.8b, CPU | 3.2–3.3 s, good | 9–17 s, **no valid JSON** | 14–15 s, facts kept but a **contradiction** ("fits fully on the GPU but spills 12%") | 22.1 tok/s (−8%) |
| qwen2.5:1.5b, CPU | 2.0–2.6 s, good, drifts off the answer | 9 s, **wrong answers**, a repeated option | 5–11 s, drops the headings | 21.7 tok/s (−9%) |

- **Keep the 9B for all three.** On the GPU it is as fast as or faster than every small model on the CPU, and
  only its quiz and summaries were right. A small model on the CPU also slows the chat answer by 8–12%: the
  chat model uses ~8 CPU cores itself.

## 4. Limits

- **One model call at a time.** Chat, routing, suggestions, quiz, chat summaries and embeddings all queue on the
  one GPU. Suggestions and the quiz run after an answer and delay the next question (ISSUES_LOG O52).
- **Context ceiling ~12288.** Above that the chat model spills to the CPU and every call slows down. 12288 sits
  at the edge: if the desktop or Chrome hold more GPU memory when the model loads, it can spill too. After a
  restart, `ollama ps` must show `100% GPU`. Seen 2026-11-14: with the desktop at ~680 MB (gnome-shell 346,
  Chrome 259, System Monitor 45, Nautilus 23) a reload at 12288 gave 6.5 GB, 12% on the CPU. Ollama decides the
  split when the model loads and keeps it until the next load: close what holds GPU memory, then
  `ollama stop qwen3.5:9b`; the next message loads it again.
- **Images and chat cannot run together.** SDXL needs the whole card, so the chat model is unloaded for an
  image and loads again afterwards (a few seconds).
- **No GPU-side tuning yet.** The Ollama service runs with its defaults: no `OLLAMA_*` settings, so no flash
  attention and a full-size (f16) context cache. Whether flash attention works on this Pascal card is not
  tested.
- **The CPU is mostly idle.** ~20 of 40 threads stay free while the chat model works, and 23 GB of RAM is
  unused.

## 5. Options to use the idle CPU and RAM

Not built; each is a decision for the user (BACKLOG L20 O58).

| Option | What changes | Cost |
|---|---|---|
| ~~Embedding model on the CPU~~ | Already so: the app has always run it on the CPU. Measured 2026-11-14 (section 3b): keep it there | Adding documents ~3× slower than on the GPU |
| ~~A small second model on the CPU~~ for suggestions, quiz and chat summaries | Measured (section 3d): not worth it. No faster than the 9B on the GPU, wrong quiz answers, the chat slows 8–12% | — |
| **Ollama tuning:** `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` (a systemd override on the Ollama service, `sudo`) | Measured (section 3c): works on Pascal, ~170–250 MB less GPU memory, same speed: margin against the desktop's share | Reverted 2026-11-14 until an eval with the defaults shows whether the 35/38 came from it; q8_0 may lower quality slightly |
| Not worth it: the 9B on the CPU, more chat slots on the GPU | — | Much slower / does not fit |

## How to measure

```
python -m src.llm.bench_resources                         # 8192, then 16384
python -m src.llm.bench_resources --ctx 8192,12288,16384
python -m src.llm.bench_embed                             # embedding model: CPU (the app) vs GPU
python -m src.llm.bench_small_models                      # small CPU models vs the 9B: suggestions, quiz, summary
```

Run it when nobody is chatting: the app's own calls use `LLM_NUM_CTX` and make Ollama reload the model in
between. It unloads both models at the end; `bench_embed` leaves the chat model loaded and unloads the
embedding model. Quick checks any time: `ollama ps` (size, CPU/GPU split, context),
`nvidia-smi` (GPU memory and load), `free -h`, `df -h`.

| Constant | Value | File |
|---|---|---|
| `PROMPT_TOKENS`, `OUTPUT_TOKENS` | 6000, 200 | `src/llm/bench_resources.py` |
| `SAMPLE_SECONDS` | 0.5 | 〃 |
| `LLM_NUM_CTX`, `CONTEXT_HISTORY_SHARE`, `LLM_SLOTS`, `LLM_KEEP_ALIVE` | 12288, 0.35, 1, 30m | `.env` |
