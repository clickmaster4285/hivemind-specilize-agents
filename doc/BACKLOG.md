# MulteAgent — Backlog & Decisions

Things we have decided, and things we will plan and build later.
**Current focus: the Knowledge Brain (Phase 1).** Everything under "Later" waits until the brain works.

Related: [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md) · [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) · [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md) · [ISSUES_LOG.md](ISSUES_LOG.md) (problems we hit and how we solved them)

---

## Decisions (locked)

| Date | Decision | Why |
|------|----------|-----|
| 2026-10-14 | One main model: `qwen3.5:9b` for router, chat, agents, vision | 10/10 routing in benchmark; stays loaded in 8GB VRAM (~5.7GB at 8K ctx); no model swapping |
| 2026-10-14 | `LLM_THINK=false` by default | Thinking made qwen3.5:9b take ~65s instead of ~1s for a short answer |
| 2026-10-14 | Small models removed (`qwen3:0.6b`, `qwen3.5:2b`) | 6/10 and 7/10 routing accuracy; running them on the GPU evicted the 9b |
| 2026-10-14 | **No cloud models** | Everything stays on our own hardware |
| 2026-10-14 | Embedding model: `qwen3-embedding:0.6b` (multilingual, 1024 dims, 32K input) | Multilingual retrieval; long chunks allowed |
| 2026-10-14 | Debate personas (Optimist / Cynic / Consensus) will be **replaced** by domain specialists | Business-debate personas are not what we need (see L2) |
| 2026-10-14 | Brain storage: **PostgreSQL + pgvector, run with Docker** | Team concurrency, private-data isolation, hybrid search in one database, same setup moves to a server later |
| 2026-10-14 | Languages: **English + Hinglish** | Keyword search needs a non-stemming setup + fuzzy matching for Hinglish spellings; test set must include Hinglish |
| 2026-10-14 | Brain starts from **public sources**; we index them, we do not train the model | Facts live in the database, so they can be updated and cited |
| 2026-10-14 | **Private data never mixes with the shared brain** | Internal data belongs to a user/account (and its agents) and is only searchable by them |

Benchmark evidence: the router, panel-quality and parallel-load test (`bench_models.py`, results from 2026-10-14) — rerun when models change.

---

## Now — Knowledge Brain (Phase 1)

The brain is the knowledge system, not the model: documents + metadata + search index + update tracking.
The model only reasons over what the brain retrieves.

- [x] Brain design doc → [BRAIN_DESIGN.md](BRAIN_DESIGN.md) (draft, open decisions in §11)
- [ ] **Hybrid retrieval**: vector search (meaning) + keyword/full-text search (exact terms such as CVE IDs, error codes, package names, flags), merged and optionally re-ranked
- [ ] **Fresh sources**: scheduled ingestion of trusted official sources (docs, advisories, feeds) + change detection + re-indexing — no retraining
- [ ] Answers cite sources and say "not enough evidence" when retrieval finds nothing relevant
- [ ] Stale-content tracking (last checked date, version, superseded documents)
- [ ] Evaluation set: 30–50 questions with known source documents, in the languages we use
- [ ] Embedding calls go through the GPU lock rules (or run on CPU) so they do not evict the 9b

---

## Later — planned, not started

### L1. Model comparison: `qwen3.5:9b` vs `gemma4:12b`

Decide whether to keep both or only one. Compare on the same prompts:

| Category | What to measure |
|----------|-----------------|
| IT / dev / marketing knowledge Q&A | Correctness (spot-check against docs) |
| Grounded answers (with brain context) | Uses only the given passages, cites them, admits gaps |
| Instruction following | Exact formats, JSON validity |
| Vision | Screenshots, error dialogs, diagrams, OCR of small text |
| Multilingual | Same questions in each language we use |
| Coding | Small scripts, config files, debugging |
| Cost | Seconds per answer, tokens/sec, VRAM, CPU spill |

Rule: keep `gemma4:12b` only if it **clearly** wins somewhere that matters (likely vision) at acceptable speed. It is 8GB and does not fit fully in VRAM, so using it means swapping out the 9b. Otherwise remove it.
Run this after the brain exists, so the "grounded answers" test uses real retrieval.

