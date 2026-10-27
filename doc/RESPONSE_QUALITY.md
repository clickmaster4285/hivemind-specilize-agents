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
| D6 | **Keep the default answer's richness/length** (~20–25 s is acceptable); do **not** trim generation for speed. The **Deep** button is *not* a conciseness switch — it triggers **in-depth research across many sources**. (Decided 2026-10-27 after measuring that generation, not tools, owns the time — and the user is fine paying it for a good default answer.) | The default already gives a good grounded answer; speed there is good enough. Depth should *add* sources/analysis on demand, not change the default. → P1 micro-opts (prompt trim, routing, caching) are **deferred**; **P3 Deep mode is next.** |

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
- **Done (web tool-phase split):** `src/tools/web_search.py` now logs
  `web_timing search=…s read=…s synth=…s pages=N passages=M` — the three phases of a web answer.
  Combined with the `timing … ttft=…` line it decomposes the ~11 s web TTFT.
- **Still open:** decide()/tool-choice time, queue-wait (job `created_at`→`started_at`), the agentic
  path (`src/agentic/`), and surfacing TTFT/warm in the UI.

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

### P3 — Deep mode  *(status: v1 built 2026-10-27)*  — behind the **Deep** toggle
Per D6, Deep = **research a topic in depth across many sources** (the default stays as-is). Design:
- **Trigger:** a **Deep** toggle next to Send (like the Web button); sets `depth="deep"` on the job.
- **Pipeline:** **understand/plan** (one small call → 2–4 focused sub-queries) → **research**
  (for each sub-query: SearXNG + read pages; more than the default — target ~10–12 pages / ~8–10
  passages total, deduped; optional 2nd round if coverage is thin) → **synthesize** a thorough,
  well-sourced report (the rich table+diagram format is *wanted* here; optionally re-enable Qwen
  thinking) with citations. Every step streams to `LiveStatus`.
- **Bounded** because strictly local: hard caps on sub-queries, pages, and rounds (config knobs) so a
  deep answer is a minute-ish, not open-ended.
- Reuses the existing `src/tools/web_search.py` machinery (search/gather/bm25) with bigger budgets.

### P4 — Options  *(status: v1 built 2026-10-27)*  — behind the **Options** toggle
- Approach cards → expand (D4). A one-shot **Options** toggle next to Send (disarms after sending);
  the job carries `options=true` → the worker runs `run_chat(options=True)` →
  `src/tools/answer_options.py` generates 2–3 `Title | hint` cards (one model call) → shown as clickable
  cards under the message (`OptionCards` in `MessageExtras`). Clicking a card sends a **plain** follow-up
  (`Answer using the "<title>" approach … Question: <original>`) that writes the full answer.
- Deep and Options are mutually exclusive toggles; if the model returns no parseable cards it falls back
  to a normal answer.

### Richer answers (R1–R4) — ordered plan  *(agreed 2026-10-27, build in this order)*
Goal: close the remaining gap vs ChatGPT-style answers (sources already done — reference queries now
web-search). Build strictly in order; each ships green before the next starts.

- **R1 — Feedback (👍/👎).** *What:* thumbs up/down (+ optional note) on each answer. *How:* a `feedback`
  table (`id, job_id, user_id, rating, note, created_at`, via `create_all`); `POST /api/feedback`;
  buttons in the message toolbar; per-session state so the choice sticks. *Why:* finishes P2, cheap,
  independent. *(status: ✅ v1 built 2026-10-27 — `Feedback` table + `set_feedback` + `/api/feedback`
  + `FeedbackButtons` under each answer. Rating only for now; the note field exists server-side for a
  later inline "what was off?". 542 tests pass.)*
