# MulteAgent — Team Multi-Agent + Agentic Platform

Local-first, framework-free multi-agent system with:

- **Auto** (default) — the assistant answers directly or picks a tool itself (native tool calling,
  `src/assistant.py`): knowledge base, web search, weather, network scan, image, diagram, document
- **Specialists** — IT, Development and Marketing (`src/specialists.py`): picked per message, or pinned
  in the chat bar; each has its own instructions, allowed tools and knowledge collections. Edit them in
  Settings → Specialists (admins). They replaced the Optimist / Cynic / Consensus debate (2026-10-17)
- **Chat** — single fast Assistant reply
- **Agentic** planner → tools → critic loop
- **Vision** — paste/attach images; Auto routes to `MODEL_VISION` (default `llava:7b`)
- **Job queue**, JWT auth, model registry, GPU fairness for 8–15 users
- Hybrid path: SQLite/LAN now → Postgres + Caddy + workers for cloud

## Quick start (LAN team)

```bash
cd D:\multeagent
# Prefer Python 3.11–3.13 (3.14 has no torch+cu118 wheels for Pascal GPUs)
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
# On Quadro P4000 / Pascal: also pin CUDA 11.8 torch (see Image generation below)
pip install -r requirements-image-cu118.txt
copy .env.example .env

# Terminal 1 — API + in-process worker
python main.py --gui

# Terminal 2 — UI
cd frontend
npm install
npm run dev
```

Open the Next.js URL, sign in with `admin` / `admin123`.  
(Multi-user accounts are deferred — admin-only for testing right now.)

## Image generation (local SDXL)

Chat prompts like “create an image of …” route to in-process **Hugging Face Diffusers** (no ComfyUI/Ollama image API at runtime).

1. Place `sd_xl_base_1.0.safetensors` in `models/checkpoints/` (or set `MODEL_IMAGE_PATH`).
2. **Pascal GPUs (Quadro P4000, compute 6.1):** install the CUDA 11.8 wheel — newer cu124/cu130 builds fail with `no kernel image is available`:

```bash
pip install -r requirements-image-cu118.txt
```

3. Defaults are **768×768**, ~20 steps, fp16 + model CPU offload + VAE slicing (fits ~8GB VRAM). Prefer ≥32GB system RAM/pagefile when offloading.
4. **txt2img** — text only. **img2img** — attach a reference image + edit/regenerate wording (`IMAGE_STRENGTH`, default `0.4`). Not pixel-perfect text swaps (needs inpainting later).
5. Auto router picks vision vs txt2img vs img2img from the query; prompts are polished locally (no extra LLM call).
6. Image jobs share `LLM_SLOTS` with chat — unload large Ollama models before generating if VRAM is tight.
7. First load may download tokenizer/config pieces into the HF cache (online once), then reuse.

## Web search (on request)

Ask Auto to search ("**search the web for** …", "**google** …") or click **Web** in the composer:

- Runs against a **local SearXNG** container (`docker compose up -d searxng`, `127.0.0.1:8888` only) — the question leaves your machine only towards the search engines SearXNG asks (Google, Bing, DuckDuckGo…).
- The top pages are read with the brain's polite fetcher, the closest passages are picked, and the answer **cites its web sources** under the reply.
- Nothing found on the web is stored in the knowledge base. Router intent: `web_search` (`data/router/intents.yaml`).

## Documents (Word / PDF / PPT / Excel / HTML)

Ask Auto for a downloadable file, or click **Document** in the composer:

- “create a PDF report about …”
- “make a PowerPoint deck on …”
- “export this as a Word doc”

Agentic tools write files under `artifacts/{job_id}/files/`; the feed shows **Download** chips. See [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md) Tier 5.

## Architecture (short)

| Piece | Role |
|-------|------|
| FastAPI (`web/server.py`) | Auth, agents, models, jobs, SSE |
| Intent router (`src/intent_router.py`) | Forced intents, attachments; hands Auto to the assistant (tool calling) |
| Chat (`src/chat_runner.py`) | Single-call Assistant replies |
| Worker (`src/worker.py` / `worker_main.py`) | Claims jobs: picks the specialist, then chat / tools / agentic |
| DB (SQLite or Postgres) | Users, jobs, events, model registry |
| LLM lock (`LLM_SLOTS`) | Fair GPU concurrency |
| Next.js (`frontend/`) | Team UI |

**Full review doc (what / how / why / libraries):** [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md)

Also see [multi_agent_project_plan.md](multi_agent_project_plan.md).

## Horizontal workers

```bash
# Same machine / VMs sharing DATABASE_URL
python main.py --worker
```

Or:

```bash
docker compose up --build --scale worker=3
```

## Postgres (team / cloud)

```bash
docker compose up -d db
# set DATABASE_URL=postgresql+psycopg://multeagent:multeagent@localhost:5432/multeagent
python main.py --gui
```

TLS / reverse proxy starter: [deploy/Caddyfile](deploy/Caddyfile). SSO is gated by `SSO_ENABLED` (hook endpoint `/api/auth/sso/status`).

## Quality checks (need Ollama + the brain database)

```bash
python -m src.brain.evaluate        # retrieval: is the right page found?          (Recall@8, MRR)
python -m src.brain.answer_eval     # answers: grounded, right source, no-evidence, latency  (B6)
python -m src.assistant --eval      # does the assistant pick the right tool?  (--specialists: as real chats do)
python -m src.specialists --eval    # does the picker choose the right specialist?
```

## Defaults

- Admin: `admin` / `admin123` — change immediately
- `LLM_SLOTS=1` for 8GB GPUs
- Model roles: `MODEL_FAST`, `MODEL_STRONG`, optional `MODEL_CLOUD`
- Vision: `MODEL_VISION=llava:7b` — used whenever a message has images
- Image gen: `MODEL_IMAGE_PATH` → local SDXL; `IMAGE_WIDTH`/`IMAGE_HEIGHT` default 768

## Chat UI

- **Enter** sends; **Shift+Enter** inserts a new line
- Paperclip icon attaches images; compact send icon submits
- **Edit** on a user message loads it into the composer so you can change it and send again
- **Retry** on the last turn regenerates without duplicating the user bubble
- Paste or drop images (up to 4, 5MB each)
