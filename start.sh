#!/usr/bin/env bash
# Start MulteAgent for local development on Linux, in the right order, skipping whatever already runs.
# The Linux version of start.ps1 (hybrid: database + SearXNG in Docker, API + UI on this machine).
#
#   1. Docker -> database (Postgres + pgvector) and SearXNG containers; a fresh database gets its setup
#   2. Ollama (systemd service)
#   3. API  (python main.py --gui)  in the background, log in logs/api.log
#   4. UI   (npm run dev)           in the background, log in logs/ui.log — the LAN's way in (it passes /api on)
#   5. Health check
#
#   ./start.sh            everything
#   ./start.sh --tools    also start pgAdmin (http://localhost:5050)
#   ./start.sh --no-ui    backend only
#   ./start.sh --stop     stop the API and UI this script started (containers and Ollama keep running)

set -u
source "$(dirname "${BASH_SOURCE[0]}")/deploy/shell-common.sh"
LOGS="$ROOT/logs"

# Same default as Next.js; override in the environment if changed
UI_PORT="${UI_PORT:-3000}"
OLLAMA_URL="${LLM_BASE_URL:-http://127.0.0.1:11434}"

# Waits, as in start.ps1: the database needs a first-run init; the API loads models; Next.js compiles on start
DB_WAIT_SECONDS=90
API_WAIT_SECONDS=120
UI_WAIT_SECONDS=120
POLL_SECONDS=2
# compose prints one line per service plus the error itself; this many lines show the cause without the pull progress
COMPOSE_ERROR_LINES=5

TOOLS=0; NO_UI=0; STOP=0
for arg in "$@"; do
    case "$arg" in
        --tools) TOOLS=1 ;;
        --no-ui) NO_UI=1 ;;
        --stop)  STOP=1 ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg (see --help)"; exit 2 ;;
    esac
done

http_ok()   { curl -fs -o /dev/null --max-time 3 "$1"; }  # quiet: wait_until polls it and reports the outcome itself

wait_until() {  # wait_until <seconds> <what> <command...>
    local seconds="$1" what="$2"; shift 2
    local deadline=$((SECONDS + seconds))
    while ((SECONDS < deadline)); do
        "$@" && return 0
        sleep "$POLL_SECONDS"
    done
    bad "$what did not become ready within $seconds s"
    return 1
}

# Start a command in its own process group so --stop can end it with all its children (npm -> next -> node)
start_bg() {  # start_bg <name> <dir> <command...>
    local name="$1" dir="$2"; shift 2
    mkdir -p "$LOGS"
    (cd "$dir" && setsid nohup "$@" >"$LOGS/$name.log" 2>&1 & echo $! >"$LOGS/$name.pid")
}

stop_bg() {
    local name="$1" pidfile="$LOGS/$1.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        kill -- "-$(cat "$pidfile")" 2>/dev/null && ok "$name stopped"
    else
        skip "$name not started by this script"
    fi
    rm -f "$pidfile"
}

if ((STOP)); then
    step "Stopping"
    stop_bg api
    stop_bg ui
    exit 0
fi

# ── 1. Docker + database + search ─────────────────────────────────────
step "Docker, the database and SearXNG"
DB_UP=0
if ! docker_err="$(docker info 2>&1 >/dev/null)"; then
    if grep -qi "permission denied" <<<"$docker_err"; then
        bad "no permission to use Docker — run once:  sudo usermod -aG docker \$USER  then log out and back in"
    else
        bad "Docker is not running — start it:  sudo systemctl start docker"
    fi
    bad "the API keeps users and jobs in the database, so it will not work until Docker is up"