- **R2 — Diagram in educational answers.** *What:* "parts of / how X works / architecture of" answers
  include a Mermaid diagram where it helps. *How:* strengthen the web/answer synthesis prompt to add a
  ```mermaid block for structural/architectural topics (we already render them). *Why:* quick, accurate,
  fully local, high visual value. *(status: not started)*
- **R3 — Quiz under educational answers.** *What:* a short multiple-choice self-check under educational
  answers. *How:* a dedicated call (`generate_quiz`, like options/suggestions) → MC questions + correct
  answers → a `QuizCard` (pick, then "check answers" reveals). Gated to educational answers. *Why:*
  learning polish; reuses the dedicated-call + card patterns. *(status: ✅ v1 built 2026-10-27 —
  `src/tools/quiz.py`, `QuizCard`, gated by `_is_educational` in chat_runner, `QUIZ_ENABLED/COUNT`.)*
- **R4 — Real images (web image search + safe local proxy).** *What:* a small gallery of relevant real
  photos atop educational/visual answers. *How:* SearXNG image search → backend **fetches each image
  server-side** (polite client, size/type/time limits) and **re-serves it locally** (like attachments)
  → the frontend shows the *local* URL (passes `isOwnImage`, so no external load, no IP leak). Gated to
  topics where images help; **we** control the search (not the model emitting URLs). *Why:* the biggest
  visual gap; highest effort; a *careful* relaxation of the no-external-image rule, kept safe by the
  proxy. *Risks:* privacy (mitigated), storage, relevance. *(status: ✅ v1 built 2026-10-27 —
  `src/tools/web_images.py` with an **SSRF guard** (blocks private/loopback/link-local hosts) +
  size/type/time limits; saved to the job's attachment store and served by the existing authed
  endpoint; emitted as `images` → `AgentThumbs`; `WEB_IMAGES_ENABLED/COUNT`. Gated by `_is_educational`.)*

### 2026-10-27 — R3 (quiz) + R4 (real images) built
- **R3 quiz:** educational answers (gated by `_is_educational` — parts of / explain / how X works / …)
  now get a dedicated `generate_quiz` call → an interactive `QuizCard` (pick, then "Check answers" shows
  correct/score). `QUIZ_ENABLED/COUNT`.
- **R4 images:** the same educational answers get a small gallery of **real photos** — SearXNG image
  search → each image **fetched server-side** (SSRF guard blocks private/loopback/link-local; content-type
  must be an image; ≤5 MB; timeouts) → saved into the job's attachment store → served by the existing
  authed `/attachments` endpoint → shown via `AgentThumbs`. The browser never touches the external URL, so
  no IP leak. `WEB_IMAGES_ENABLED/COUNT`.
- **Cost (honest):** an educational answer can now trigger up to three extra calls after the text —
  suggestions + quiz + image fetch (~several seconds). They run *after* the answer is streamed, so reading
  isn't blocked, but the message finalises later; each is config-gated and best-effort. If it feels heavy,
  turn off `QUIZ_ENABLED` / `WEB_IMAGES_ENABLED` or we emit the extras as separate post-answer events.
- 7 new tests (quiz parse, SSRF guard, educational gate via existing paths); 546 pass; frontend builds.

### 2026-10-27 — Validated (user): "parts of a motherboard" now rich & grounded
- **Measured (user run):** the same query that was a plain 58s "Direct reply" now returns **Web search ·
  45s** with: 4 relevant labeled motherboard images (proxied), a cited structured parts+functions answer,
  a Sources table (3 cited / 4 found), a 4-question quiz, follow-up chips, and 👍/👎. Everything from the
  R-plan firing together. User verdict: "check the response" (👍).
- **The one trade-off = latency.** ~45s because the extras stack *after* the answer: web answer (~25–30s)
  → images (search+fetch) → quiz → suggestions. The text streams first so reading isn't blocked, but the
  message stays "pending" until the extras finish (~15s tail).
- **Optimizations on the table (not yet done):** (a) emit the answer's `agent_done` first, then stream
  `images`/`quiz`/`suggestions` as *separate* post-answer events so the message completes at ~30s and the
  extras pop in after; (b) parallelize the image fetch in `gather_web_images` (currently sequential).

### 2026-10-27 — Both latency fixes done
- **(a) Answer completes first; extras stream in after.** `run_chat` now emits `agent_done` (answer +
  sources + verification) as soon as the text is done, *then* computes suggestions/quiz/images and emits a
  separate **`answer_extras`** event; the reducer merges it into the last answer. So the message is "done"
  at ~answer time and the chips/quiz/images pop in a few seconds later instead of holding the message
  "pending". (Status spinners for the extras removed — the message is no longer pending.)
- **(b) Images fetched concurrently.** `gather_web_images` now downloads all candidates in a
  `ThreadPoolExecutor` and keeps the first N that succeed (in search order), instead of one at a time.
- Additive; 546 tests pass; frontend builds. The ~45s total is unchanged, but the *perceived* finish is
  now ~answer-time, with extras arriving after.

### 2026-10-27 — Chat-view polish (user feedback)
- **Images too big / dominant → small strip.** Web images were going through `imageFiles` → `AgentThumbs`
  (max 36rem, at the top). Now emitted under a distinct `web_images` key → a new `WebImages` component
  renders a compact strip of uniform 5.5rem thumbnails **under the answer text** (click to enlarge).
  Generated images still use `AgentThumbs`.
- **Open chat not shown as active →** the real cause: `navBase` has `bg-transparent border-transparent`,
  and Tailwind resolves conflicts by CSS source order (not class order), so it was silently overriding
  `navActive` — the highlight never rendered. Fixed with the `!` important modifier on the active bg/border
  (copper tint + copper border now win).
- **Opened chats scrolled to the top →** `MessageFeed` now scrolls a bottom anchor into view on open /
  new turns / while streaming, so you land on the latest message.
- **Switch-away-and-back lost the message →** `openThread` rebuilds from `thread.jobIds`, which only had
  jobs from the last `refreshJobs`; a just-sent job wasn't there yet. `send` now calls `history.addJob`
  immediately so the job is in state (and grouped into its chat) at once. *Known remaining edge:* if you
  switch while an answer is still streaming, the live stream can briefly apply to the wrong feed — a
  deeper per-chat-stream fix, noted for later.
- 546 tests pass; frontend builds.

### P2 — Suggestions + feedback  *(status: suggestions v1 built 2026-10-27; feedback = R1 above)*
- **Suggestions — done.** After a substantive answer (>120 chars, not the options list), a small
  dedicated call (`src/tools/suggestions.py`, the reliable pattern) produces `SUGGESTIONS_COUNT` short
  follow-up questions → attached to `agent_done` as `suggestions` → `SuggestionChips` renders them under
  the **last** answer; clicking one asks it as a normal new question (routes as usual). `SUGGESTIONS_ENABLED`.
- **Feedback — next.** 👍/👎 + optional note per answer. New `feedback` table
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

### 2026-10-27 — Measured: keep-warm validated; the bottleneck moved to the web-search phase
- **Measured (user run):** `Warmed … qwen3.5:9b` logged at startup; **every** chat job logged
  `warm=True`, even one right after an image job unloaded Ollama. → keep-warm works; **cold-load is no
  longer the steady-state bottleneck — stop optimizing there.**
- **New finding:** for Web-search answers, time-to-first-token was **~9–14 s while warm**
  (ttft 10.2 / 9.0 / 13.9 s; totals 19.5 / 38.0 / 22.4 s). Since the model is warm, that pre-answer wait
  is the **tool phase** (`decide()` → SearXNG → fetch/read pages), *not* model load. The 38 s job wrote
  a long table+diagram (generation-bound). A plain Direct reply was ~6 s ("hi"); a long Direct reply
  (processor) ~24 s (generation-bound). Time is also **wasted fetching robots-blocked pages**
  (reddit, quora), and many factual questions route to web search (the nudge), each paying the ~11 s.
- **Decision:** next is the P0 **tool-phase split** — instrument the web-search path into
  decide / search / fetch / synth-start — then attack the biggest chunk (likely parallelize + cap page
  fetches, skip known robots-blocked domains). Long-answer generation is largely model-bound (local 9B).

### 2026-10-27 — P0: web-search tool-phase split added
- **What we did:** `answer_from_web` now logs `web_timing search=…s read=…s synth=…s pages=N
  passages=M`. `search` = SearXNG; `read` = fetching/ranking pages (capped at `READ_BUDGET_SECONDS=6`);
  `synth` = the model writing the answer. Additive; 526 tests pass.
- **How it decomposes TTFT:** run_chat's `ttft` is measured from the answer start and the first token
  appears after search+read, so **synth-to-first-token ≈ ttft − search − read**, and `web_timing.synth`
  is the full generation time. Read `web_timing` right above each job's `timing` line.
- **Next:** get one real `web_timing` sample to see whether the ~11 s is search, read, or synth, then
  pick the P1 web lever (parallel/cap fetch, skip robots-blocked, faster SearXNG, shorter synth prompt).

### 2026-10-27 — Measured: web tools are fine; the cost is answer-generation length
- **web_timing (two real samples):** search ~1.3 s, read 1.9–3.8 s, **synth 22–29 s**; ttft ~6.5 s,
  total 27–32 s. So ttft ≈ search + read + ~1.7 s; the rest is **streaming a long answer**.
- **Conclusion — hypothesis flipped:** the web fetch is *not* the bottleneck. The cost is the model
  writing a long, richly-formatted answer every time (Direct Answer + bullets + Summary Table + a
  Mermaid diagram). On the local 9B that's ~25 s of generation. (Read is also doing wasted work:
  reddit/quora/linustechtips are fetched then rejected by robots.txt and fall back to their snippet.)
- **Levers:** (1) *clean, no quality hit* — skip known robots-blocked domains before fetching (saves
  the wasted round-trip + a little read/ttft). (2) *the big win, a quality call* — **shorter default
  answers**: this IS the Fast lane. Make tables/diagrams opt-in (Deep), default to a concise answer →
  cuts synth from ~25 s toward ~8–10 s.
- **Decision pending (user):** quick conciseness tune to the synth prompts now (fast default; the rich
  report moves to the Deep lane later), vs. keep today's richness and get speed only once the
  Fast/Deep toggle is built (P3).

### 2026-10-27 — Decision: keep the default; build Deep research next (D6)
- **User call:** "~20 s we can bear … the deep research/thinking is happening when [the user] clicks …
  search a topic in depth from a lot of sources." → **keep the default answer's richness** (don't trim
  generation); the **Deep** button means *research across many sources*, not a conciseness mode.
- **Consequence:** P1 micro-opts (prompt trim, fast/strong routing, caching) are **deferred** — current
  speed is acceptable. **P3 Deep mode is now the active work** (see §3 for the design).
- *Lesson:* the measurement reframed the whole phase — "faster" wasn't the real ask; "deeper on demand,
  default stays good" was. Measuring first (P0) is what surfaced that.

### 2026-10-27 — P3: Deep research mode (v1) built
- **What we built:** a **Deep** toggle next to Send (`Composer.tsx`) → the job carries `depth="deep"`
  → the worker short-circuits tool routing and runs `run_chat(deep=True)` →
  **`src/tools/deep_research.py`**: plan ~4 focused sub-queries (one small model call) → search each on
  SearXNG + read pages (reusing `web_search.search/gather`) → pool, de-dupe, BM25-rank against the
  original question, cap to `DEEP_MAX_PASSAGES` (12) → write one thorough, well-sourced report with
  citations. Every step streams to `LiveStatus` ("Planning… / Searching 2/4: … / Reading… / Writing a
  sourced report from N passages across M sites…"). Logs `deep_timing research=…s synth=…s …`.
- **Config:** `DEEP_SUBQUERIES` (4), `DEEP_MAX_PASSAGES` (12) — the "Thorough" budget, tunable.
- **Scope (v1):** web-only, text-only; single research round (no 2nd round yet); no Qwen hidden
  "thinking" (the planning step + thorough synthesis is the "deep thinking"). 4 new tests (plan/dedupe);
  530 tests pass; frontend builds.
- **Validate (your run):** toggle **Deep**, ask a topic, watch the live steps + the `deep_timing` line;
  expect ~1–2 min and a sources table with many sites.
- *Next:* P4 — **Options** toggle (multiple candidate answers → approach cards → expand).

### 2026-10-27 — P3 Deep validated by the user
- **Measured (user run):** "deep dive on GDDR5 vs HBM vs HBM3" → **Deep research · 127.4s**, a thorough
  multi-section report (direct answer, 4 sections, comparison table, architecture diagram) with
  **10 cited sources from 12 found** across ~9 distinct sites. User verdict: "this is better."
- **Conclusion:** Deep v1 delivers the "research across many sources" the ask called for, within the
  ~1–2 min Thorough budget. Deferred refinements (2nd round, deep-think) remain optional, not needed yet.

### 2026-10-27 — P4: Options (approach cards) built
- **What we built:** a one-shot **Options** toggle next to Send (disarms after sending; mutually
  exclusive with Deep). The job carries `options=true` → worker → `run_chat(options=True)` →
  `src/tools/answer_options.py` asks the model for 2–3 `Title | hint` approaches (one call) → emitted on
  `agent_done` as `options`/`options_query` → `OptionCards` renders clickable cards → clicking sends a
  **plain** follow-up that writes the full answer that way. Config: `OPTIONS_COUNT` (3).
- **Robustness:** unparseable/no cards → normal answer; the `plain` send-flag stops a card click from
  re-triggering Options or Deep. 2 new tests (parser); 532 tests pass; frontend builds.
- **Validate (your run):** toggle **Options**, ask something open-ended (e.g. "how should I structure a
  home network?") → pick a card → full answer. This completes the original seven-item plan except
  **P2 (suggestions + feedback)**, which is the remaining cheap polish.
- **Bug fix (same day, user test):** *Symptom* — clicking a card answered "The web search found nothing."
  *Cause* — the composed follow-up ("Answer using the X approach … Question: …") still went through
  routing, and the factual-question nudge sent that long instruction to web search, which found nothing.
  *Fix* — a card pick now sends `direct=true`; the worker answers it straight from the model (no web/tool
  routing). Plumbed `plain`→`direct` through api.ts / useChat / JobCreatePayload / worker.

### 2026-10-27 — Auto-clarify (model asks first) + Options moved to the "+" menu
- **New decisions:** Options shouldn't only be a manual toggle — the model should **ask clarifying
  questions on its own** when a query is ambiguous; the user ticks options and submits, then it answers.
  Trigger = **the model decides in its single decision call** (no extra latency on clear queries). The
  manual Options toggle **moves into the "+" menu** (Deep stays next to Send).
- **How (auto-clarify):** added an `ask_clarification` tool (`src/llm/assistant.py`, always offered)
  with strict "only when genuinely ambiguous" guidance; `decide()` parses its JSON questions
  (`_parse_clarify`) → `Decision(intent="clarify")` → the worker emits a `clarify` message (no answer
  yet) via `_clarify`. Frontend `ClarifyForm` renders multiple-choice chips + **Submit**; submitting
  sends a **plain/direct** follow-up (`<original> + My choices: …`) that writes the answer.
- **Options → "+" menu:** the next-to-Send Options toggle is gone; "Answer options" is now a "+" menu
  item (`act({ options: true })`, one-shot). `wantOptions` state removed; `SendOptions.options` drives it.
- **Why in the first call:** the user picked it to avoid a second round-trip; the model emits either a
  clarify tool call, a normal tool call, or a direct answer — all in one decision.
- 5 new tests (`_parse_clarify`); 537 pass; frontend builds.
- **Risk to watch:** this adds a tool to `decide()`'s set — re-run the LLM tool-choice eval
  (`python -m src.llm.assistant --eval`) to confirm routing didn't regress and the model isn't
  over-asking. Tunable via the tool's description.
- *Deferred:* removing the "+" menu Options entry entirely once auto-clarify proves itself.

### 2026-10-27 — Correction: a picked approach must still be able to web-search
- **Symptom (user):** "what if it needs the web search then? it needs current equipment and their current
  prices and availability." — the earlier `direct=true` fix made a card pick (e.g. "Budget Build Guide")
  answer from the model's memory, with **no** web search → no current prices.
- **Cause:** over-correction. The original bug was a *bad search query* (the verbose "Answer using the
  approach … Question: …" string), not that web search was wrong for picks. `direct` threw out web search
  entirely.
- **Fix:** replaced `direct` with **`followup`**. A picked card / submitted clarify now **routes
  normally — web search included when the topic needs it** — but `decide(clarify_ok=False)` drops the
  `ask_clarification` tool so it can't loop. And the follow-up query now **leads with the original
  question** (`<question> — <approach/choices>`) instead of instruction framing, so a web search is clean.
- *Lesson:* don't fix a bad-query symptom by removing the capability; fix the query.

### 2026-10-27 — Auto-clarify made reliable (dedicated call, not the tool)
- **Symptom (user):** asked "ask some question and then tell me what suits" → the model wrote the
  clarifying questions as **plain numbered text**, not the multiple-choice form.
- **Cause:** the `ask_clarification` tool relies on the local 9B *choosing to call a tool* for a meta
  action — it won't do that reliably; its instinct is to write the questions out.
- **Fix:** added a **heuristic-gated dedicated call**. For decision/recommendation/setup-style requests
  (`_might_need_clarify`: "ask me…", "i want to buy…", "which … should I", "recommend", "what suits",
  "how do I start…"), `decide()` makes one small call (`_ask_clarifying`, `CLARIFY_SYSTEM`) that returns
  multiple-choice questions as JSON — or `[]` if the query is actually clear → then it answers normally.
  Reliable because it's a dedicated generation, not a tool the model may ignore. Clear/factual/greeting
  queries skip the gate (no extra latency). `clarify_ok=False` disables it on follow-ups. The tool
  remains as a dormant secondary path. 2 new heuristic tests; 537 pass.
- *Lesson:* don't make a local model call a tool for a meta-action it would rather write as prose —
  gate cheaply, then generate the structured form in a dedicated call.

### 2026-10-27 — P2 part 1: follow-up suggestions built
- **What we built:** after a substantive answer, `src/tools/suggestions.py` makes one small dedicated
  call (same reliable pattern) → 3 short "ask this next" questions → attached to `agent_done` as
  `suggestions` → `SuggestionChips` shows them under the **last** answer; a click asks that question
  normally. Config `SUGGESTIONS_ENABLED` / `SUGGESTIONS_COUNT`. Skipped for the options card list and
  short/stopped answers.
- **Cost:** one extra small call (~1s warm) after a substantive answer, with a "Suggesting follow-ups…"
  status; the answer is already streamed so it only delays the final chips, not the reading.
- **Test note:** the verify tests count every chat call through a fake client, so `_run` now sets
  `suggestions_enabled=False` to isolate verification. 4 new tests; 541 pass; frontend builds.
- *Next:* P2 part 2 — 👍/👎 feedback (capture-first).

### 2026-10-27 — Richer answers: reference queries now cite sources
- **Symptom (user, vs ChatGPT):** "parts of mother board" → a plain, uncited "Direct reply" (58s, and
  the 9B even duplicated the BIOS section) — no sources, no visuals.
- **Cause:** the web nudge only matched "what is / how to / compare…"; a bare noun phrase ("parts of X")
  slipped through, so the IT specialist (which *does* have web_search) answered from memory.
- **Fix (done):** broadened `_WEB_KW` to catch reference/educational shapes — "parts of X",
  "components of", "types of", "features of", "overview of", "architecture of", "explained",
  "how X works" — so they web-search and come back **cited + structured** (and grounding curbs the
  duplicate-section glitch). Diagram/local/code requests still skip web (`_WEB_SKIP`). Re-run the eval.
- **Still open (the visual gap vs ChatGPT), decision pending:** per-section **real photos** (needs a
  server-side image search + proxy, since we block external images for privacy — a real build);
  pushing a **Mermaid diagram** into such answers; an optional **quiz**. SDXL can't make accurate part
  photos, so images mean *web image search + local proxy*, not generation.

### 2026-10-27 — Inline edit (ChatGPT-style) of a user message
- **What we built:** clicking **Edit** on a sent message turns that bubble into an in-place textarea
  (Cancel / Send, Enter sends, Esc cancels) instead of refilling the bottom composer. Send replaces the
  message and regenerates from there: `useChat.send` takes `editFromId`, truncates the feed at that
  message (`baseFeed`), soft-deletes the dropped turns' jobs (`softDeleteJobs` + `history.dropJobs`), and
  sends the trimmed `conversation`. `MessageFeed` holds `editingId` and renders `<InlineEdit>`;
  `AppProvider.editAndResend` wires it. Frontend builds; backend unchanged (546 pass).
- *Lesson:* edit-in-place reads as "fix this turn," so the regeneration must drop everything after it —
  do it client-side (baseFeed) and mark the old jobs deleted so a refresh doesn't resurrect them.

### 2026-10-27 — Icon library (emoji everywhere + Iconify in diagrams)
- **Idea (user):** "a big icons library, so instead of always making a diagram we can simply use those
  icons to denote things — in a mermaid diagram, in the chat, in docs." Chosen scope: **both** tracks.
- **Constraint found first:** the Mermaid PNG/SVG export depends on `htmlLabels: false`
  ([MermaidDiagram.tsx](../frontend/src/components/MermaidDiagram.tsx)) — labels are plain SVG text so
  `svgToPng` can draw them onto a canvas. FontAwesome `fa:` icons need `htmlLabels: true` (HTML `<i>`),
  and HTML-in-SVG (`foreignObject`) does **not** render when the SVG is drawn into a canvas → flipping
  the flag would silently blank the exported images. So FA-in-flowchart was rejected.
- **Track A (emoji — universal):** added prompt guidance in `assistant.SYSTEM` to denote a
  component/status/step with a relevant emoji inline (🖥️🗄️📁⚙️🌐🔒📦 ✅/⚠️/❌ →) and to prefer a few
  icons over a whole diagram when one would be overkill. Emoji are plain Unicode → render in chat, docs,
  Markdown **and inside Mermaid labels**, with **no export regression**. Zero new deps.
- **Track B (real vector icons in diagrams):** chose Mermaid **`architecture-beta`** + **Iconify packs
  bundled locally** (no CDN, strictly local): `@iconify-json/logos` (2,282 tech/brand logos) and
  `@iconify-json/mdi` (7,638 generic shapes) ≈ **~9,900 offline icons**. Registered via
  `mermaid.registerIconPacks([...])` with lazy `import('@iconify-json/<pack>/icons.json')`; icons render
  as inline SVG so the PNG/SVG export still works. Taught the model the `architecture-beta` syntax +
  real icon names (logos:aws, logos:postgresql, mdi:server, mdi:database…) with a worked example, and
  "if unsure of a name, use a plain flowchart instead." tsc + next build clean; 546 pass.
- *Lesson:* "add an icon library" collided with an existing working feature (canvas export). Picking the
  diagram type whose icons are **real SVG** (architecture-beta/Iconify) keeps export intact where an icon
  **font** (FontAwesome via foreignObject) would have broken it. The 9B reliability risk is bounded by
  the existing "Fix diagram" path if it emits a bad icon name.

### 2026-10-27 — Images spread through the answer + diagram icons made directive (user feedback)
- **Symptom (user):** on the rich motherboard answer, "all the images again came together, each should be
  with the relevant content, with different sizing" and "in the diagram there is no icon used."
- **Cause:** `WebImages` rendered every image as an identical 5.5rem **cropped square** in one strip at the
  end of the message; and the icon guidance was only *"may use"* — the 9B drew a plain flowchart with no
  icons.
- **Fix — images (user chose "spread the same images through sections"):** extracted a `useWebImages` hook,
  added `SpreadImage` (natural size, capped 15rem, alternating left/right). `MessageFeed` now has
  `AnswerContent`: when the answer is done and images loaded, it `splitBlocks()` the Markdown at blank
  lines (keeping ``` / mermaid fences atomic so a diagram is never cut) and `assignGaps()` spreads the
  images evenly between blocks; while streaming or when there's only one block, it falls back to plain
  Markdown + a varied-size strip (fixed height, natural width). No extra image searches → no added latency.
