# MulteAgent — Backlog & Decisions

What we decided, what is being built, and what comes later. **Start with "At a glance".**

- **One item = one `### Ln.` section**, in number order. Finished parts stay as one or two lines with a link
  to the doc that explains how they work; the detail lives there, not here.
- **Problems** go to [ISSUES_LOG.md](ISSUES_LOG.md) (O-numbers) and are linked from the item.
- **What got done, by date:** [PROGRESS_LOG.md](PROGRESS_LOG.md) (newest first).
- **Every doc and where new information goes:** [README.md](README.md).

---

## At a glance

| Item | What | Status | Left |
|---|---|---|---|
| [L1](#l1-model-comparison-qwen359b-vs-gemma412b) | Model comparison qwen3.5:9b vs gemma4:12b | open |  |
| [L2](#l2-domain-specialist-agents-replace-the-debate---first-version-2026-10-17-issues_log-39) | Domain specialists (replace the debate) | ✅ first version 2026-10-17 |  |
| [L3](#l3-stop--pause-and-progress-feedback) | Stop / pause and progress feedback | ✅ streaming + Stop 2026-10-17 | left: pause |
| [L4](#l4-enforce-local-only-no-cloud---done-2026-11-02) | Local-only (no cloud) | ✅ 2026-11-02 |  |
| [L5](#l5-router-heuristics) | Router heuristics | open | revisit with specialists |
| [L5b](#l5b-the-model-does-not-know-todays-date---fixed-in-b4-datetime-in-every-chat-prompt) | Model knows today's date | ✅ (B4) |  |
| [L5c](#l5c-citation-checker-answer-quality---built-2026-10-19) | Citation checker | ✅ 2026-10-19 |  |
| [L6](#l6-cleanup---done-2026-11-02) | Cleanup | ✅ 2026-11-02 |  |
| [L7](#l7-sdxl-memory-is-double-the-expected-size--slow-images---resolved-2026-10-16) | SDXL memory / slow images | ✅ 2026-10-16 |  |
| [L8](#l8-serving-throughput-revisit-vllm--only-if-we-go-multi-user-on-linux--gpu) | vLLM serving | not now | only for many users on Linux + GPU |
| [L9](#l9-serial-number-for-inventoried-servers-over-ssh-credentialed-path) | Serial numbers over SSH | deferred | when inventory + stored SSH logins exist |
| [L10](#l10-documents-direct-export-download-as-section-by-section-writer---done-2026-11-02-fixes-issues_log-o8) | Documents: export, Download as | ✅ 2026-11-02 |  |
| [L11](#l11-token-usage-tracking---done-2026-11-02) | Token usage tracking | ✅ 2026-11-02 |  |
| [L12](#l12-context-awareness-meter-auto-compaction-new-chat---done-2026-11-02-fixes-o9) | Context meter, auto-compaction | ✅ 2026-11-02 |  |
| [L13](#l13-marketing--seo-tools---done-2026-11-02-fixes-o11-assistant-eval-run-pending) | Marketing / SEO tools | ✅ 2026-11-02 | assistant eval run (user) |
| [L14](#l14-skills--reusable-instruction-packs--in-progress-2026-11-03-fixes-issues_log-o13) | Skills | in progress | a unit test for `clarify_query` |
| [L15](#l15-seo-suite--screaming-frog--semrush-capabilities--p1-p3-p5-done-p2-on-hold-fixes-o12) | SEO suite | P1, P3–P6 ✅ | P2 page speed on hold |
| [L16](#l16-security-hardening--in-progress-s1-s1b-s2-s2b-s4-s7-s8-done-s3-s5-s6-open-fixes-issues_log-o16) | Security hardening | in progress | S3 uploads, S5, S6 |
| [L17](#l17-nvr-camera-list--in-progress-2026-11-04-fixes-issues_log-o17-before-l16-s2-users-order) | NVR camera list | in progress | N4 ONVIF; live check on the NVR |
| [L18](#l18-rdp-window--open-2026-11-04-issues_log-o18) | "RDP window" | open — to decide |  |
| [L19](#l19-devices--routers-switches-and-more-read-only--in-progress-2026-11-04-fixes-issues_log-o19) | Devices (routers, switches) read-only | in progress | D2 reader (rest), D3 chat tool |
| [L19b](#l19b-saved-devices-full-report-full-pages--planned-2026-11-04-issues_log-o21o23) | Saved devices, full report, full pages | in progress | D5 full pages |
| [L20](#l20-move-to-ubuntu--started-2026-11-07-issues_log-o24) | Move to Ubuntu | in progress | O32 database password (user); O44 eval misses, O46 stale answer (rerun by the user); O47 web answer depth; O48 timings; O51–O58 chat quality, speed, context; O62 Mermaid 12 (user checks diagrams) |
| [L21](#l21-docs-that-are-easy-to-find-and-maintain---done-2026-11-11-issues_log-o41) | Docs easy to find and maintain | ✅ 2026-11-11 |  |
| [L22](#l22-styles-in-tailwind-only---done-2026-11-11-issues_log-o42) | Styles in Tailwind only | ✅ 2026-11-11 |  |
| [L23](#l23-frontend-components-in-feature-folders---done-2026-11-11-issues_log-o43) | Frontend components in feature folders | ✅ 2026-11-11 |  |

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

## Knowledge Brain — Phase 1 checklist

The brain is the knowledge system, not the model: documents + metadata + search index + update tracking.
The model only reasons over what the brain retrieves.

- [x] Brain design doc → [BRAIN_DESIGN.md](BRAIN_DESIGN.md) (draft, open decisions in §11)
- [x] **Hybrid retrieval** (B3, 2026-10-15): vector search (meaning) + keyword/full-text search (exact terms such as CVE IDs, error codes, package names, flags), merged and optionally re-ranked
- [x] **Fresh sources** (B5, 2026-10-16): scheduled ingestion of trusted official sources (docs, advisories, feeds) + change detection + re-indexing — no retraining
- [x] (B4, 2026-10-15) Answers cite sources and say "not enough evidence" when retrieval finds nothing relevant
- [x] (B5: versions, `superseded`, `last_checked_at`, `src/brain/freshness.py`) Stale-content tracking (last checked date, version, superseded documents)
- [x] (60 questions by B5) Evaluation set: 30–50 questions with known source documents, in the languages we use
- [x] (on the CPU by design, ISSUES_LOG O35) Embedding calls go through the GPU lock rules (or run on CPU) so they do not evict the 9b

---

## Items

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

### L5b. The model does not know today's date — ✅ fixed in B4 (date/time in every chat prompt)

- Asked "what is the date today", it answered "October 16, 2023" once and "I don't have real-time information" the other times
- Fix: put the current date/time (and timezone) into every system prompt — small change
- Same root cause as stale facts ("who is the PM…"): the model only knows its training data; fresh facts must come from the brain

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

### L6. Cleanup — ✅ done 2026-11-02

- `deepseek-r1:1.5b` is no longer installed (Ollama has only `qwen3.5:9b` + `qwen3-embedding:0.6b`)
- Old registry rows: the startup sync now deletes rows for models Ollama no longer has (they return if the
  model is pulled again); an Ollama outage skips this, so the list is never emptied

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

### L16. Security hardening — in progress (S1, S1b, S2, S2b, S4, S7, S8 done; S3, S5, S6 open) (fixes ISSUES_LOG O16)

Threats: a stolen login token, password guessing, request floods, a malicious uploaded file, XSS, clickjacking.
DDoS proper is stopped at the network (firewall / reverse proxy); the app limits what one client can do.

Decisions (user, 2026-11-04): **plain http on the LAN** (HTTPS with a local certificate was tried and dropped —
see S1b); **ClamAV in Docker, fail closed** (scanner down → upload
refused). AES: nothing secret is stored today (passwords bcrypt, refresh tokens hashed, SSH logins never saved),
and encrypting a token JS cannot read adds nothing — S5 starts when the app first stores a secret.

How the finished parts work: **[SECURITY.md](SECURITY.md)**.

- [x] **S1 sessions — done 2026-11-04:** `HttpOnly` cookie sessions (15-min access + rotating 7-day refresh,
      stored hashed), logout and disabling a user take effect at once, same-origin `/api` (no CORS),
      cross-site writes 403, weak `JWT_SECRET` refused → [SECURITY.md §1.2, §2.2](SECURITY.md#22-sessions-s1)
- [x] **S1b network — done 2026-11-04, HTTPS dropped (user's decision):** a local certificate authority
      (Caddy `tls internal`) meant installing its root certificate on every PC. Weighed: own domain + Let's
      Encrypt (DNS challenge), Tailscale, Group Policy push, plain http → **plain http on the LAN** (accepted
      risk: LAN sniffing). API on 127.0.0.1 only, reached through the UI's `/api` rewrite. Revisit with a
      domain + Let's Encrypt if the app leaves the trusted LAN → [SECURITY.md §1.1](SECURITY.md#11-who-can-reach-the-app)
- [x] **S2 abuse limits (O36) — done 2026-11-11:** sign-in back-off and locks per account and per PC,
      request rates per user / per PC, 30 MB body cap, real client address through `frontend/server.mjs`;
      `daily_job_quota` (50 model jobs/day) unchanged → [SECURITY.md §1.3, §2.3](SECURITY.md#13-abuse-limits-s2)
- [x] **S2b unlock + CAPTCHA (O39) — done 2026-11-11, live check passed:** image CAPTCHA drawn by our API (sign-up always,
      sign-in from the 3rd failure; a try without it is not counted, so nobody locks an account without a
      person); admin **Unlock now** and **Blocked computers** → [SECURITY.md §1.4–1.5, §2.4–2.5](SECURITY.md#14-the-captcha-s2b).
      **Left (user):** live check — picture readable, shown at the right time; tune ([SECURITY.md Part 3](SECURITY.md#part-3--the-numbers-and-where-to-change-them))
- [ ] **S3 uploads:** type from the file's own bytes (not the declared MIME), decoded and re-encoded with
      Pillow (drops hidden payloads and metadata, pixel limit against decompression bombs), virus scan (ClamAV)
      on uploaded files; skill uploads size-capped
- [x] **S4 headers + XSS — done 2026-11-11:** CSP with a nonce per page, `nosniff`, no framing, no referrer;
      the API's own locked-down policy; answer links http(s)/mailto only. How: [SECURITY.md 1.7](SECURITY.md#17-what-the-browser-is-told-s4).
      HSTS comes with HTTPS
- [ ] **S5 encryption at rest (AES-256-GCM):** for secrets the app stores — decided per item (see questions)
- [ ] **S6 security log:** failed logins, locks, refresh-token reuse, rejected uploads — visible to admins
- [x] **S8 message filter (O59) — done 2026-11-13:** secrets and fake turn markers masked, oversize refused, an
      optional model check (off) → [SECURITY.md §1.8](SECURITY.md#18-what-a-message-may-carry-s8)
- [x] **S7 self sign-up (O38) — done 2026-11-11:** "Create account" (username, email, phone optional,
      password); open sign-up (admin switch) and one-use 7-day invite links; every new account waits for an
      admin; 5 sign-ups per PC per day. Google / Microsoft sign-in later, with HTTPS; Clerk not used
      → [SECURITY.md §1.6, §2.6](SECURITY.md#16-self-sign-up-s7)

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

### L18. "RDP window" — open 2026-11-04 (ISSUES_LOG O18)

To decide with the user: (a) **remote desktop in the browser**, which a person operates — Apache Guacamole
in Docker (SERVER_ACCESS_DECISION.md §6 C2, already the chosen option for this), or (b) **the AI operating
desktop apps** — vercel-labs/agent-desktop (accessibility tree, Windows/macOS/Linux, Apache-2.0); it drives
the machine it runs on (here: the MulteAgent server itself), so it needs its own safety design.

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
- [x] **`--stop` finds the API and UI (O40) — done 2026-11-11:** by port, not only by PID file; this
      project's processes only (working directory); the whole process group; 15 s, then forced; honest messages
- [ ] **Assistant eval misses (O44):** 24/34. The eval now runs the same pre-routing as real chats
      (`pre_route()`: web nudge, clarify check) and prints the start of the reply on a miss; tool descriptions
      sharpened for the diagram, blog post, Excel, firewall-audit and latest-version misses. Left: the user reruns
      `python -m src.llm.assistant --eval`; then the `about_multeagent` and `ask_clarification` misses
- [ ] **Stale answer with raw LaTeX (O46):** a GTA 6 download-time question was answered from memory. The prompt now
      says a game, product or release the model knows only as upcoming may be out (search first); `MATH_RULE`
      (`src/llm/diagram_rules.py`) forbids LaTeX in the direct and web answers; the message is an eval case.
      Left: the eval rerun; the user decides on a `calculate` tool for arithmetic
- [ ] **Weak web answer (O47):** a news question (Jensen Huang) got the right tool but a thin answer. Fix per problem:
  - [x] News questions search news too (3) — done 2026-11-12: the model sets web_search's `news`; general and news
        results taken in turn ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B)
  - [x] No talk of its own mechanics (1) — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B)
  - [x] Allegations attributed (4) — done 2026-11-13 (same)
  - [ ] Substance (2): depth to suit the question (user, 2026-11-13): a quick fact stays fast; an explanation,
        comparison or news story reads more pages for longer (web_search sets it, like `news`)
  - [x] Honest dates (5) — done 2026-11-13: the page's or the result's date, else none (same)
  - [x] Title once (6) — done 2026-11-13 (same)
  - [x] ~~Chat title follows the topic (7)~~ — dropped by the user 2026-11-13 (an extra model call for little gain)
- [ ] **See where the time goes (O48):** logs survive a restart; a `job_timing` line for every job (total and model
      time per step), so slow answers can be measured before speeding them up
- [x] **Search with the model's words (O49)** — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B)
- [x] **No answers from memory after a dead end (O50)** — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) A)
- [x] **Search first for things that change (O51)** — done 2026-11-13, eval 31/38 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5): GTA 6 size and China chip rules answered from memory; the GTA math
      stopped half-way; pre-routing keyword lists caused 3 eval misses
  - [x] Routing call in place of the keyword lists — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5);
        quiz only when the user is learning
  - [x] An unreadable page frees its read slot — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B)
  - [x] `calculate` tool — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5)
  - [x] The eval rerun (user) — 31/38 on 2026-11-13; the router's 2 misses fixed
- [ ] **Faster chats (O52):** ~20 s tool choice on an ~8,000-token prompt, quiz and suggestions holding the GPU after
      each answer; measure prompt reuse first. Quiz (user, 2026-11-13): only when the user is learning a topic,
      not after every answer
  - [x] Prompt reuse: the date last in the long prompts — done 2026-11-14, tool choice 17.1 → 1.5 s, router
        4.5 → 1.35 s ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) "The date goes last")
  - [ ] Suggestions, quiz and chat summaries after each answer (17–19 s); diagram rules only when a diagram
        could help (~930 tokens)
- [ ] **Pictures only where they fit (O53):** only section headings that name one thing get images
- [x] **No facts from other questions (O54)** — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B)
- [x] **Follow-ups without clarifying (O55)** — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5)
- [x] **One source per page (O56)** — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) B)
- [x] **A named site goes to its tools, not web search (O60)** — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5)
- [x] **Unit math with the tool (O57)** — done 2026-11-13 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5)
- [x] **Router names the tool (O61)** — done 2026-11-13, eval 36/38 ([HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md) Layer 5)
- [x] **Larger context (O58)** — done 2026-11-13: measured, `LLM_NUM_CTX=12288` (user) ([RESOURCES.md](RESOURCES.md))
- [ ] **Use the idle CPU (O58 follow-up):** embedding model on the CPU; a small CPU model for suggestions, quiz and
      summaries; Ollama flash attention + q8_0 cache. Each a user decision ([RESOURCES.md](RESOURCES.md) section 5)
- [ ] **Mermaid 12 (O62):** pinned 12.3.0, the dagre layout and classic look kept (done 2026-11-14); left: the user
      checks diagrams and PNG / SVG downloads in the app; then compare ELK and `neo` and keep what reads better;
      use-case diagrams in the diagram rules

### L21. Docs that are easy to find and maintain — ✅ done 2026-11-11 (ISSUES_LOG O41)

- [x] `doc/README.md`: index of every doc (what it is for, status) + where new information goes
- [x] `doc/SECURITY.md`: plain-language guide to sign-in protection (sessions, limits, CAPTCHA, unlock, sign-up)
- [x] BACKLOG: status table at the top, items in number order, finished items short with a link to their guide;
      progress log in its own file `doc/PROGRESS_LOG.md`, newest first
- [x] ISSUES_LOG: open issues apart from solved ones, both in number order

### L22. Styles in Tailwind only — ✅ done 2026-11-11 (ISSUES_LOG O42)

- [x] `@theme` maps the colour tokens to Tailwind names (`text-muted`, `bg-panel`, `border-line`, `shadow-shadow` …),
      kept as `var()` so the light theme still switches
- [x] Every class in `globals.css` moved into Tailwind utilities in its component; shared ones (buttons) in one module
- [x] `globals.css` keeps only the tokens, the theme mapping and base styles (scrollbars, page height and background)
- [x] The user looks over the app in both themes (chat, answers with tables/code/diagrams, settings, login)

### L23. Frontend components in feature folders — ✅ done 2026-11-11 (ISSUES_LOG O43)

- [x] `components/` by feature: `app` (shell), `auth` (login, CAPTCHA), `chat` (feed, composer), `answer` (markdown,
      diagrams), `settings` (+ `users/`, `skills/`); file names unchanged
- [x] Every import, code comment and doc path updated; tsc, eslint and the UI log clean
