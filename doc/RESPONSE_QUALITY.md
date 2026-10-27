# MulteAgent — Response Quality & Speed

Make answers **faster** and, on demand, **deeper** — without leaving the local box.
Scope: response speed · deeper query understanding · research · deep thinking · multiple-answer
options · follow-up suggestions · feedback.

Related: [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) (current pipeline) · [ISSUES_LOG.md](ISSUES_LOG.md)
(all bugs) · [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md) (speed tiers / cold start) ·
[BACKLOG.md](BACKLOG.md) · [CHAT_HISTORY_DECISION.md](CHAT_HISTORY_DECISION.md).

This is a **living doc**: as we build we record every issue (Symptom → Cause → Fix · *Lesson*),
every decision (what / why), and the state of each phase. Newest log entries go at the bottom of §5.
Detailed speed line-items may also appear in IMPROVEMENT_ROADMAP.md; this doc is the source of truth
for the Fast/Deep program and keeps the roadmap in sync.

---

## 1. Decisions (locked 2026-10-27)

| # | Decision | Why |
|---|----------|-----|
| D1 | **Strictly local.** Everything runs on local Ollama; no cloud models. | Privacy / the project's no-cloud stance. Trade-off: depth is capped by local compute, so heavy work must be bounded and opt-in. |
| D2 | **Two lanes.** *Fast* (default send) and *Deep* (opt-in). | Depth costs time; most prompts don't need it. Let the user spend effort only when they want it. |
| D3 | **Depth is user-triggered**, never automatic. **Deep** and **Options** are visible toggles **next to the Send button** (like the existing Web toggle). | User chose predictability over the system guessing. Toggles next to Send are discoverable (vs buried in the "+" menu). |
| D4 | **"Options" = multiple candidate answers.** Implemented as **approach cards → expand**: 2–3 short one-line approaches first, user picks one, then the full answer is written. | Generating 2–3 *full* answers locally ≈ 2–3× the time. Short cards are cheap and arguably better UX than three walls of text. |
| D5 | **Order: hard first.** P0 instrument → P1 speed → P3 deep mode → P4 options → P2 suggestions + feedback. | Tackle the hard speed/depth work while focus is high; the cheap, independent polish (suggestions, feedback) lands last. |

---

## 2. Current pipeline (as-is)

Full detail in [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md). In short: a prompt becomes a **job**
(`src/jobs/jobs.py`) → the **worker** (`src/jobs/worker.py`) claims it → `assistant.decide()`
(`src/llm/assistant.py`) makes **one Ollama call** that either streams a direct answer or picks a
tool; web/brain tools add fetch + read + synth; events stream to the UI and render as the live steps
(`frontend/src/components/LiveStatus.tsx`).

Facts from the code that shape this work:

- **The chat model is deliberately unloaded, not kept warm.** `src/llm/ollama_mgmt.py` only ever sends
  `keep_alive=0` (to *unload*), and IMPROVEMENT_ROADMAP Tier 1 ("unload Ollama") does this **on
  purpose** — to free VRAM for SDXL image generation. **So "keep the chat model warm" is not a free
  win; it trades image-gen readiness for chat latency.** The real lever is a *smarter keep_alive
  policy* (warm while actively chatting, yield to SDXL when an image job runs), not "always warm".
- `num_ctx` defaults to **8192** (`LLM_NUM_CTX`); prompt size directly affects first-token time.
- **Model roles already exist** — `fast` / `strong` / `cloud` via `resolve_model_plan`
  (`src/llm/model_registry.py`). We can route cheap work to `fast`. (Caveat: ISSUES_LOG #3 — running a
  *second* small model alongside the 9b evicted it and made the next reply slow; measure before splitting.)