- **Fix — diagram icons:** made the prompt **directive** — when the model draws a Mermaid flowchart of
  parts/components/architecture it must begin each node label with a fitting emoji
  (`A["🧠 CPU Socket"] --> B["🔗 Chipset"]`…). Emoji render in the browser view and need no htmlLabels flip.
- **Checks:** tsc + next build clean; 546 pass. *Model behavior (does it now use icons / how the spread
  looks) is confirmed by the user's eval + screenshots, not pytest.*
- *Lesson:* generic topic images can't be *semantically* matched to each section without a per-section
  search (declined for latency); spreading them across the answer's blocks at varied sizes gives the
  "attached to the content" feel cheaply, while fence-aware splitting keeps diagrams/code intact.

### 2026-10-27 — Diagrams: hard rules + a client-side parse safety net (user feedback)
- **Symptom (user, 3 example diagrams):** "too complex, no icon, and issues." A network example rendered
  fine but with plain boxes (no icons); an IP/subnet example **failed to parse** ("Parse error on line
  19" — `|Layer 2 Forwarding (MAC Table)|`, parentheses inside a pipe edge-label); the "fixed" retry was
  an unreadable sprawl (legend box, subnet box, traffic-flow subgraph, crossing dashed arrows).
- **Cause:** (a) the 9B ignores soft "may use emoji" guidance and reaches for `classDef` colours instead;
  (b) it over-engineers when asked for an "example"; (c) it emits invalid Mermaid (brackets/pipes in
  labels). Prompt-tuning a 9B only partly sticks — the model is the ceiling.
- **Fix 1 — hard prompt rules** (`assistant.SYSTEM`): a diagram must be SIMPLE and VALID — ≤7 nodes, one
  direction, **no legend/subnet/traffic-flow boxes or nested subgraphs unless asked**; **every node label
  must start with an emoji and NOT use classDef colours**; **parse-safe** (quote labels, 2–4 words, never
  `( ) : | / <br>` inside a label, prefer plain `-->`); **IPs/masks/ports go in prose or a table, never in
  nodes**. Added a clean worked office-network example. architecture-beta demoted to optional.
- **Fix 2 — client-side safety net** (`MermaidDiagram.tsx`): `sanitizeMermaid()` runs before render and
  normalises the common breakers (v1: strips `( ) [ ] { } "` from `|...|` edge-label text → plain
  multi-word label), so a small model mistake *draws* instead of erroring into a Fix-diagram round-trip.
  Deliberately minimal — only rewrites text that's invalid anyway (verified: the line-19 case is fixed,
  valid diagrams and classDef lines are untouched). Render/Copy/Show all use the sanitised code. tsc +
  build clean; 546 pass.
- *Lesson:* for a weak local model, pair "author better" (prompt) with "repair at the edge" (a
  conservative client-side sanitizer) so UX doesn't depend on the model getting syntax perfect. The real
  ceiling for clean structured output is model capacity — see the pending model-choice discussion.
- *Still open:* re-test the prompt on top of the net (user eval). If parse errors persist from other
  breakers, extend `sanitizeMermaid` (e.g. quote node labels with parens). Deeper lever: a stronger local
  model for diagram authoring (P4000 8GB limits this — `qwen2.5-coder:14b` is tight).