### L2. Domain specialist agents (replace the debate) — ✅ first version 2026-10-17 (ISSUES_LOG #39)

Decided: picked per message by one short model call reading each specialist's "covers" line (37/38), or
pinned in the chat bar; tools per specialist (network_scan IT-only); collections searched first, then all;
debate mode removed. Still open: specialists working together on one question, more eval questions per field.

Replace Optimist / Cynic / Consensus with specialists for fields within the industry, e.g.:

- **IT** (infrastructure, Linux/Windows, networking, security)
- **Development** (code, architecture, DevOps)
- **Marketing** (campaigns, copy, SEO, analytics)

Each specialist = its own instructions + its own tools/permissions + its own knowledge collections in the brain.
To plan:
- How the router picks a specialist (or several) for a question
- When specialists collaborate (e.g. IT + Dev on a deployment question) and who writes the final answer
- Tool permissions per specialist (read-only first)
- Whether a "debate/second opinion" mode is kept at all
- Evaluation questions per field

### L3. Stop / pause and progress feedback

What exists today:
- A **Cancel** button in the header, shown only while a job runs — easy to miss
- Cancellation is checked **between** steps; an LLM call already running cannot be stopped and can last until `LLM_TIMEOUT_SECONDS` (300s)
- No visible progress while the model is generating

To build:
- Stop button in the composer (Send turns into Stop while running)
- Streaming responses (`stream: true`) so text appears as it is written and Stop closes the request immediately
- Status line: queued → loading model → generating → done, with elapsed time
- Clear message when something stops or fails: timeout, Ollama down, out of memory, cancelled
- Pause between agent steps (maybe)

### L4. Enforce local-only (no cloud)

- `qwen3.5:cloud` is still in the model registry with role `cloud`, and cloud rows are always treated as available — remove it and make the registry ignore `:cloud` models
- Remove or disable the cloud overflow path (`OVERFLOW_WAIT_SECONDS`) and the `REMOTE_LLM_*` settings

### L5. Router heuristics

- Rule-based routing sent a clear decision question ("…would a self-service portal be worth it compared to hiring two more agents?") to plain chat before the model was asked
- Revisit once specialists exist — routing will become "which specialist", not "chat vs debate"

### L5c. Citation checker (answer quality) — ✅ built 2026-10-19

- **Built:** after each knowledge-base answer, one more model call (`src/brain/verify.py`, the same
  quote-first judge as the B6 test) checks every cited sentence; the chat shows "✓ 6 of 7 statements
  confirmed by the sources" and lists the unsupported ones. The answer is readable meanwhile; nothing is
  deleted; a failed check only hides the line. Off switch: `VERIFY_ANSWERS=false`. Not yet: web-search answers.

Earlier notes:

- **Done:** commands are checked against the sources with no model call (`src/brain/command_check.py`)
- **Measured (B6):** 89% grounded; prompt rules no longer help (two runs at 89%). Left: closing filler
  ("This command helps…") and the model's own examples cited as if from the source
- **To decide:** a second model pass per answer costs ~8 s — over the 20 s target if the user waits for it.
  Option: show the answer at once, run the check after it, then mark unsupported sentences ("✓ verified")
- After the model answers, check each cited sentence against its passage (cheap: keyword/embedding overlap; stronger: a second short LLM pass) and drop or flag unsupported claims
- Seen in B4: "Scavenging tab hota hai jab record ki TTL expire ho chuki ho [5]" — passage [5] does not say that

### L5b. The model does not know today's date — ✅ fixed in B4 (date/time in every chat prompt)

- Asked "what is the date today", it answered "October 16, 2023" once and "I don't have real-time information" the other times
- Fix: put the current date/time (and timezone) into every system prompt — small change
- Same root cause as stale facts ("who is the PM…"): the model only knows its training data; fresh facts must come from the brain

### L7. SDXL memory is double the expected size → slow images — ✅ resolved 2026-10-16

