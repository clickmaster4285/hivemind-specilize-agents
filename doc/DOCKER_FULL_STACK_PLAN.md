# Improve MulteAgent Docker (frontend included)

**Status:** draft for review — do not implement until approved.  
**Related:** [README.md](../README.md), [docker-compose.yml](../docker-compose.yml), [start.ps1](../start.ps1)

---

## Goal

One reliable command so the app works without a separate host `npm run dev` and without broken API containers:

```bash
docker compose up -d --build
# open http://localhost:3000
```

**Locked decision for this plan:** keep **Ollama on the host** (`127.0.0.1:11434`). Ollama + GPU SDXL inside Compose is a later phase.

---

## Current problems (from your logs)

```text
Browser → Next.js (:3000) → proxy /api → API (:8787)  ← nothing listening

docker compose up:
  db       → OK (until Ctrl+C)
  searxng  → OK (until Ctrl+C)   ← it DID start; you stopped the stack
  api      → crash: ModuleNotFoundError: babel
  worker   → crash: No module named 'src.config'
  caddy    → up, but only stubs UI (no Next.js service)
  frontend → NOT in compose (host npm only)
```

| # | Problem | Why |
|---|---------|-----|
| 1 | API container dies | `src/tools/market.py` imports `babel`; `requirements.txt` does not list it |
| 2 | Worker container dies | `worker_main.py` still imports old paths (`src.config`, `src.db`, `src.worker`) |
| 3 | Frontend not in Compose | Only host `npm run dev`; Caddy returns a stub for `/` |
| 4 | Docker env incomplete | API hardcodes few vars; inside Docker, SearXNG must be `http://searxng:8080`, not `127.0.0.1:8888` |
| 5 | Foreground compose + Ctrl+C | Stops SearXNG/DB too — day-to-day should use `docker compose up -d` |

---

## Target architecture

```text
Browser
   │
   ▼
frontend (:3000)  ──rewrite /api/*──►  api (:8787)
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
                   db               searxng (:8080)     Ollama on host
                 (:5432)                                  (:11434)
                    ▲
                    │
                 worker

caddy (optional): / → frontend, /api → api
```

---

## Phase 1 — Make existing Compose services healthy (do first)

1. **Fix Python deps**
   - Add `Babel` to `requirements.txt`
   - Smoke-import `web.server` and add any other missing packages the Docker image needs

2. **Fix worker entrypoint**
   - Update `worker_main.py` to use current modules:
     - `src.core.config.Config`
     - current DB init module
     - `src.jobs.worker.worker_loop`  
   - Match what `web/server.py` already uses

3. **Harden `Dockerfile`**
   - Set `PYTHONPATH=/app` (or equivalent) so `src.*` imports work
   - Keep API as default `CMD`; worker keeps compose `command` override

4. **Fix `docker-compose.yml` env for `api` / `worker`**
   - Pass secrets from `.env` (stop hardcoding `JWT_SECRET=change-me-in-production`)
   - `DATABASE_URL` / `BRAIN_DATABASE_URL` with hostname `db`
   - `WEB_SEARCH_URL=http://searxng:8080`
   - Keep `LLM_BASE_URL=http://host.docker.internal:11434` + `extra_hosts`
   - `depends_on` with db healthy before api starts

5. **Verify Phase 1**
   ```bash
   docker compose up -d --build db searxng api
   curl http://127.0.0.1:8787/api/health
   ```

---

## Phase 2 — Add frontend as a Compose service

1. Add `frontend/Dockerfile` (multi-stage: `npm ci` → `next build` → `next start`)
2. Add `frontend` service to `docker-compose.yml`:
   - `MULTEAGENT_API_ORIGIN=http://api:8787` (server-side rewrite inside Docker network)
   - Publish `3000:3000`
   - `depends_on: api`
3. Update `deploy/Caddyfile`:
   - `/` → `frontend:3000`
   - `/api/*` → `api:8787`
   - Remove the stub “API only” HTML response
4. Docs note: full compose uses container UI (production-style). Host `npm run dev` remains only for frontend hot-reload / hybrid mode.

---

## Phase 3 — Linux starter + docs

1. Add `start.sh` (Linux equivalent of `start.ps1`):
   - Check Docker + Ollama
   - `docker compose up -d --build` for full stack  
   - Optional hybrid path later: db/searxng in Docker + local Python API + local `npm run dev`

2. Update `README.md` with two clear paths:
   - **Recommended (Docker):** `docker compose up -d --build`
   - **Dev hybrid:** `docker compose up -d db searxng` + `python main.py --gui` + `cd frontend && npm run dev`

---

## Out of scope (this plan)

- Ollama as a Compose service / GPU passthrough
- SDXL / Diffusers inside Docker (keep host venv for image gen)
- Fixing every SearXNG engine warning (`ahmia` / `torch`) if search still works

---

## Work checklist

- [ ] Fix `requirements.txt` (Babel+) + smoke imports
- [ ] Fix `worker_main.py` imports
- [ ] Harden `Dockerfile` (`PYTHONPATH`)
- [ ] Wire `api` / `worker` env in `docker-compose.yml` (db, searxng, secrets, health)
- [ ] Add `frontend/Dockerfile` + `frontend` compose service
- [ ] Update `deploy/Caddyfile` to proxy UI + API
- [ ] Add `start.sh` + README Docker vs hybrid notes
- [ ] Verify: compose up, login at `:3000`, health, chat via host Ollama

---

## Success criteria

- `docker compose up -d --build` leaves `db`, `searxng`, `api`, `worker`, `frontend` (and optionally `caddy`) **Up**
- http://localhost:3000 loads login; no `ECONNREFUSED` on `:8787`
- Web search reaches SearXNG via Docker DNS (`searxng:8080`)
- Chat works against host Ollama (`qwen3.5:9b`)

---

## After you read this

Reply with one of:

- **approve** — implement as written  
- **change X** — adjust the plan first  
- **phase 1 only** — fix API/worker first, frontend later  
