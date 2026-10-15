# MulteAgent — Backlog & Decisions

Things we have decided, and things we will plan and build later.
**Current focus: the Knowledge Brain (Phase 1).** Everything under "Later" waits until the brain works.

Related: [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md) · [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) · [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md)

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

### L2. Domain specialist agents (replace the debate)

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

### L5b. The model does not know today's date

- Asked "what is the date today", it answered "October 16, 2023" once and "I don't have real-time information" the other times
- Fix: put the current date/time (and timezone) into every system prompt — small change
- Same root cause as stale facts ("who is the PM…"): the model only knows its training data; fresh facts must come from the brain

### L6. Cleanup

- `deepseek-r1:1.5b` is still installed — failed JSON/instruction tests and ignores `think:false`; remove
- Old rows in the model registry (`llama3.1`, `llava`, `qwen2.5:3b`, …) — mark as unused or delete

---

## Progress log

| Date | Item | Notes |
|------|------|-------|
| 2026-10-14 | Model setup | `LLM_THINK` setting added; all roles on `qwen3.5:9b`; embedding models hidden from chat model list |
| 2026-10-14 | Backlog created | This file |
| 2026-10-15 | **B2 done** — source registry + ingestion | `python -m src.brain.ingest` syncs `data/brain/sources.yaml` and ingests enabled sources. First source: 42 Microsoft DNS pages → 472 chunks, 0 failures, ~7 min (embedding on CPU); re-run skips unchanged pages in 6.5 s. Migration `002_source_config.sql` (sources.config). 31/31 tests pass. Citation URLs verified (learn.microsoft.com, HTTP 200). Follow-ups for B3: (1) split very large tables by rows with the header repeated (largest chunk = 2,684-token audit-events table); (2) Hinglish "purane DNS records apne aap delete kaise honge" ranked manual deletion above *aging & scavenging* — first eval-set question; (3) HTML extraction arrives with web sources (B5) |
| 2026-10-15 | **B1 done** — brain schema | `deploy/brain-migrations/001_init.sql` applied via `python -m src.brain.migrate`; 6 tables + `source_status` view in schema `brain`; app login `brain_app` (no superuser, no RLS bypass); 12/12 RLS tests pass (`python -m pytest tests/test_brain_rls.py`). First source registered: Microsoft Windows Server DNS docs (CC-BY-4.0) in `data/brain/sources.yaml`. Gotcha: use `127.0.0.1`, not `localhost`, in `BRAIN_DATABASE_URL` (Windows tries IPv6 first and hangs) |
| 2026-10-14 | Brain database up | Docker `pgvector/pgvector:0.8.0-pg17` on 127.0.0.1:5432 (`docker compose up -d db`); extensions `vector` + `pg_trgm`; password + `BRAIN_DATABASE_URL` in `.env`. Search test: Hinglish↔English vector matches work; exact CVE ID found by full-text; trigram weak on very short words (`kese`/`kaise` = 0.22) |