**Root cause:** the venv had `torch 2.14.1+cpu` (pulled in by `accelerate` on 2026-10-13 instead of
`requirements-image-cu118.txt`). SDXL silently ran on the CPU in float32 → double RAM, ~4 min/image,
GPU at 0%. Fixed by installing `torch==2.7.1+cu118` / `torchvision==0.22.1+cu118`:
image 2: 215 s → **41 s**, service RAM 17.4 → **10.4 GB**, GPU up to 100% / 7.3 GB.
Guard added: the image service now **refuses** CPU mode with a clear "install the GPU build" message
(`IMAGE_ALLOW_CPU=true` to override). Remaining quality item: 768×768 sometimes gives tiled/collage
images (SDXL is trained at 1024) — try 896/1024 now that the GPU works.

Original notes:

- The image service holds **17.4 GB** with SDXL loaded; the fp16 checkpoint is 6.46 GB, so ~8 GB is expected
- Likely some components loaded/kept in fp32 (log: "Casting directly with `to()` … set torch_dtype") or the load peak not released
- Effect: with ~12 GB already in use the PC runs near 31.7 GB → paging → a second 768², 16-step image took **215 s**
- Measure per-component dtype/size once, fix (e.g. explicit fp16 for every component, `low_cpu_mem_usage`), re-time
- Option after that: `enable_sequential_cpu_offload` vs model offload, TinyVAE

### L6. Cleanup

- `deepseek-r1:1.5b` is still installed — failed JSON/instruction tests and ignores `think:false`; remove
- Old rows in the model registry (`llama3.1`, `llava`, `qwen2.5:3b`, …) — mark as unused or delete

### L8. Serving throughput: revisit vLLM — only if we go multi-user on Linux + GPU

Today we serve `qwen3.5:9b` with **Ollama** (`src/llm/llm_client.py`, `src/llm/ollama_mgmt.py`, 127.0.0.1:11434),
which fits the local-first, single-model, one-user-at-a-time setup on Windows + 8 GB VRAM.

**vLLM** is a high-throughput inference server (PagedAttention + continuous batching, OpenAI-compatible API).
Its advantage is serving **many concurrent requests** on datacenter GPUs — wasted on a handful of local users,
and it is Linux/CUDA-first (poor Windows support; WSL2 + NVIDIA only). So: **not now.**

Revisit **only if** MulteAgent moves to **many simultaneous users on a dedicated Linux box with a real GPU**.
Then vLLM's throughput would clearly beat Ollama, and because its API is OpenAI-compatible, `llm_client.py`
should repoint with little change. Decide it with a **throughput benchmark** (extend `src/llm/bench_models.py`),
not by assumption. _Noted 2026-10-31 — keeps the local-only rule (L4) unless/until that scaling need is real._

### L9. Serial number for inventoried servers over SSH (credentialed path)

`network_scan` now reads a serial **only** where a device offers one to a credential-free sweep — i.e. SNMP
(community `public`) on managed gear (`snmp_identify` in `src/servers/net_scan.py`). Ordinary PCs/servers never
expose a serial that way. The remaining case is a server we have **added to inventory with SSH credentials**:
there we could read the real serial with one read-only command per OS — Linux `cat /sys/class/dmi/id/product_serial`
(or `dmidecode -s system-serial-number`, root), Windows `Get-CimInstance Win32_BIOS | % SerialNumber`. This belongs
to the authenticated `server_ops`/`host_check` flow, **not** the read-only LAN sweep, so it was deferred out of the
net_scan identification work (phases 1–3). Pick it up when inventory + stored SSH credentials exist. _Noted 2026-10-31._

---

## Progress log

