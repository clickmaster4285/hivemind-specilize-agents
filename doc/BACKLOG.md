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

### L4. Enforce local-only (no cloud) — ✅ done 2026-11-02

- Removed: `MODEL_CLOUD` / `OVERFLOW_WAIT_SECONDS` / `REMOTE_LLM_*` settings, the OpenAI-compatible remote
  backend in `LLMClient`, the `cloud` role and queue overflow in `resolve_model_plan`, and `allow_overflow`
  end to end (API + frontend). `:cloud` rows are no longer forced "available"

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

### L6. Cleanup — ✅ done 2026-11-02

- `deepseek-r1:1.5b` is no longer installed (Ollama has only `qwen3.5:9b` + `qwen3-embedding:0.6b`)
- Old registry rows: the startup sync now deletes rows for models Ollama no longer has (they return if the
  model is pulled again); an Ollama outage skips this, so the list is never emptied

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

### L10. Documents: direct export, "Download as", section-by-section writer — ✅ done 2026-11-02 (fixes ISSUES_LOG O8)

**Problem:** "make this a PDF" re-sends the earlier answer (cut by the history caps) to the agentic worker,
which must re-type it as one JSON tool call — the 9B model truncates it, so the file is short or missing.

- [x] **Export, not regenerate:** an answer already in the chat is converted straight to the format with the
      existing converters (`src/tools/answer_export.py` → `doc_builder.py` writers) — no LLM, nothing truncated
- [x] **"Download as ▾"** under every answer: md · txt · docx · pdf · pptx · html (`POST /api/jobs/{id}/export`)
- [x] "This / that / the above as a PDF" in chat: `create_document` gets `source=previous_answer` (the model
      decides) → the chat's previous answer is read in full from its job events and exported
- [x] Markdown tables and code blocks survive: real Word tables, HTML `<table>`/`<pre>`, aligned rows in PDF/txt
- [x] New documents (not an export): `src/tools/doc_writer.py` — outline (small JSON: topic title, format,
      sections + what each covers) → each section written on its own, streamed, knowing the whole plan → checked
      (empty / repeats an earlier section / open code fence) and rewritten once → same converters as export.
      Spreadsheets (xlsx) and unusable outlines still go to the agentic worker
- [x] File names follow the topic, not the request: the document's own title, else a short model-made topic
      title (`topic_title`), else the question — never "make_this_a_pdf.pdf" or "document.docx"

### L11. Token usage tracking — ✅ done 2026-11-02

Ollama returns `prompt_eval_count`, `eval_count` and `total_duration` on every call; we now keep them.
- [x] One `llm_usage` row per model call (`src/llm/usage.py`): job, user, step, model, tokens in/out, ms. The
      worker binds each job (`job_usage`, a context variable — post-answer extras threads carry it); the step is
      read from the call site (chat_runner, doc_writer, suggestions, quiz, deep_research …), so callers label
      nothing. A call stopped mid-answer has no counts → estimated (chars/4, wall time), flagged. Calls outside
      a job (evals, benchmarks) are not recorded; recording can never fail an answer
- [x] Per answer: a `usage` event just before `session_done` → "1.2k in · 850 out · 14 s" in the action bar,
      hover = split by step (replayed from history after a reload)
- [x] Per chat: the answers added up, under the message bar ("This chat: …")
- [x] Per user: Settings → **Usage** (`GET /api/usage?days=`), Today / 7 / 30 / 90 days, totals + per user
      (admins see everyone, members themselves) + per step
- Note: Ollama counts only newly evaluated prompt tokens — a reused (cached) prompt prefix is not counted again,
  so "in" can be lower than the real prompt size. L12's context meter must measure the prompt it builds instead

### L12. Context awareness: meter, auto-compaction, new chat — ✅ done 2026-11-02 (fixes O9)

- [x] Memory rebuilt **server-side** from the chat's own jobs, in full (`src/jobs/chat_memory.py`), not the
      browser's last 10 turns cut to 6,000 chars; budgeted in **tokens**: `CONTEXT_HISTORY_SHARE` (0.35) of
      `LLM_NUM_CTX` → 2,867 tokens at 8K; the rest is for instructions, sources and the answer
- [x] Auto-compaction: past the budget the oldest exchanges are folded ONCE into a running summary on the chat
      (`chat_threads.summary` / `summary_through`) until the verbatim part is half the budget; later turns reuse
      it. The prompt gets [summary] + recent turns word for word; a summary failure drops the oldest turns
      instead. Summaries of chats bigger than the model are built piece by piece. A feed note says when it happens
- [x] Memory meter under the message bar (chat size vs budget; past 100% = "full · summarizing")
- [x] At 80%: banner "This chat is getting long" → **Summarize & continue in a new chat** (new chat starts with
      the summary as its memory, shown as its first card) / **Save summary** (.md) / dismiss for this chat
- [x] The summary turn always leads the prompt (`normalize_conversation`, tool-calling messages as a system note)

### L13. Marketing / SEO tools — ✅ done 2026-11-02 (fixes O11; assistant eval run pending)

"Maturity / improvement" means **both** (user, 2026-11-02): score how mature another website's SEO and
content are and say what to fix, AND improve our own written content over several drafts.

- [x] **`site_audit` tool** (`src/tools/site_audit.py`): polite bounded crawl of a PUBLIC site (`src/brain/web.py`:
      robots.txt, 1 req/s/host, same host only; every redirect hop of the start address must resolve to a public
      address — private/loopback/link-local refused) — sitemap pages first, then links, up to `SITE_AUDIT_MAX_PAGES`
      (25). Links that are not web pages (PDFs) are skipped, not counted as broken
