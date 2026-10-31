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
      _2026-10-30: `crawl_depth` changed 1 → 2 in `sources.yaml` ([C] done)._
      _2026-10-31: first re-ingest aborted at 236/300 (Ollama dropped). **[U] re-ran, now completed**
      (partial — hit the 300 `max_pages` cap, 2 transient `RemoteDisconnected` failures). Index +
      vacuum content now present: `indexes-intro.html` (11.1, shows `CREATE INDEX`),
      `runtime-config-vacuum.html` (19.10). The **canonical `sql-createindex.html` / `sql-vacuum.html`
      command-reference pages are still beyond the 300 cap** — eval now accepts the ingested intro/config
      pages; raise `postgresql-docs` max_pages (~450) later for the canonical reference pages._

### 2. Build the eval question set  **[C] draft, [U] review**
- [x] Expand `data/brain_eval/questions.yaml` from DNS-only to **~2–3 questions per source** across all
      three collections, plus `out_of_scope: true` questions to exercise the evidence gate. Schema:
      `id`, `question`, `docs: [url-ending]`, optional `facts: ["term|alt"]`.
      _2026-10-30 ([C] drafted): now **129 questions** (107 en / 22 hi-Latn), 5 out-of-scope, covering
      all 34 sources. No duplicate ids; YAML validated._
- [x] **[U] review:** the 32 sources added 2026-10-30 have **best-effort `docs:` endings** — run
      `python -m src.brain.evaluate` and correct any ending that doesn't match an ingested document URL
      (the `facts:` terms should hold regardless). Do this after the re-ingest in item 1.
      _2026-10-31 slug audit ([C] done): cross-checked all 27 misses against actual `brain.documents`
      URLs. Corrected 9 `docs:` endings that were guessed wrong (content present, retrieves rank 1):
      `it-adds-fsmo` → `understand-fsmo-roles`/`manage-fsmo-roles`, `it-adds-funclevel` →
      `active-directory-functional-levels`, `it-net-ipam` → `ipam-top`, `it-adds-promote` →
      `install-active-directory-domain-services--level-100-`, `dev-http-301` → `redirections`,
      `mkt-bing-call` → `web-search-client-library-*`, `it-docker-network`/`-volumes` +
      `docker-storage-networking-and-logging`, `it-ps-pipeline` → `04-pipelines`. These flip to hits on
      next eval (~+7 R@8)._

### 2b. Coverage fixes from the 2026-10-31 audit  **[C] done, [U] re-ingest**
The remaining misses were **coverage**, not measurement — target pages weren't ingested because
`max_pages` head-truncates the alphabetically-sorted file list ([ingest.py](../src/brain/ingest.py) `prepared.files[:max_pages]`), cutting late-sorting subtrees. Raised caps / narrowed globs in `sources.yaml`:
- `kubernetes-concepts` max_pages 120 → **200** (tree is 187; unlocks `workloads/`, `services-networking/` → pods, service, deployment).
- `fastapi-docs` 80 → **160** (tree 156; unlocks `tutorial/` → path-params, body).
- `ubuntu-server-docs` 100 → **260** (tree 255; unlocks `how-to/security/openssh-server`).
- `powershell-docs-conceptual` include narrowed to `learn/**` (41 files, was 638 at cap 120 never reaching `learn/`), max_pages → 60. Unlocks deep-dives (arrays, string-substitutions) + `ps101/04-pipelines`. **Tradeoff:** drops `developer/`/`install/`/`gallery/` reference (peripheral to admin scripting).

**[U] re-ingest the 4 changed sources + postgres**, then re-evaluate.

**Known-remaining gaps (by decision 2026-10-31 — leave as expected misses):**
- MDN JS (1,343 pages) / CSS (1,285) kept at cap 150 — reaching `Promise`/`Optional_chaining`/`Array.map`/`position` would ~double the KB. Misses: `dev-js-promise`, `dev-js-optional-chaining`, `dev-js-array-map`, `dev-css-position`.
- WDAC + Credential-Guard-overview aren't in `windowsserverdocs/security` (fully ingested, 105 files) — they're in the Windows **client** security docs (new source, needs licence check — deferred). Misses: `it-sec-wdac`, `it-sec-credguard`.
- Ranking misses (correct page present + correct slug, ranks >8): `dev-dj-models`, `mkt-schema-add` — kept as real retrieval signal.