| Date | Item | Notes |
|------|------|-------|
| 2026-10-14 | Model setup | `LLM_THINK` setting added; all roles on `qwen3.5:9b`; embedding models hidden from chat model list |
| 2026-10-14 | Backlog created | This file |
| 2026-10-17 | **Web search on request** (step 4) | SearXNG in Docker (`searxng/searxng:2026.9.30-a9d990033`, 127.0.0.1:8888 only, secret `SEARXNG_SECRET`); `src/web_search.py`: SearXNG → top 4 pages read in parallel with the brain's polite fetcher → embeddings pick the 6 best passages → answer with `[n]` citations + source list; web text treated as data, nothing stored in the brain. Router intent `web_search` (`data/router/intents.yaml`, examples EN+Hinglish) + **Web** button (`force_intent: web_search`); eval 56/56, 0 wrong. `WEB_SEARCH_URL` in `.env`/`.env.example` |
| 2026-10-17 | **The assistant picks its tools (native tool calling)** | No more intent routes per situation: tools = one entry each in `src/assistant.py`; the model answers or calls a tool with arguments. Eval 27/27. New weather tool (6.5 s). Web search 60 s → 12 s. ISSUES_LOG #37 |
| 2026-10-17 | **First action tool: `network_scan`** | "Check all the IPs … table" now scans instead of advising: direct route (no LLM planning) + agentic tool; private ranges only, ≤1024 addresses, 60 s, Stop, off switch. Real LAN: 25 devices in 9.6 s, table + .xlsx. First brick of the L2 IT specialist; pattern for more networking/security tools. [TOOLS_DECISION.md](TOOLS_DECISION.md) §7 |
| 2026-10-17 | Semantic router, browser time zone, model identity from Ollama, web search | Intents = example sentences in `data/router/intents.yaml` (eval 61 cases, 0 wrong); time in the user's own zone; no hand-made model table; SearXNG in Docker for "search the web" |
| 2026-10-17 | **L3 done** (chat + diagrams) — streaming + Stop | Answers stream into the chat; Send turns into Stop, which ends the model call at its next word and keeps the text; statuses Searching / Loading model / Thinking / Writing with a timer; plain error messages. Details: ISSUES_LOG #36. Left: debate/agentic streaming, pause |
| 2026-10-17 | App data → Postgres; "about MulteAgent" answers; diagrams | Users/jobs/events in schema `app` with own login (#34); questions about the assistant answered from live facts (#35); Mermaid diagram writer with fixes + review (#29–#33). 217 tests |
| 2026-10-16 | Images: RealVisXL V5.0 + Lightning, hires fix, prompt rewrite | Fast ~30 s / Balanced ~80 s / Quality ~190 s; `localhost` → `127.0.0.1` (+2 s per Ollama call removed). ISSUES_LOG #21–#28, #31–#32 |
| 2026-10-16 | **SDXL image service** — own process | API RAM at start: +13.3 GB → **+0.7 GB**. Cause was SDXL warm-loading inside the API (`enable_model_cpu_offload` keeps the whole model in system RAM). Now: `src/image_service.py` (127.0.0.1:8790, token, one job at a time, cancel, `--exit-when-idle`), `src/image_client.py` (API starts it on the first image request, sends jobs over HTTP, forwards cancel); the API never imports torch/diffusers. Service exits after `IMAGE_IDLE_UNLOAD_MINUTES` (10) → all memory returned. `IMAGE_WARM_ON_START=false` by default. `start.ps1` starts Docker/db, Ollama, API, UI. 103/103 tests. Real run: first image 257 s (incl. load), service 17.4 GB, exited after 73 s idle (1-minute test setting). |
| 2026-10-16 | **B5 done** — web sources + automatic refresh | `src/brain/web.py` (robots.txt, 1 req/s/host, same-host redirects only, 5 MB/30 s limits, ETag/Last-Modified → HTTP 304), kinds `web_page` (bounded link-following, `crawl_depth` ≤ 3), `sitemap` (incl. indexes), `feed` (RSS/Atom); HTML → Markdown via main-content + `markdownify` (`trafilatura` rejected: mangled inline code/tables); outage protection (unreachable start page stops the run — never "all pages missing"); `src/brain/scheduler.py` started with the API (every 30 min, optional `BRAIN_REFRESH_WINDOW`, advisory lock), manual `python -m src.brain.ingest --due`. Second source **python-docs-howto** (PSF-2.0): 27 pages, 615 chunks; first run lost internet for the last 3 pages (status `partial`), re-run fetched those 3 and skipped 24 unchanged. 81/81 tests (local test web server, no internet). Eval 60 questions: **hybrid Recall@8 100%, MRR 0.95, 5/5 out-of-scope refused** |
| 2026-10-15 | **B4 done** — grounded answers in chat | `src/brain/answer.py`: retrieve → numbered passages + current date/time + grounding rules → `qwen3.5:9b` (temperature 0.2) → `[n]` citations → sources list in the chat UI (`SourceList` in ControlDeck) → `brain.retrieval_log` row per answer (private per account). Chat uses the brain automatically for real questions (≥3 words); greetings, creative writing, images, documents skip it. No evidence → `BRAIN_NO_EVIDENCE=general` (default: marked notice + general answer) or `refuse`. Brain down → chat still answers with a "knowledge base unavailable" notice. Every chat reply now gets the current date/time (fixes L5b). 59/59 tests, frontend typecheck clean. Real answers: English/cmdlet/off-topic correct; Hinglish fixed after prompt rule (keep technical terms in English — model had glossed "scavenging" as "pakhana"). Follow-ups: (1) **citation checker** — the 9B model sometimes cites a passage that does not support the sentence (e.g. "scavenging happens when TTL expires" [5]); (2) long answers take 25–60 s — streaming (L3) will make this feel faster; (3) debate/agentic modes do not use the brain yet (comes with specialists, L2) |
| 2026-10-15 | **B3 done** — hybrid search | `src/brain/retrieve.py` (meaning search + exact-identifier vote, weighted RRF, max 2 passages/page, 3,000-token budget, evidence threshold 0.49); `GET /api/brain/search`, `GET /api/brain/stats`; eval `python -m src.brain.evaluate` on 45 questions (40 answerable: 28 EN + 12 Hinglish, 8 of them exact identifiers; 5 out-of-scope): **hybrid Recall@8 100%, MRR 0.93, 5/5 out-of-scope refused** (meaning-only 0.88, keyword-only 0.64). Big tables now split by rows (chunker v2; 483 chunks, max 591 tokens). 44/44 tests. Follow-ups: heading_path repeats the page title (cosmetic); first query after idle ~2.3 s while the embedding model loads on CPU; evidence margin is small (0.451 vs 0.526) — grow the eval set with each new source |
| 2026-10-15 | **B2 done** — source registry + ingestion | `python -m src.brain.ingest` syncs `data/brain/sources.yaml` and ingests enabled sources. First source: 42 Microsoft DNS pages → 472 chunks, 0 failures, ~7 min (embedding on CPU); re-run skips unchanged pages in 6.5 s. Migration `002_source_config.sql` (sources.config). 31/31 tests pass. Citation URLs verified (learn.microsoft.com, HTTP 200). Follow-ups for B3: (1) split very large tables by rows with the header repeated (largest chunk = 2,684-token audit-events table); (2) Hinglish "purane DNS records apne aap delete kaise honge" ranked manual deletion above *aging & scavenging* — first eval-set question; (3) HTML extraction arrives with web sources (B5) |
| 2026-10-15 | **B1 done** — brain schema | `deploy/brain-migrations/001_init.sql` applied via `python -m src.brain.migrate`; 6 tables + `source_status` view in schema `brain`; app login `brain_app` (no superuser, no RLS bypass); 12/12 RLS tests pass (`python -m pytest tests/test_brain_rls.py`). First source registered: Microsoft Windows Server DNS docs (CC-BY-4.0) in `data/brain/sources.yaml`. Gotcha: use `127.0.0.1`, not `localhost`, in `BRAIN_DATABASE_URL` (Windows tries IPv6 first and hangs) |
| 2026-10-14 | Brain database up | Docker `pgvector/pgvector:0.8.0-pg17` on 127.0.0.1:5432 (`docker compose up -d db`); extensions `vector` + `pg_trgm`; password + `BRAIN_DATABASE_URL` in `.env`. Search test: Hinglish↔English vector matches work; exact CVE ID found by full-text; trigram weak on very short words (`kese`/`kaise` = 0.22) |