- [x] 16 page checks + 4 site checks (title / description present, length, unique; one H1; heading order; thin
      pages vs the site's own median; alt text; canonical; lang; viewport; noindex; Open Graph; structured data;
      HTTPS; robots.txt; sitemap; broken pages). A check's score = share of pages passing → areas Technical /
      On-page / Content / Sharing → **maturity 0-100 + level** (Beginner < 40 ≤ Developing < 60 ≤ Established
      < 80 ≤ Advanced). No model in the score: the same site scores the same
- [x] The model reads the failing checks + page titles + the home page text (as data) → "Fix first" (≤ 5, by
      impact, with rewritten titles/descriptions) + "Content maturity" (audience, offer, CTA, trust signals)
- [x] **`write_content` tool** (`src/tools/content_writer.py`): JSON brief (kind, audience, reader, keywords, length,
      CTA, language, web?) → draft (streamed) → checks without a model (length ±25%, keywords, main keyword in title
      and opening, Flesch reading ease vs the reader, long sentences, sub-headings in long copy, repeats, meta
      title/description length) + an editor's rubric (5 × 1-10 + fixes) → revise, up to `CONTENT_MAX_ROUNDS` (2).
      Stops when all checks pass and the editor gives ≥ 8, or a revision scores no better. Best draft wins; a
      "How this was improved" table shows every draft's score
- [x] Improve an existing page: `write_content` with a URL scores the page's current copy first ("Current page"),
      then the drafts improve it
