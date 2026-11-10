# How a user query works in MulteAgent

What happens, layer by layer, from pressing **Send** in the chat to the answer on the screen.
Updated 2026-11-10 for the current code (Auto with tool calling, specialists, knowledge base, image service).

---

## Big picture

```text
Browser (Next.js, port 3000)
    │  POST /api/jobs, then GET /api/jobs/{id}/events (streamed)       ← the browser only talks to Next.js;
    ▼                                                                     Next.js passes /api/* to the API
API (FastAPI, 127.0.0.1:8787 — web/server.py, web/routers/jobs.py)
    │  login check → create the job → first routing → saved as "queued"
    ▼
Worker (src/jobs/worker.py — runs inside the API process)
    │  claims the job → memory, skill, specialist
    │  Auto: the assistant answers itself or picks ONE tool (src/llm/assistant.py)
    ▼
Runner for that tool
    │  chat / knowledge base / web search / image service / diagram / document / network & server tools …
    ▼
Every step is an event in the database → streamed to the browser → the chat updates live
```

One idea everywhere: **create a job, run it in the background, stream progress back as events.**

---

## Layer 1 — Browser (what the user sees)

**Files:** `frontend/src/components/ControlDeck.tsx`, `frontend/src/lib/api.ts`

1. The user types (and may attach images, pick a specialist, or turn on **Deep** / **Options**).
2. On Send the UI shows the user's bubble at once and calls `POST /api/jobs` with:
   `query`, `mode` (normally Auto), `images[]`, `specialist`, `depth`, `options`, the browser's time zone, and
   any action button (image, diagram, document…).
3. It then reads `GET /api/jobs/{job_id}/events`: each event updates the feed (status line, specialist,
   tool used, the answer as it is written, sources, images, download chips).
4. Generated files come from `GET /api/jobs/{job_id}/attachments/{filename}`.

The browser calls only Next.js (port 3000). Next.js forwards `/api/*` to the API, which listens on
`127.0.0.1` only — other machines on the LAN can reach the UI but not the API directly. The browser never
talks to Ollama or the image service.

---

## Layer 2 — API: who is asking, and is it allowed

**Files:** `web/server.py`, `web/routers/jobs.py`, `src/core/auth.py`

1. Every request carries the login token (JWT, signed with `JWT_SECRET` from `.env` — the API refuses to
   start with a short or placeholder secret).
2. `POST /api/jobs` checks the body and calls `create_job` (Layer 3).
3. When the API starts it also starts the **worker** (Layer 5), the knowledge-base refresh scheduler and the
   SEO tracker. Extra workers can run beside it (`python main.py --worker`); a job is never claimed twice.

---

## Layer 3 — Creating the job

**File:** `src/jobs/jobs.py` → `create_job`

| Step | What happens |
|---|---|
| 1. Guards | Active jobs per user, queue length, daily quota |
| 2. First routing | `classify_query` (Layer 4) |
| 3. Model plan | Which chat / vision model (`resolve_model_plan`; images attached → vision model) |
| 4. Save | Job row with `status=queued`; attached images to `artifacts/{job_id}/images/` (the base64 is not kept in the row) |
| 5. Events | `job_queued`, `route_decided` |

The job then waits until a worker claims it (`claim_next_job` → `status=running`).

---

## Layer 4 — First routing (fast, no model call)

**File:** `src/llm/intent_router.py` → `classify_query`

Only the cases that are clear without a model are decided here; everything else goes to the assistant:

```text
An action button was pressed (image, edit, diagram, web search, document, describe)?  → that action
Images attached?   change / regenerate wording → image edit (img2img); otherwise → vision (describe it)
Image-prompt flags (--ar 16:9, --v 6)?                                              → image generation
Otherwise (Auto or Chat)                                     → intent "auto": the assistant decides (Layer 5)
```

With `ASSISTANT_TOOLS=false` the older routing is used instead: matching the message against the example
sentences in `data/router/intents.yaml` with the embedding model (`src/llm/semantic_router.py`), and word
rules if that model is not reachable.

---

## Layer 5 — Worker: who answers, and how

**File:** `src/jobs/worker.py` → `process_job` / `_run_job`

In order:

1. **Memory** — the chat's earlier turns, rebuilt from its own jobs and kept within the model's context
   (`src/jobs/chat_memory.py`; older turns are summarised).
2. **Skill** — a message starting with `/name` uses that skill's instructions (`src/llm/skills.py`).
3. **Specialist** — IT, Development or Marketing (`src/llm/specialists.py`): the one pinned in the chat bar,
   the skill's, or picked by one short model call. It brings its own instructions, allowed tools and
   knowledge collections. Event: `specialist_chosen`.
4. **Deep** (research across many sources) or **Options** (several approaches to pick from): straight to the
   chat runner, no tool choice.
5. **Auto** — the assistant (`src/llm/assistant.py` → `decide`): **one model call** with the list of tools.
   The model either
   - **answers directly** — streamed into the chat, done; or
   - **calls one tool** with its arguments (event `tool_chosen`); or
   - **asks a clarifying question** (multiple-choice form in the chat).
6. The worker runs that tool's code:

| Tool the model can call | What runs |
|---|---|
| `search_knowledge_base` | Chat runner with the knowledge base (Layer 6 A) |
| `web_search` | Chat runner with SearXNG search (Layer 6 B) |
| `generate_image` | Image service (Layer 6 C) |
| `draw_diagram` | Mermaid diagram (`src/tools/diagram_gen.py`) |
| `create_document` | Document writer: outline → each section written and checked → PDF / Word / … (`src/tools/doc_writer.py`); "this as a PDF" converts the earlier answer as it is |
| `get_weather` | Weather and 3-day forecast |
| `network_scan`, `ping_host`, `security_check`*, `server_audit`, `diagnose_server`, `operate_server`, `nvr_cameras`, `network_device` | Network and server tools (`src/servers/`); they use the user's own saved device logins, encrypted with `DEVICE_KEY` |
| `site_audit`, `compare_sites`, `keyword_research`, `seo_projects`, `write_content` | SEO and marketing tools (`src/tools/`) |
| `about_multeagent` | Live facts about this installation |

\* admins only.

A specialist only offers its own tools. The **Agentic** mode (planner → tools → critic loop,
`src/agentic/`) still exists for multi-step work and spreadsheets.

---

## Layer 6 — The main runners

### A) Chat with the knowledge base — `src/jobs/chat_runner.py` → `src/brain/answer.py`

1. **Search** (`src/brain/retrieve.py`): the question is embedded (`qwen3-embedding:0.6b`, on the CPU on
   purpose, ~0.1 s) and searched in Postgres + pgvector by meaning **and** by exact words (product names,
   error codes), only in the specialist's collections first.
2. The best passages are numbered and given to `qwen3.5:9b` with today's date and grounding rules.
3. The answer cites them as `[1]`, `[2]` and gets a sources list; which passages were used is logged per
   account (`brain.retrieval_log`).
4. Not enough evidence → it says so instead of guessing.

The knowledge base keeps itself fresh: the scheduler in the API re-checks each source when its refresh
interval has passed (fetch → extract → chunk → embed; unchanged pages are skipped). Progress:
`./start.sh --follow api`.

### B) Web search — `src/tools/web_search.py`

Only when asked. Local SearXNG (`127.0.0.1:8888`) → the top pages are read → an answer with links.

### C) Images — `src/images/image_client.py` → image service (`src/images/image_service.py`)

The API itself never loads torch. It starts the separate **image service** (SDXL) on the first image
request and sends it the prompt over HTTP; the service stops itself after some idle minutes and gives the
GPU memory back. The chat model is unloaded from the GPU first (`IMAGE_UNLOAD_OLLAMA`), because both do not
fit in 8 GB. Text-to-image, or image-to-image for an attached picture (`IMAGE_STRENGTH`). The PNG goes to
`artifacts/{job_id}/images/`.

### D) Plain chat and vision — `src/jobs/chat_runner.py`

No tool needed: the model's direct answer from step 5. With attached images → `MODEL_VISION`.

---

## Layer 7 — Shared pieces

| Piece | File | Role |
|---|---|---|
| Settings | `src/core/config.py` | `.env` → settings |
| Model client | `src/llm/llm_client.py` | Talks to Ollama (`LLM_BASE_URL`) |
| GPU slots | `src/llm/llm_lock.py` | `LLM_SLOTS`: how many model calls at once |
| App database | `src/core/db.py`, `src/core/models_db.py` | Users, jobs, events, models — Postgres schema `app`, login `multeagent_app` |
| Knowledge base | `src/brain/` | Postgres schema `brain`, login `brain_app` (row-level security per account) |
| Device logins | `src/servers/server_vault.py`, `src/core/crypto.py` | Encrypted with `DEVICE_KEY` |
| Files | `src/tools/attachments.py`, `artifacts/` | Uploaded and generated images, documents |

---

## Layer 8 — Events back to the browser

A typical knowledge-base question:

```text
job_queued
route_decided        ← "the assistant chooses (tool calling)"
specialist_chosen    ← e.g. IT
tool_chosen          ← search_knowledge_base
agent_start          ← status line: "Searching knowledge base"
… the answer as it is written …
agent_done           ← full text + sources
session_done
```

The UI turns these into the bubble, the status line and the sources list.

---

## Worked examples

**"How do I open port 443 in ufw?"** → Auto → specialist IT → the model calls `search_knowledge_base` →
passages from the Ubuntu docs → answer with `[1]` citations and sources.

**"What's the weather in Lahore?"** → Auto → `get_weather` → current weather and forecast.

**"Create an image of a seahorse in Antarctica"** → Auto → `generate_image` → the image service starts
(first time ~1 min), the chat model is unloaded → PNG in the chat.

**Screenshot attached + "what's in this?"** → first routing: vision → `MODEL_VISION` describes it.

**"Hi"** → Auto → the model answers directly, no tool.

---

## In one sentence per layer

| Layer | One-liner |
|---|---|
| Browser | Send the message, then follow the job's events. |
| API | Check the login, accept the job. |
| Job | Guards, first routing, model plan, save, queue. |
| First routing | Buttons and images decided at once; the rest goes to the assistant. |
| Worker | Memory, skill, specialist — then the assistant answers or picks one tool. |
| Runners | Knowledge base, web, images, diagrams, documents, network/server and SEO tools. |
| Events | Stream every step so the chat updates live. |

---

## Related docs

- Start, status, backup: [README.md](../README.md)
- Knowledge base design: [BRAIN_DESIGN.md](BRAIN_DESIGN.md)
- What runs where (hybrid Docker): [DOCKER_FULL_STACK_PLAN.md](DOCKER_FULL_STACK_PLAN.md)
- Broader architecture: [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md)