- `src/llm/llm_client.py` sets Ollama `options` (`temperature`, `num_ctx`, `num_predict`) per call.
- `LLM_THINK=false` is already set (Qwen's hidden reasoning was a 65 s → 0.9 s fix, ISSUES_LOG #2);
  deep-thinking mode will re-enable thinking **only** on the Deep lane.

---

## 3. Plan (phases, in build order)

### P0 — Instrument speed  *(status: in progress)*
- **What:** time the pipeline (queue wait → model load / first token → tool time → synth) and surface
  it, so speed work is data-driven, not guessed.
- **How:** emit timings on the existing event stream / logs from `chat_runner` / `jobs`.
- **Why:** we can't yet say where the seconds go. Measure before optimizing — especially the
  cold-load cost caused by the deliberate Ollama unload (§2).
- **Done so far:** `src/jobs/chat_runner.py` now records **time-to-first-token** and whether the model
  was **cold/warm** at the start, emits them on `agent_done`/`session_done`, and logs one line per job:
  `timing job=… role=… warm=… ttft=…s total=…s`. The existing `elapsed_seconds`/`total_seconds` stay.
- **Still open:** tool-phase split (search vs synth), queue-wait (job `created_at`→`started_at`), the
  agentic path (`src/agentic/`), and surfacing TTFT/warm in the UI.

### P1 — Speed quick wins  *(status: in progress)*
- **Smarter `keep_alive` policy — done.** Chat calls now send an explicit `keep_alive`
  (`LLM_KEEP_ALIVE`, default `30m`) so the model stays warm between messages instead of Ollama's
  ~5-min default, and the API **warms the chat model on startup** (`LLM_WARM_ON_START`, default on,
  `src/llm/ollama_mgmt.py:warm_model` on a daemon thread). Image jobs still unload it
  (`IMAGE_UNLOAD_OLLAMA`) to yield VRAM to SDXL, so the policy *warms while chatting, yields for images*.
  Validate with P0: the first message after startup should now log `warm=true`.
- *Still open:* route tool-choice / simple answers on the **`fast`** model and escalate to `strong`
  only when needed (watch the ISSUES_LOG #3 eviction trap); **trim the prompt** (cap conversation
  history, slim the system prompt); **cache** embeddings / recent queries / web results.

### P3 — Deep mode  *(status: not started)*  — behind the **Deep** toggle
- Pipeline: **understand** (intent, entities, what's missing) → **plan** → **research**
  (budgeted iterative SearXNG: ≤2 rounds, ≤5 pages) → **deep thinking** (re-enable Qwen thinking)
  → **synthesize** with citations. Each step streams to `LiveStatus`.
- Bounded hard, because strictly local.

### P4 — Options  *(status: not started)*  — behind the **Options** toggle
- Approach cards → expand (see D4). Lives in `Composer` / `MessageFeed`.

### P2 — Suggestions + feedback  *(status: not started, independent)*
- **Suggestions:** 3 clickable follow-up chips after each answer (`MessageExtras`).
- **Feedback:** 👍/👎 + optional note per answer. New `feedback` table
  (`job_id, user_id, rating, note, created_at`, via `create_all`). Phase 1 capture + show;
  Phase 2 (later) use it (e.g. re-rank/avoid down-voted sources).

---

## 4. Query-understanding placement

- **Fast lane:** fold intent + entities + "what's really asked" into the *existing* `decide()` call
  (no extra round-trip → no added latency).
- **Deep lane:** a dedicated, visible "Understanding…" step before planning.

---

## 5. Log — issues, solutions, decisions

Format: **Symptom** → **Cause** → **Fix** · *Lesson*. Newest at the bottom.

### 2026-10-27 — initiative opened
- Scope, constraints (D1–D5) and phase order agreed. No code yet; P0 is next.
- **Tension found while planning:** the chat model is unloaded on purpose to give VRAM to SDXL
  (IMPROVEMENT_ROADMAP Tier 1), so "keep warm" fights image-gen readiness. P1 must make `keep_alive`
  a *policy* (warm while chatting, yield for image jobs), not a blanket setting.
- *Lesson (carried in):* measure the chat model's cold-load cost first (P0) before changing the
  unload policy, and beware the ISSUES_LOG #3 small-model eviction trap when splitting fast/strong.

### 2026-10-27 — P0: first timing instrumentation landed
- **What we did:** added time-to-first-token (TTFT) + cold/warm capture to `src/jobs/chat_runner.py`;
  every job now logs `timing job=… role=… warm=… ttft=…s total=…s` and the same fields ride on the
  `agent_done`/`session_done` events. Additive only — 526 tests still pass.
- **Why TTFT:** it's the *perceived* speed (when text starts appearing); `total_seconds` already
  existed but hid where the wait was. `warm=false` on a slow job will confirm the cold-load theory.
- **How to read it:** watch the app log while sending a few prompts; compare `warm=true` vs
  `warm=false` TTFT to size the cold-load penalty before P1 touches the unload policy.
- *Still open (P0):* tool-phase split, queue-wait, agentic path, UI surfacing.

### 2026-10-27 — P1: keep-warm policy landed
- **What we did:** chat calls now send an explicit Ollama `keep_alive` (`LLM_KEEP_ALIVE`, default `30m`)
  on all three request paths (`_chat_ollama`, `chat_tools`, `_chat_ollama_stream`), and the API warms
  the chat model on startup (`LLM_WARM_ON_START`, `warm_model` on a daemon thread in the lifespan).
  Added to `.env.example`. Additive — 526 tests still pass.
- **How it respects the SDXL tension (§2):** image jobs keep unloading Ollama (`IMAGE_UNLOAD_OLLAMA`),
  so VRAM still frees for SDXL; the chat model just reloads (cold) on the next message after an image.
  Net: *warm while chatting, yield for images* — the policy the planning note asked for.
- **Why default 30m, not `-1`:** `-1` (never evict) would hold VRAM through long idle gaps and fight
  SDXL more; `30m` keeps a work session warm and still frees when idle. User-overridable.
- **How to validate (your run):** restart the API, send the first message — P0 should now log
  `warm=true` (was `warm=false` cold before). Compare TTFT first-vs-second message.
- *Still open (P1):* fast/strong routing, prompt trim, caching.