else
    services=(db searxng)
    ((TOOLS)) && services+=(pgadmin)
    profile=(); ((TOOLS)) && profile=(--profile tools)
    if compose_out="$(compose "${profile[@]}" up -d --wait --wait-timeout "$DB_WAIT_SECONDS" "${services[@]}" 2>&1)"; then
        DB_UP=1
        ok "database healthy (127.0.0.1:5432), SearXNG up (127.0.0.1:8888)"
        ((TOOLS)) && ok "pgAdmin: http://localhost:5050"
        # a fresh or restored database still needs its logins, schema and migrations; each step runs only if missing
        if [[ ! -x "$PYTHON" ]]; then
            skip "database setup skipped: no virtual environment yet"
        elif prep="$(cd "$ROOT" && "$PYTHON" -m src.core.app_db prepare 2>&1)"; then
            while IFS= read -r line; do ok "$line"; done <<<"$prep"
        else
            DB_UP=0
            bad "database setup failed: $prep"
        fi
    elif helper="$(grep -o 'docker-credential-[[:alnum:]_.-]*' <<<"$compose_out" | head -n 1)" && [[ -n "$helper" ]]; then
        # typically Docker Desktop's "credsStore", left behind when it is removed; public images need no helper
        bad "Docker cannot pull images: its config names the credential helper $helper, which is not installed"
        bad "fix: remove the \"credsStore\" / \"credHelpers\" entry from ~/.docker/config.json, then run ./start.sh again"
    else
        bad "containers not healthy:"
        tail -n "$COMPOSE_ERROR_LINES" <<<"$compose_out" | sed 's/^/        /'
        bad "more:  docker compose logs db searxng"
    fi
fi

# ── 2. Ollama ─────────────────────────────────────────────────────────
step "Ollama"
if http_ok "$OLLAMA_URL/api/version"; then
    skip "Ollama already running"
elif ! command -v ollama >/dev/null; then
    bad "Ollama is not installed — see https://ollama.com/download/linux"
else
    bad "Ollama is not running — start it:  sudo systemctl start ollama   (chat will not answer until then)"
fi

# ── 3. API ────────────────────────────────────────────────────────────
step "API (port $API_PORT)"
API_EXPECTED=1
if port_open "$API_PORT"; then
    skip "already listening on $API_PORT — assuming the API runs (restart it yourself to load new code)"
elif [[ ! -x "$PYTHON" ]]; then
    API_EXPECTED=0
    bad "virtual environment not found: $PYTHON"
elif ((!DB_UP)); then
    # the API creates its tables and the first admin at startup and exits without the database
    API_EXPECTED=0
    bad "not started: it needs the database (fix the Docker step above, then run ./start.sh again)"
else
    start_bg api "$ROOT" "$PYTHON" main.py --gui
    ok "API started in the background (log: logs/api.log)"
fi

# ── 4. UI ─────────────────────────────────────────────────────────────
if ((!NO_UI)); then
    step "UI (port $UI_PORT)"
    if port_open "$UI_PORT"; then
        skip "already listening on $UI_PORT — assuming the UI runs"
    elif [[ ! -d "$ROOT/frontend/node_modules" ]]; then
        bad "frontend packages missing — run:  cd frontend && npm install"
    else
        start_bg ui "$ROOT/frontend" npm run dev
        ok "UI started in the background (log: logs/ui.log)"
    fi
fi

# ── 5. Health ─────────────────────────────────────────────────────────
step "Health check"
health_url="http://127.0.0.1:$API_PORT/api/health"
if ((!API_EXPECTED)); then
    skip "API not running — nothing to check"
elif wait_until "$API_WAIT_SECONDS" "API" http_ok "$health_url"; then
    curl -fsS --max-time 5 "$health_url" | "$PYTHON" -c '
import json, sys
h = json.load(sys.stdin)
image = (h.get("image_pipeline") or {}).get("state")
print(f"   \033[32mOK\033[0m   API up - model {h.get("model")}, Ollama reachable: {h.get("llm_reachable")}, "
      f"image model: {image}")'
else
    bad "see logs/api.log"
fi
if ((!NO_UI)); then
    if wait_until "$UI_WAIT_SECONDS" "UI" http_ok "http://127.0.0.1:$UI_PORT"; then
        # this machine's LAN addresses: the interfaces that carry a default route (not Docker bridges)
        urls="http://localhost:$UI_PORT"
        for dev in $(ip -4 route show default | awk '{print $5}' | sort -u); do
            for ip in $(ip -4 -o addr show dev "$dev" | awk '{split($4, a, "/"); print a[1]}'); do
                urls+="  http://$ip:$UI_PORT"
            done
        done
        ok "UI: $urls"
    else
        bad "see logs/ui.log"
    fi
fi
echo
echo "Stop the API and UI with:  ./start.sh --stop"
