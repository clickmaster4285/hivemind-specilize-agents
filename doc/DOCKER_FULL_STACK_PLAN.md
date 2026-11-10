# Docker: what runs where

**Decision (2026-11-10, O28):** MulteAgent runs **hybrid** on the Ubuntu machine. A full Docker stack is
not planned now; this note says what it would need if it ever is (e.g. a second machine or a team server).

## Now: hybrid

| Part | Where | Started by |
|---|---|---|
| Postgres + pgvector (`db`) | Docker, `127.0.0.1:5432` | `./start.sh` |
| SearXNG (`searxng`) | Docker, `127.0.0.1:8888` | `./start.sh` |
| pgAdmin (`pgadmin`, optional) | Docker, `127.0.0.1:5050` | `./start.sh --tools` |
| API + in-process worker | host, port 8787 | `./start.sh` (`logs/api.log`) |
| UI (Next.js dev) | host, port 3000 | `./start.sh` (`logs/ui.log`) |
| Ollama (chat on the GPU, embedding on the CPU) | host service, `127.0.0.1:11434` | systemd |
| Image service (SDXL) | host, started by the API when needed | API |

Why hybrid: one machine with one 8 GB GPU; the API and UI reload on every code change; the GPU, Ollama and
the image service are used directly; `./start.sh --status / --follow`, `backup.sh` and `restore.sh` are built
for this layout. Only the services that keep data or are hard to install run in Docker.

`docker-compose.yml` holds only `db`, `searxng` and `pgadmin`. The old `api`, `worker` and `caddy` services
were removed (O28): they were broken, published ports on the LAN, logged in as the database owner and
hardcoded a JWT secret the API now refuses.

## If a full Docker stack is ever needed

Rebuild the API side correctly rather than restoring the old services:

1. **API image** without GPU torch and pytest (split `requirements.txt`: app / image service / tests).
2. **Secrets from `.env`** (`env_file`), never written in the compose file: `JWT_SECRET`, `DEVICE_KEY`,
   `BRAIN_APP_PASSWORD`, `POSTGRES_PASSWORD`.
3. **Logins:** the API uses `DATABASE_URL` with `multeagent_app` and `brain_app`, never the owner;
   `python -m src.core.app_db prepare` runs once the database is healthy (as `start.sh` does).
4. **Network:** the API is not published; only the UI is reached, and it calls the API inside the Docker
   network (`MULTEAGENT_API_ORIGIN=http://api:8787`). Hostnames `db`, `searxng:8080`; Ollama via
   `host.docker.internal` + `extra_hosts`.
5. **UI image:** `next build` + `next start` (no hot reload; keep the host `npm run dev` for development).
6. **No reverse proxy / HTTPS** (dropped by user decision); add one only if the UI must be reached from
   other machines.
7. **Extra workers:** `python main.py --worker` (or a `worker` service with the same image and env);
   jobs are claimed with `SKIP LOCKED`, so several are safe.
8. Update `start.sh`, `--status`, `--follow` and `backup.sh` (which reads `BACKUP_PATHS` from the host).