- [x] Offered to every reading specialist (Marketing included) via `seen_tools`; the web-search nudge no longer
      swallows messages with an explicit address (http(s)://, www.) so the model can pick site_audit
- [x] 7 assistant eval cases (`data/assistant/eval.yaml`, incl. "3 slogans" → direct answer); 17 tests
- Measured on example.com (no model): 1 page, 37/100 Beginner — no robots.txt, sitemap, description, H1,
  canonical, Open Graph or structured data; title 14 chars

### L15. SEO suite — Screaming Frog / Semrush capabilities — P1, P3-P5 done; P2 on hold (fixes O12)

Research + capability matrix: [SEO_TOOLS.md](SEO_TOOLS.md). Order (user, 2026-11-02): SEO capabilities first,
then skills (L14) and sub-agents.

- [x] **P1 Crawler depth** — done 2026-11-02: the fetcher now keeps redirect hops, response time, size and headers
      (`WebResponse`); the audit builds a link graph → click depth, inlinks, orphan pages (in the sitemap, linked
      from nowhere), links to redirects / broken pages, redirect chains, link text reused for ≥3 different pages,
      near-duplicates (5-word shingles ≥ 90%, SF's default); canonical to a redirect/broken URL, mixed content,
      clean URLs (lowercase, no _, ≤ 115 chars), security headers, images without width/height, server response
      ≤ 0.8 s. 27 page + 6 site checks in 6 areas (+ Links, Performance). Every page and redirect in
      `seo-crawl-<host>.xlsx`. Real run python.org (10 pages): 79/100 Established — one description for all
      pages, several H1s, no canonical, a menu link through a redirect, no width/height on images
- [ ] **P2 Performance** — ON HOLD (user wants to discuss first): options A-D compared in SEO_TOOLS.md (recommended: PageSpeed API for key pages + our crawl proxies)
- [x] **P3 Keywords & SERP** (approximate is fine — user) — done 2026-11-02: `src/tools/serp.py` (SearXNG results with
      positions, related searches, autocomplete ideas two levels deep, top pages read in parallel, shared two-word
      phrases, difficulty estimate = share of top-10 titles targeting the keyword + share of home pages) →
      `keyword_research` tool: table per keyword (your rank ≈, difficulty), who ranks, what top pages cover (median
      length, phrases), ideas, the model's intent + what to target. States that it is approximate, no volume. Live
      test: 2 keywords + a domain in 8 s (results come from SearXNG's region — UK)
- [x] **P4 Competitors & writing** — done 2026-11-02: `compare_sites` tool (named competitors or the sites ranking
      for a keyword; all audited in parallel, page budget shared; scores side by side; checks they pass and you
      fail; content gap = phrases on ≥2 pages of each competitor, minus its menu/footer and brand, on ≥ half the
      competitors, nowhere on your site; model plan). `write_content` reads the top pages for its main keyword:
      median length + shared phrases go into the brief and a "Covers what top pages cover" check. Live:
      python.org vs ruby-lang.org in 12 s
- [x] **P5 Projects** — done 2026-11-02: every audit saved per user (`seo_audits`); the next audit of the same site
      shows score/area changes and fixed / new / worse / better checks; "track it weekly" → `seo_projects` row,
      re-audited by the `seo-tracker` thread (hourly check, crawl + checks, no model); `seo_projects` tool lists
      sites, trend, latest changes, stops tracking
- [x] 6 more assistant eval cases; 27 tests (search faked on 127.0.0.1, in-memory database)
- Not possible locally: search volume, traffic estimates, backlinks (paid API — user's choice); Search Console
  (OAuth) and JS rendering later
- [x] **P6 search market + menu text (ISSUES_LOG O14) — done 2026-11-03:** `src/tools/market.py` decides the
      market per request: the user's words (tool argument `market`: PK / GB / worldwide) → the website (declared
      locale `<html lang>` / `og:locale` / own `hreflang`, else its country domain; `.com`/`.io` say nothing) →
      ask once (the user's own country, from the browser time zone, offered first). Sent to SearXNG as
      `language=en-PK` (`all` = worldwide); search language follows the keyword's script (Urdu keyword → `ur-PK`);
      the report says which market and why. `write_content` does not stop to ask: worldwide, said so. Page text
      now leaves out `nav`, `aside`, the site header/footer and ARIA navigation landmarks (content gap, word counts).
      Country data from Babel's CLDR (no lists of ours). Phone numbers / currency not used (would need lists)

### L14. Skills — reusable instruction packs — in progress 2026-11-03 (fixes ISSUES_LOG O13)

A **specialist** is the domain (who answers: IT, Development, Marketing). A **skill** is a repeatable task
(how to do one job: "Incident report", "Campaign plan", "Monthly SEO check"). Modelled on claude.ai → Customize →
Skills (Yours / Discover, categories, "+" to add, create, upload). Skills are for MulteAgent's users — general
skills like those, not developer skills for this repo (S4 dropped, user's decision 2026-11-03).

Decisions (user, 2026-11-03): **both** team skills (admin-managed, everyone sees them) and personal skills (only
the owner sees them); **no automatic picking** for now — a skill runs only when picked or typed as `/name`.

- [x] **S1 model + store + catalog — done 2026-11-03:** skills use the open **Agent Skills format** (`SKILL.md`: YAML front matter
      `name` + `description`, optional `allowed-tools`, `metadata` with title / category / specialist / inputs,
      then the instructions), so skills from elsewhere can be uploaded and ours exported. **Discover** = the built-in
      catalog, files in `skills/<name>/SKILL.md`; **Yours** = table `skills` (owner = a user, or none = team).
      "+" copies a catalog skill into Yours (admin may add it for the team). Categories come from the skills
      themselves. API `/api/skills` (list, catalog, create, upload, update, delete, add, download). Starter
      catalog: 17 skills (IT & security 5, Engineering 4, Marketing 5, Writing 2, Operations 1), our own text.
      `src/llm/skills.py`, `web/routers/skills.py`, table `skills`. A skill's instructions may use
      SKILL_CONTEXT_SHARE (20 %) of LLM_NUM_CTX; tools/specialists MulteAgent lacks are dropped on upload
- [x] **S2 use in chat — done 2026-11-03:** type `/` in the message box (or "+" → Use a skill) → the user's
      skills, filtered as typed (↑ ↓ Enter/Tab, Esc); the message starts with `/name`. The worker reads it
      (`skills.for_job`; a `/path` that is no skill of the user's stays a normal message), the skill's specialist
      answers (else the usual pick), the skill's instructions join its prompt and only the skill's tools that the
      specialist may use are offered (`skills.apply` — one helper `_answerer` now resolves the specialist for
      chat, deep and options). Missing inputs → the model calls ask_clarification; the follow-up repeats the
      typed `/name …` so the skill goes on. Status lines "Skill · …" and "Specialist · set by the skill".
      Eval cases with `skill:` (4)
- [x] **S2 fix (ISSUES_LOG O15) — done 2026-11-03:** `/` showed nothing until a skill was added to Yours — built-in skills now
      run straight away (yours first, then the team's, then the built-in one; a copy in Yours, even switched
      off, replaces the built-in); the menu matches names starting with what is typed, then names/titles
      containing it; the message feed no longer re-renders on every keystroke
- [x] **S3 Customize page — done 2026-11-03:** Settings → Skills (everyone): **Yours** = Team and Created by you,
      each with On, Edit (View for a member on a team skill), Download (SKILL.md), Delete; **Discover** = search,
      category pills with counts, View (read-only preview) and "+ Add" ("Added ✓" once in Yours). New skill
      (form: title → /name, description, category, who answers, inputs one per line, tools, instructions) and
      Upload SKILL.md. Admins tick "Add for the whole team" to add/upload for the team. Every change reloads
      the `/` menu. `SkillsSection.tsx`, `SkillModal.tsx`, `useSkills` (save / setEnabled / remove / upload / add)
- [ ] Tests (offline) + assistant eval cases — left: a unit test for the worker's `clarify_query` wrapper
      (keeps `/name` in the clarify follow-up)
- Later: automatic picking from the descriptions (measured with an eval first); skill files beyond SKILL.md
  (scripts, references) are not run — MulteAgent never executes uploaded code

### L16. Security hardening — planned 2026-11-04 (fixes ISSUES_LOG O16)

Threats: a stolen login token, password guessing, request floods, a malicious uploaded file, XSS, clickjacking.
DDoS proper is stopped at the network (firewall / reverse proxy); the app limits what one client can do.

Decisions (user, 2026-11-04): **plain http on the LAN** (HTTPS with a local certificate was tried and dropped —
see S1b); **ClamAV in Docker, fail closed** (scanner down → upload
refused). AES: nothing secret is stored today (passwords bcrypt, refresh tokens hashed, SSH logins never saved),
and encrypting a token JS cannot read adds nothing — S5 starts when the app first stores a secret.

- [x] **S1 sessions — done 2026-11-04:** 15-minute access token (`JWT_EXPIRE_MINUTES`) + rotating refresh token
      (`REFRESH_TOKEN_DAYS`, 7), both `HttpOnly` cookies (`ma_access` path `/api`, `ma_refresh` path `/api/auth`),
      `SameSite=Strict`, `Secure` when the request came over https. Table `refresh_tokens` keeps only SHA-256
      hashes; one family per sign-in — a spent token shown again (later than REFRESH_REUSE_GRACE, 10 s: two tabs
      refreshing together) revokes the family. `/api/auth/refresh`, `/api/auth/logout` (revokes; access tokens of
      a revoked family are refused at once). Disabled users refused on every request. No more CORS: the browser
      calls `/api` on the page's own origin (Next.js rewrite, proxy timeout 300 s, SSE heartbeat 15 s); writes
      whose Origin is another site → 403. Startup refuses the dev default or a <32-byte `JWT_SECRET`.
      Frontend: no token in `localStorage`; `apiFetch` refreshes once on a 401 (one refresh for parallel calls)
      and repeats the call. `src/core/auth.py`, `web/routers/auth.py`, `tests/test_sessions.py`
- [x] **S1b network — done 2026-11-04, HTTPS dropped (user's decision):** a local certificate authority
      (Caddy `tls internal`) meant installing its root certificate on every PC — too much. Options weighed: own
      domain + Let's Encrypt (DNS challenge), Tailscale, Group Policy push, plain http — **plain http on the
      LAN** chosen. Accepted risk: someone sniffing the LAN can read traffic, passwords included; cookies stay
      `HttpOnly` + `SameSite=Strict` (`Secure` is set by itself if HTTPS is added later). Kept: the API listens on
      127.0.0.1 only — the LAN reaches it through the UI's `/api` rewrite (port 3000); port 8787 is closed to
      the LAN. Revisit with a domain + Let's Encrypt if the app leaves the trusted LAN
- [x] **S2 abuse limits (ISSUES_LOG O36) — done 2026-11-11** (limits approved by the user the same day;
      `src/core/abuse.py`, `web/limits.py`, `frontend/server.mjs`, `tests/test_abuse_limits.py`):
      - **Client address:** every request comes from the Next.js rewrite on 127.0.0.1, so the address is read
        from `X-Forwarded-For`, trusted only when the direct peer is 127.0.0.1 (check first that the rewrite
        sends it). Assumption (user): one user per PC, so one address = one person
      - **Login per account:** 5 failures free; then back-off 1 s, 2 s, 4 s, 8 s … capped at 30 s; 10 failures
        within 15 min → account locked 15 min. A successful login clears the count
      - **Login per address:** 20 failures within 15 min → logins from that address blocked 15 min (catches one
        PC trying many usernames)
      - **Unknown usernames:** counted like real accounts; same message and timing (does not reveal which
        usernames exist)
      - **Requests per signed-in user:** 120/min average, burst 60 (a page load is ~10–20 calls).
        `daily_job_quota` (50 model jobs/day) stays as is
      - **Requests without sign-in, per address:** 60/min (login page, SSO status)
      - **Request body cap:** 30 MB for any request (largest real upload: a 25 MB artifact file + form
        overhead), checked from `Content-Length` and again while the body streams in → 413
      - **Answer:** 429 + `Retry-After`; the login page shows "Too many attempts — try again in N min"
      - **Storage:** failed logins in a database table (an API restart does not reset an attacker's count);
        request rates in memory (the API is one process)
      - **Logging:** each lock → a warning in `logs/api.log` (S6 shows them to admins later)
      - **Built as:** Next's rewrite adds no `X-Forwarded-For` and passes on a forged one (checked with its
        own proxy code), so the UI now runs a small custom server (`npm run dev` → `node server.mjs`) that
        overwrites the header with the socket address. Every failed sign-in is logged (INFO) with account and
        address. The cross-site write check became plain ASGI too: a `@app.middleware` turned the body
        cap's 413 into a 400
- [ ] **S2b unlock + CAPTCHA (ISSUES_LOG O39) — plan proposed 2026-11-11, waiting for the user's approval:**
      - **Why both:** after S2 a lock can only run out (15 min), and 10 wrong passwords lock *any* account, so
        someone can lock a colleague out on purpose. A CAPTCHA before that point makes each further try need a
        person; the unlock button undoes a lock at once
      - **Unlock (admin):** Settings → Users shows a "locked" badge on a locked account (until when, how many
        failures) and an **Unlock now** button in its form; a **Blocked computers** list (address, until when)
        with Unlock. Unlock deletes that key's rows in `login_failures`. Routes: `GET /api/auth/users/locks`,
        `DELETE /api/auth/users/{id}/lock`, `DELETE /api/auth/users/locks/address/{address}`. Each unlock is
        logged (who, what) for S6
      - **CAPTCHA kind — image text, drawn by the API with Pillow** (already installed; its own font, no
        files): 6 characters from an alphabet without look-alikes (no 0/O, 1/I/l), each turned and shifted,
        with noise lines and dots; the answer is not case-sensitive. "New image" button. Nothing leaves the LAN
      - **Not chosen:** proof-of-work CAPTCHAs (Altcha, mCaptcha, Friendly Captcha): the browser's
        `crypto.subtle` exists only on https or localhost pages, not on the plain-http LAN address, and they
        cost a script CPU time, not a person — S2 already limits the rate. Google reCAPTCHA / hCaptcha /
        Cloudflare Turnstile: outside services (send visitors' data out, need internet)
      - **When it is asked:** **sign-up always**; **login after 3 failures** (account or address, within the
        15-min window) — before the S2 back-off starts at 5, so a normal typo or two never sees it
      - **How it works:** `GET /api/auth/captcha` → `{id, image}` (a PNG as a data URL). The answer is kept in
        memory (one API process) for 5 min and can be checked **once** (right or wrong, then it is gone); at most
        a fixed number open at a time (oldest dropped), so it cannot fill memory. Login / sign-up send
        `captcha_id` + `captcha_answer`. A login that needs one and lacks it (or has it wrong) → 400 with
        `captcha_required: true`; the password is not checked and the try is not counted
      - **Frontend:** a small `Captcha` part in the login card (image, answer box, New image), shown when the
        API asks for it, always in the sign-up form
      - **Tests (offline):** an answer works once and runs out; login asks after 3 failures; sign-up refuses
        without one; unlock clears an account and an address; members cannot unlock
      - **Numbers to approve:** 6 characters, valid 5 min, login CAPTCHA from the 3rd failure
- [ ] **S3 uploads:** type from the file's own bytes (not the declared MIME), decoded and re-encoded with
      Pillow (drops hidden payloads and metadata, pixel limit against decompression bombs), virus scan (ClamAV)
      on uploaded files; skill uploads size-capped
- [ ] **S4 headers + XSS:** CSP, `X-Content-Type-Options: nosniff`, `frame-ancestors 'none'`, `Referrer-Policy`,
      HSTS over HTTPS; links in answers limited to http(s)/mailto; downloads sent as attachments
- [ ] **S5 encryption at rest (AES-256-GCM):** for secrets the app stores — decided per item (see questions)
- [ ] **S6 security log:** failed logins, locks, refresh-token reuse, rejected uploads — visible to admins
- [x] **S7 self sign-up (ISSUES_LOG O38) — done 2026-11-11** (design approved by the user the same day;
      `src/core/signup.py`, `src/core/accounts.py`, `GET/POST /api/auth/signup`, `/api/auth/users/invites`,
      `/api/auth/users/signup-settings`, `AuthScreen` sign-up mode, `InvitesPanel`, `tests/test_signup.py`):
      - "Create account" on the login page: username, email, phone, password
      - Two ways in: **open sign-up** for anyone who can open the page (the LAN), and an **invite link** an
        admin creates and shares
      - New accounts **wait for an admin's approval** (status `pending`) and can use nothing until approved
        (Settings → Users)
      - Protected by the S2 limits; the S2b CAPTCHA covers sign-up too
      - Google / Microsoft sign-in (OAuth 2.0 / OpenID Connect): **later**, once there is HTTPS (their
        sign-in only returns to https:// or localhost addresses). Clerk not used (accounts would live on
        an outside service; needs internet)
      - **Approved details:** phone optional; username ≥ 3, password ≥ 8, email valid and unused (as the
        admin's user form). Sign-in while pending → "waiting for an admin's approval". Admin switch "open
        sign-up" in Settings (on by default). Invite: optional email + modules, one use, **7 days**; works
        when open sign-up is off; **an invited account waits for approval too** (its modules apply on
        approval). At most **5 sign-ups per address per day**. Approve = active; reject = deleted. Table
        `user_invites` (token hash only), status value `pending`
      - **Built as:** the account rules (username, password, email) are one module now, shared by the admin
        form, `seed_users` and sign-up. Settings → Users: "Waiting for approval" (count) → Review opens the
        user form with Approve / Reject; "Sign-up and invites": the open switch, Invite someone (email +
        modules) → the link is shown once. On plain http the browser has no clipboard API, so Copy selects
        the link for Ctrl+C. `POST /api/auth/register` (always 403) removed

### L17. NVR camera list — in progress 2026-11-04 (fixes ISSUES_LOG O17; before L16 S2, user's order)

"Tell me all the cameras on the NVR at …" → a table: channel, camera name, IP, model, online/offline (+ .xlsx).
Read-only requests to the recorder's own API — never the web pages, never a setting changed.

- [x] **N1 readers — done 2026-11-04** (`src/servers/nvr.py`, `tests/test_nvr.py`; Hikvision field names checked
      live in N3, Dahua's when a Dahua recorder is at hand): Hikvision ISAPI (`/ISAPI/System/deviceInfo`, `/ISAPI/ContentMgmt/InputProxy/channels`
      + `/status`) and Dahua HTTP API (`magicBox`, `ChannelTitle`, `LogicDeviceManager`), HTTP Digest login;
      which API a device speaks is found by asking it (the one that answers with a login request), not by a
      brand list. Parsers tested offline on sample replies
- [x] **N2 login — done 2026-11-04:** Settings → Servers → Type "Camera recorder": address, port 80, user,
      password; adding it reads the camera list once (the test login). `server_vault` logins have a `kind`
      (ssh | nvr) — the SSH tools only ever see ssh ones; recorders must be on a private address (`check_private`)
- [x] **N3 tool — built 2026-11-04:** `nvr_cameras` (IT specialist, admins via the login), recorder summary +
      table (channel, camera, IP, model, protocol, online/offline) + .xlsx; 3 eval cases. **Live 2026-11-04:**
      DS-7732NI-M4 (firmware V5.04.065) at 192.168.190.248 → 27 cameras with names, IPs, models, online, 0.7 s.
      Left: the assistant eval run (user)
- [ ] **N4 ONVIF:** profiles/video sources for other brands (WS-UsernameToken digest)

### L19. Devices — routers, switches and more, read-only — in progress 2026-11-04 (fixes ISSUES_LOG O19)

Settings → Servers becomes **Settings → Devices**: servers, camera recorders, routers / firewalls, switches.
User's decisions (2026-11-04): **read-only first** (changes later, confirmed, like servers); brands **MikroTik,
Cisco, TP-Link / D-Link / Netgear**.

- [x] **D1 Devices section — done 2026-11-04:** the tab, heading and every "Settings → Servers" in replies, tool
      descriptions and prompts (20 places) say Devices; internal names (`/api/servers`, `server_vault`) stay
- [x] **D1c module access + user page — done 2026-11-04 (user's request):** Settings → Users = the list and a **Create user**
      button; create and edit open one full-page form (replaces the edit modal and the inline Devices tick) with
      **module access**: Devices, Skills, Usage (`src/core/modules.py` — one entry per module; only modules the
      API can enforce). Admins have every module; new members get Skills + Usage (what members had), Devices off.
      Replaces `users.devices_access` with `users.modules_json`
- [x] **D1b more users — done 2026-11-04 (superseded by D1c):** the Devices tab for admins **and the users an admin gives access**
      (user's correction: not every user) — `users.devices_access`, off by default, switched on in Settings → Users;
      taking it away forgets that user's device logins at once. Each user sees and uses only the devices they
      added; **trusted** mode (changes without a click) stays admin-only (`ADMIN_ONLY_MODES` in
      `web/routers/servers.py`: 403 for a member, option hidden in the form)
- [x] **D2a MikroTik — built 2026-11-04** (`src/servers/netdev.py`, `tests/test_netdev.py`): Settings → Devices →
      "Router / switch"; adding it = one read (login, platform from the SSH banner `ROSSSH`, host key pinned);
      `network_device` tool in chat (IT specialist): name, model, version, uptime, CPU, memory, interfaces
      up/down/disabled, DHCP leases, ARP, last log lines + .xlsx; 3 eval cases. **Left: live check on the user's
      MikroTik** (best with a read-only RouterOS user: `/user add name=multeagent group=read password=…`)
- [ ] **D2 network device reader (rest):** read-only, a fixed command set per platform kept as data — MikroTik RouterOS
      (SSH `/system resource print`, `/interface print`, `/ip dhcp-server lease print`, `/ip arp print`, `/log`),
      Cisco IOS (`terminal length 0`, `show version`, `show ip interface brief`, `show interfaces status`,
      `show mac address-table`, `show arp`, `show logging`); the platform found from the device's own answer.
      TP-Link / D-Link / Netgear: their CLIs differ by model, so **SNMP read** (sysDescr, IF-MIB ports and status,
      BRIDGE-MIB MAC table) is the common path
- [ ] **D3 tool:** `network_device` in chat (status, ports, who is connected, logs) + .xlsx; eval cases
- Later: config backup, then confirmed changes

### L19b. Saved devices, full report, full pages — planned 2026-11-04 (ISSUES_LOG O21–O23)

User's decisions (2026-11-04): devices **saved, each user's own, encrypted** (AES-256-GCM, key only in `.env`
as `DEVICE_KEY`; protects database copies and backups — whoever has both the database and `.env` can still
decrypt); admins do not see others' devices; cameras may be read **directly with the NVR's login** (one try
each) when the NVR cannot relay; order: encrypted devices → full NVR report → full pages.

- [x] **D4 encrypted device store — done 2026-11-04** (`src/core/crypto.py`, table `devices`, `tests/conftest.py`
      gives every test its own in-memory store and key; the API refuses to start without a valid `DEVICE_KEY`;
      logout no longer forgets devices — the `DELETE /api/servers` route is gone): table `devices` (owner, kind, label, address, user, encrypted secret,
      host key, mode, platform); `server_vault` keeps its functions but reads/writes the table, decrypting only
      for the moment a tool logs in; logins stay until removed (no longer cleared at logout or after 8 hours);
      ciphertext bound to its owner and row (AES-GCM associated data) so it cannot be moved to another user
- [x] **N5 full NVR report — built 2026-11-04, live check pending** (`tests/test_nvr_report.py`; chat: summary +
      Channel / Name / IP / MAC / Model / Firmware / Status table; `nvr-report-<host>.xlsx` = NVRs, Cameras with the
      group row, Storage) (`src/servers/nvr_report.py`; columns kept as data: group,
      column, ISAPI address, field): NVR side `/ISAPI/System/deviceInfo` + `/status`, `/ISAPI/ContentMgmt/Storage/hdd`,
      `InputProxy/channels` + `/status`, stream settings via `StreamingProxy/channels/{N}01|{N}02`; each camera's
      own `/ISAPI/System/deviceInfo`, `Network/interfaces`, `status`, `time` + `ntpServers`, `Image/channels/1`
      with the NVR login, one try per camera, 8 cameras at a time (`nvr-report-…xlsx` shape): NVRs sheet (status, network, uptime, CPU, HDDs,
      camera counts), Cameras (device, network, system, main/sub stream, image settings), Storage (per HDD)
- [ ] **D5 full pages:** user form, agent editor and device form as full pages inside the app layout

### L20. Move to Ubuntu — started 2026-11-07 (ISSUES_LOG O24)

- [x] **One requirements file — done 2026-11-07:** `requirements-dev.txt` and `requirements-image-cu118.txt`
      merged into `requirements.txt` (pytest; torch 2.14.1+cu126 for the Pascal GPU, via the PyTorch index);
      README and image-error install hints updated
- [x] **Babel (O25) — done 2026-11-07:** `Babel` added so the API starts; `cryptography`, `numpy`, `psutil`,
      `urllib3` listed too (imported directly, previously only pulled in by other packages)
- [x] **Worker entry point (O26) — done 2026-11-10:** `worker_main.py` on the current modules (`src.core.config`, `src.jobs.worker`); password no longer printed
- [x] **Backup and restore (O27) — done 2026-11-10:** `backup.sh` / `restore.sh` — `pg_dump -Fc`, `.env`, `artifacts/`,
      `agents/*.json` in one dated archive in `BACKUP_DIR` (default `backups/`, git-ignored; user's choice
      2026-11-10), with a reminder to copy it off this machine; optional daily schedule; no encryption at first
      (user 2026-11-10: plain archive now, encryption later — treat the file like a password file)
- [x] **Docker plan revision (O28) — done 2026-11-10:** user decided hybrid stays; `api`, `worker`, `caddy` removed from compose (with `Dockerfile`, `deploy/Caddyfile`); `DOCKER_FULL_STACK_PLAN.md` rewritten as "what runs where" + what a full stack would need
- [x] **`start.sh` — done 2026-11-07:** Linux version of `start.ps1` (hybrid: db + SearXNG in Docker via
      `compose up --wait`, Ollama check, API + UI in the background with logs in `logs/`, health + LAN URLs;
      `--tools`, `--no-ui`, `--stop`). Says how to fix Docker permission / stopped services instead of using sudo
- [x] **`start.sh` first run (O29) — done 2026-11-10:** name a missing Docker credential helper (Docker Desktop's `credsStore`, left
      after it was removed) with the fix; skip the API when the database is down; quiet health polling
- [x] **`start.sh` health line (O30) — done 2026-11-10:** the inline Python had `\"` inside f-strings (syntax error)
- [x] **Fresh database setup (O31) — done 2026-11-10:** `python -m src.core.app_db prepare` runs the brain migrations and creates
      the logins/schema only when missing; `start.sh` calls it after the database is healthy
- [ ] **Stronger database owner password (O32):** user changes it in the database and `.env` (walk-through given)
- [x] **Separate test database (O33) — done 2026-11-10:** DB tests on `multeagent_test` (created by `tests/conftest.py`), not the live one; lock waits time out
- [x] **See what runs (O34) — done 2026-11-10:** brain refresh progress per file in `logs/api.log`; `./start.sh --status` snapshot
      (containers, Ollama + loaded model, GPU, API health/queue, UI, brain progress, last backup); `./start.sh --follow`
      live tagged output of API, UI, containers, Ollama
- [x] **Embedding model on the CPU (O35) — closed 2026-11-10:** on the CPU by design (`num_gpu: 0`; query 0.09 s), user confirmed; `--status` now says so
- [x] **Assistant eval cases lost (O37) — done 2026-11-11:** `data/assistant/eval.yaml` was never committed
      (`data/*` ignored); new set of 34 cases (all 20 tools, direct answers, clarify, 4 skills), `data/assistant/`
      tracked. Left: the user runs `python -m src.llm.assistant --eval`

### L18. "RDP window" — open 2026-11-04 (ISSUES_LOG O18)

To decide with the user: (a) **remote desktop in the browser**, which a person operates — Apache Guacamole
in Docker (SERVER_ACCESS_DECISION.md §6 C2, already the chosen option for this), or (b) **the AI operating
desktop apps** — vercel-labs/agent-desktop (accessibility tree, Windows/macOS/Linux, Apache-2.0); it drives
the machine it runs on (here: the MulteAgent server itself), so it needs its own safety design.

---

## Progress log

| Date | Item | Notes |
|------|------|-------|
| 2026-11-11 | **L16 S7 self sign-up** | "Create account" on the login page (username, email, phone optional, password); open sign-up (admin switch, on by default) and one-use invite links (7 days, optional email + modules); every new account waits for an admin (status `pending`); 5 sign-ups per address per day; approve / reject in Settings → Users. Tables `user_invites`, `app_settings`. 801 tests |
| 2026-11-11 | **L20 O37 assistant eval set** | `data/assistant/eval.yaml` lost in the move (ignored by `data/*`, never committed); rebuilt with 34 cases for today's tools and skills; folder now tracked |
| 2026-11-11 | **L16 S2 abuse limits** | Sign-in: per account 5 free failures, back-off 1–30 s, 10 in 15 min → 15-min lock; per address 20 in 15 min → 15-min block; unknown names counted and timed like accounts (table `login_failures`). Requests: 120/min per user (burst 60), 60/min per address signed out; 30 MB body cap (413). Real client address via `frontend/server.mjs`. 788 tests |
| 2026-11-10 | **L20 O34 see what runs** | Brain refresh logs `n/total` + every file; `./start.sh --status` snapshot and `./start.sh --follow` live tagged output (signal stops every watcher — checked). Status showed the embedding model on the CPU → O35 |
| 2026-11-10 | **L20 O26 worker entry point** | `worker_main.py` imports the current modules, checks `DEVICE_KEY`, hides the database password, leaves running jobs alone; +1 offline test |
| 2026-11-10 | **L20 O35 embedding on the CPU** | Not a fault: `embed.py` asks for `num_gpu: 0`; chat 6.5 GB + desktop leave no room anyway; query embedding 0.09 s on the CPU. User kept it; `--status` wording fixed |
| 2026-11-10 | **L20 O33 test database** | Brain tests on `multeagent_test` (conftest creates it); lock test with `lock_timeout`; full suite 770 passed, nothing deselected, 68 s instead of 254 s |
| 2026-11-10 | **L20 O28 Docker plan** | User: hybrid stays, remove the broken services. Compose now `db`, `searxng`, `pgadmin` only; `Dockerfile` + `deploy/Caddyfile` deleted; README worker/database sections corrected; plan doc rewritten |
| 2026-11-10 | **L20 O27 backup / restore** | `backup.sh` (dump + `.env`, `artifacts/`, `agents/`, eval results, MANIFEST → `backups/*.tar.gz`, mode 600, unencrypted), `restore.sh --verify` (scratch database, row counts) and `restore.sh` (safety backup first, files, database, logins/grants). Shared shell helpers in `deploy/shell-common.sh` (also used by `start.sh`). `app_db setup` hands restored tables back to the app login (+1 test). Live: backup 67 MB, verify OK. 767 passed, 2 skipped |
| 2026-11-10 | **L20 O31 fresh database setup** | `app_db prepare` (migrations, `brain_app`, `multeagent_app` + schema `app`, each only when missing); `start.sh` runs it after the database is healthy. 4 new offline tests; 766 passed, 2 skipped (the refresh-lock test deselected: blocked by the API's first full brain refresh, logged as O33) |
| 2026-11-10 | **L20 O30 start.sh health line** | Health summary crashed with a SyntaxError (escaped quotes in the inline Python); now plain quotes. Checked against the running API |
| 2026-11-10 | **L20 O29 start.sh first run** | Removed Docker Desktop left `credsStore` in `~/.docker/config.json` → pulls failed, no DB, API exited. `start.sh` now names the helper and the fix, skips the API while the DB is down, and polls health quietly. Failure path checked with a copy of the bad config |
| 2026-11-07 | **L20 start.sh** | Linux starter in `start.ps1`'s order; API and UI run in their own process groups (logs `logs/api.log`, `logs/ui.log`), `--stop` ends them with their children. Syntax and `--help`/`--stop` checked; full run waits for Docker access |
| 2026-11-07 | **L20 O25 Babel** | API failed to start on Ubuntu (`No module named 'babel'`, only present on Windows by chance). Added `Babel`, plus `cryptography`, `numpy`, `psutil`, `urllib3` (direct imports found by an import scan). 738 passed, 27 skipped — 25 are database tests waiting for Postgres (Docker permission) |
| 2026-11-07 | **L20 one requirements file** | Moved to Ubuntu (Python 3.14): `-dev` and `-image-cu118` merged into `requirements.txt` (pytest; `torch==2.14.1+cu126` from the PyTorch index — cu118 has no 3.14 wheels and the default cu132 torch lacks sm_61 kernels for the P4000). README and image-error hints updated |
| 2026-11-04 | **L17 NVR camera list (N1–N3)** | "Tell me all the cameras on the NVR" → `nvr_cameras`: Hikvision ISAPI / Dahua HTTP API found by asking the device, read-only, one login attempt, defusedxml; login in Settings → Servers (type Camera recorder, memory only, private addresses); table + .xlsx. 744 tests. Live check on 192.168.190.248 pending |
| 2026-11-04 | **L16 S1 cookie sessions** | Login token out of `localStorage`: HttpOnly SameSite=Strict cookies, 15-min access + rotating 7-day refresh (hashed, reuse → sign-in revoked), logout revokes at once, disabled users locked out immediately (was: until the 12-h token ran out), same-origin `/api` (CORS removed, cross-site writes 403), weak `JWT_SECRET` refused. 733 tests |
| 2026-11-03 | **L14 S3 Settings → Skills** | Yours (Team / Created by you: on/off, edit, download, delete) and Discover (search, categories, view, + Add); create and upload SKILL.md; admins add for the team. Frontend only |
| 2026-11-03 | **L14 S2 fix: `/` menu + typing lag** | Built-in skills run without adding them (`skills.usable`; `GET /api/skills` → `usable`); `/s` lists names starting with s, then names/titles containing it; the message feed is memoised so typing does not re-render the conversation. 724 tests |
| 2026-11-03 | **L14 S2 skills in chat** | `/name` in the message box runs a skill: its specialist, instructions and tools; missing inputs asked first; `/` menu in the composer + "Use a skill" in "+". 4 eval cases (`skill:` key). 723 tests |
| 2026-11-03 | **L14 S1 skills store + catalog** | Skills in the open SKILL.md format; Discover = 17 built-in skills in `skills/<name>/SKILL.md`; Yours = team skills (admins) + personal skills (owner only) in table `skills`; `/api/skills` list / catalog / add / create / upload / edit / delete / download. 720 tests |
| 2026-11-03 | **L15 P6 search market** | SEO searches run in one market: the user's words, else the site's own locale/country domain, else asked once (own country first). Live: "best laptop price" UK → currys/argos, Pakistan → paklap/priceoye; paklap.pk, priceoye.pk → en-PK, currys.co.uk → en-GB, python.org → ask. Menus/header/footer out of page text. 4 eval cases check the `market` argument (`expect:`). 705 tests |
| 2026-11-02 | **L15 P3-P5 keywords, competitors, projects** | `keyword_research` (SearXNG: ranks ≈, ideas, difficulty estimate, what top pages cover), `compare_sites` (side-by-side audit, what they do better, content gap), `write_content` briefed by the top pages, saved audits with change reports, tracked sites re-audited on a schedule, `seo_projects`. P2 page speed on hold for discussion. 695 tests |
| 2026-11-02 | **L15 P1 SEO crawler depth** | Research of Semrush / Screaming Frog / PageSpeed / Search Console → SEO_TOOLS.md matrix + phases. Audit gains redirects + chains, response time, click depth, orphans, inlinks, reused link text, near-duplicates, canonical/mixed content/URL/security-header checks, Links + Performance areas, a spreadsheet of every page. 681 tests |
| 2026-11-02 | **L13 marketing tools** | `site_audit`: polite crawl of a public site → 20 checks → maturity score per area + level → model writes the fix plan and judges the content. `write_content`: brief → draft → checks + editor → revisions, best draft wins with a score table; can start from an existing page's copy. 679 tests |
| 2026-11-02 | **L12 chat memory** | Memory rebuilt from the chat's own jobs, budgeted in tokens (35% of LLM_NUM_CTX), oldest turns folded into a saved running summary; memory meter + 80% banner → "Summarize & continue in a new chat" / "Save summary". 662 tests |
| 2026-11-02 | **L10 documents + L11 token usage** | Answers export to md/txt/docx/pdf/pptx/html with no model call ("Download as", "make this a PDF" reads the full earlier answer); new documents written section by section with checks; file names follow the topic. Every model call recorded in `llm_usage` → usage per answer, per chat (under the message bar) and per user (Settings → Usage). 655 tests |
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