### 2c. Result log (2026-10-31)
| Stage | R@8 | MRR | notes |
|---|---|---|---|
| Baseline (morning) | 77–78% | 0.62 | 27 missed |
| After slug fixes (pages already ingested) | 84% | 0.68 | +postgres partial re-ingest |
| After k8s / fastapi / ubuntu / powershell re-ingest | **92%** | **0.75** | EN 91 / Hinglish 95; 10 missed |

One more slug fix applied post-92%: `it-k8s-pod` → `pods/_index` (k8s section pages are `_index.md`, so the
URL ends `/pods/_index` not `/pods`; the page retrieves rank 1 at 0.82 — was scored a miss only on the slug).
Expect **~93%** on the next eval.

**Residual after that (9, all expected):** `it-sec-credguard` + `it-sec-wdac` (no source — L8/new-source task);
`dev-js-promise` / `-optional-chaining` / `-array-map` / `dev-css-position` (MDN 150-cap, by decision);
`dev-dj-models` / `dev-fa-body` / `mkt-schema-add` (ranking misses — correct page present, outranked by an
index/features page).

**Eval-quality note:** after the ubuntu re-ingest, `oos-linux-root` (0.66, "Enabling the root account") and
`oos-vlan` (0.66, NPS VLAN config) are now **genuinely covered by the KB** — they are no longer truly
out-of-scope and should be reclassified (moved to in-scope questions or dropped), not counted as gate leaks.
Only `oos-instagram` (0.57, Remarketing) is a weak spurious match. This confirms the MIN_SIMILARITY HOLD:
the gate stats are contaminated by mislabeled OOS questions, not a bad threshold.

### 3. Re-measure retrieval & re-tune the gate  **[U]**
- [ ] Run `python -m src.brain.evaluate` (and `--mode vector|keyword|hybrid`) to get MRR per mode on the
      34-source corpus.
- [ ] **Re-tune `MIN_SIMILARITY`** (currently `0.49`) and the fusion weights (`VECTOR_WEIGHT=1.0`,
      `KEYWORD_WEIGHT=0.0`, `EXACT_WEIGHT=1.0`) in `src/brain/retrieve.py` — all measured 2026-10-15 on
      ~2 sources, and the code itself says to re-tune when sources change. **[C]** proposes new values
      from the eval numbers; **[U]** re-runs to confirm.
      _2026-10-31: **HOLD** — gate stays at 0.49. The 3 OOS leaks (oos-vlan 0.663, oos-instagram 0.571,
      oos-linux-root 0.535) are all above the proposed 0.53, so raising to 0.53 fixes none; refusing them
      needs ≥0.57, which would start refusing in-scope (min 0.542). The leaks are semantic overlap
      (e.g. VLAN-via-NPS genuinely is in the KB), not a threshold problem. Revisit after coverage settles._

### 4. Measure answer faithfulness  **[U]**
- [x] Run `python -m src.brain.answer_eval` (claim-by-claim judge). Confirm grounded answers stay faithful
      to cited sources and that out-of-scope questions correctly get the "not found" banner.
      _2026-10-31 ([U] ran, 129 Q): **grounded 94% PASS** (target ≥90%, strict 93%), **latency median 18.8 s
      PASS** (p90 24.3 s), **no-evidence 2/5 FAIL** — but 2 of the 3 "leaks" (`oos-vlan`, `oos-linux-root`)
      are now genuinely covered by the KB (mislabeled; see §2c reclassification); only `oos-instagram` (0.57)
      is a true leak. `right source cited 0.855`, `claims supported 0.845`, `judge quotes verified 0.925`.
      The 8 `NO` claims are grounding-precision (model states correct domain facts the cited passage lacks,
      e.g. FSMO roles, aging/scavenging) — `verify.py` already hides unsupported lines in production.
      `wrong_source` list ≈ the documented retrieval gaps (MDN-capped / no-source / ranking misses).
      One latency anomaly: `mkt-ga-event` 1444 s (single Ollama stall, not reproducible; didn't affect p90)._
- [ ] **[U] follow-up:** reclassify `oos-vlan` + `oos-linux-root` as in-scope (they're covered now), and
      decide `oos-instagram` (keep as the one true gate probe). Then the no-evidence metric is honest.

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
