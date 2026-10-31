# Knowledge Base — calibration & eval plan

A forward checklist for the brain's knowledge base, to be worked **one item at a time, later**.
This is the task plan; the design lives in [BRAIN_DESIGN.md](BRAIN_DESIGN.md), the running log in
[RESPONSE_QUALITY.md](RESPONSE_QUALITY.md). Tick items as they land.

_Created 2026-10-30._

## Where we are now (baseline)

- **34 enabled sources** across `it` (10), `development` (11), `marketing` (13).
- **2,436 documents · 23,755 chunks** in `brain.chunks` (pgvector), avg ~9.75 chunks/doc.
- Retrieval is **wired and collection-aware** and chat uses it: `chat_runner.py` → `answer_question`
  (`src/brain/answer.py`) → hybrid search (`src/brain/retrieve.py`). Grounded answers must cite `[n]`;
  an evidence gate (`MIN_SIMILARITY=0.49`) falls back to a "not found in the knowledge base" banner.
- **Not yet done:** the retrieval tuning and the eval question set still reflect the original
  **2-source** corpus. That is the gap this plan closes.

## Guiding principle

**Add sparingly, measure always, refresh on a schedule.** The registry
(`data/brain/sources.yaml`) is the one control surface; the eval set (`data/brain_eval/questions.yaml`)
is the one source of truth for whether a change helped. Don't add sources to fix a problem the eval
hasn't shown.

## Owner key

- **[C]** Claude can do it (code, data authoring, pytest).
- **[U]** User runs it (long LLM / embedding / ingest jobs — per project rule, Claude runs only pytest).

---

## The list (do in order)

### 0. Done
- [x] Expanded registry 2 → 34 sources with verified open licences (commit `4a5576c`).
- [x] Fixed Windows cp1252 crash in `_git` subprocess decode (commit `fc1de77`).
- [x] Removed the dead `google-search-docs-updates` feed from the registry.

### 1. Housekeeping  **[U]**
- [ ] Run `python -m src.brain.ingest` once so the sync **disables the removed dead feed** in the DB
      (it still shows as an enabled 0-doc row until then).
- [ ] **Fix `postgresql-docs` depth:** it has 80 docs but only ~195 chunks — crawled chapter *landing*
      pages, not content. Change its `crawl_depth` 1 → 2 in `sources.yaml`, re-ingest. **[C]** edits YAML,
      **[U]** runs the ingest. Expect chunks to roughly 5–10×.
      _2026-10-30: `crawl_depth` changed 1 → 2 in `sources.yaml` ([C] done). **[U] re-ingest pending.**_

### 2. Build the eval question set  **[C] draft, [U] review**
- [x] Expand `data/brain_eval/questions.yaml` from DNS-only to **~2–3 questions per source** across all
      three collections, plus `out_of_scope: true` questions to exercise the evidence gate. Schema:
      `id`, `question`, `docs: [url-ending]`, optional `facts: ["term|alt"]`.
      _2026-10-30 ([C] drafted): now **129 questions** (107 en / 22 hi-Latn), 5 out-of-scope, covering
      all 34 sources. No duplicate ids; YAML validated._
- [ ] **[U] review:** the 32 sources added 2026-10-30 have **best-effort `docs:` endings** — run
      `python -m src.brain.evaluate` and correct any ending that doesn't match an ingested document URL
      (the `facts:` terms should hold regardless). Do this after the re-ingest in item 1.

### 3. Re-measure retrieval & re-tune the gate  **[U]**
- [ ] Run `python -m src.brain.evaluate` (and `--mode vector|keyword|hybrid`) to get MRR per mode on the
      34-source corpus.
- [ ] **Re-tune `MIN_SIMILARITY`** (currently `0.49`) and the fusion weights (`VECTOR_WEIGHT=1.0`,
      `KEYWORD_WEIGHT=0.0`, `EXACT_WEIGHT=1.0`) in `src/brain/retrieve.py` — all measured 2026-10-15 on
      ~2 sources, and the code itself says to re-tune when sources change. **[C]** proposes new values
      from the eval numbers; **[U]** re-runs to confirm.

### 4. Measure answer faithfulness  **[U]**
- [ ] Run `python -m src.brain.answer_eval` (claim-by-claim judge). Confirm grounded answers stay faithful
      to cited sources and that out-of-scope questions correctly get the "not found" banner.

### 5. Verify collection routing  **[C] inspect, [U] spot-check**
- [ ] Confirm the specialist picker sends an SEO question to the Marketing specialist, an AD/DNS question
      to IT, etc. (routing drives which collection is searched first; wrong picks still answer via the
      whole-KB fallback, just less precisely). Check the picker in `src/llm/specialists.py` /
      `src/llm/assistant.py`; spot-check a few live questions.

### 6. Automate freshness  **[C] wire, [U] enable**
- [ ] Schedule `python -m src.brain.ingest --due` (see `src/brain/scheduler.py::run_due`) as a recurring
      task so sources refresh on their own `refresh_every` cadence instead of manual runs. Git sources are
      near-free to re-check (ETag/hash); web sources cost a polite crawl.

### 7. Chunk-quality pass (optional)  **[C]**
- [ ] Spot-check low-value chunks: section/landing/`_index` pages, changelog dumps (e.g. schema.org
      `releases.html`, `full.html`), thin crawled pages. Consider excluding or down-weighting. The
      per-doc diversity cap (`MAX_PER_DOCUMENT=2`) already limits damage.

### 8. Only then — add more sources
- [ ] Add sources **only where the eval shows a gap**, each with a verified open licence. Rejected
      candidates and why are recorded in the session memory (Arch Wiki = Anubis bot-block, Linux
      man-pages = mixed per-page licence, Bing Webmaster help = no open licence).

---

## Reference

**Pipeline:** `sources.yaml` → ingest (fetch → extract → chunk → embed) → `brain.chunks` (pgvector)
→ question embed → hybrid search by collection → top-k passages → model answers + cites.

**Key files**
- Registry: `data/brain/sources.yaml` · sync+ingest: `src/brain/ingest.py`, `src/brain/sources.py`
- Retrieval: `src/brain/retrieve.py` (tuning constants here) · answer/grounding: `src/brain/answer.py`
- Routing: `src/llm/specialists.py` (specialist → collections) · chat path: `src/jobs/chat_runner.py`
- Refresh: `src/brain/scheduler.py`, `src/brain/freshness.py`
- Eval: `data/brain_eval/questions.yaml`, `src/brain/evaluate.py` (retrieval), `src/brain/answer_eval.py`
  (faithfulness)

**Commands**
```
python -m src.brain.ingest            # sync registry + ingest all enabled sources
python -m src.brain.ingest --source <slug>
python -m src.brain.ingest --due      # only sources past their refresh_every (scheduler path)
python -m src.brain.evaluate          # retrieval MRR (add --mode vector|keyword|hybrid)
python -m src.brain.answer_eval       # end-to-end answer faithfulness
```

**Current tuning constants** (`src/brain/retrieve.py`, measured 2026-10-15 on ~2 sources — re-tune):
`MIN_SIMILARITY=0.49` · `VECTOR_WEIGHT=1.0` · `KEYWORD_WEIGHT=0.0` · `EXACT_WEIGHT=1.0` ·
`MAX_PER_DOCUMENT=2` · `MAX_PASSAGES=8` · `TOKEN_BUDGET=3000`.
