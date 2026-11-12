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
#   ./start.sh --stop     stop the API and UI — found by their ports, only this project's processes, with their
#                         children (containers and Ollama keep running)
#   ./start.sh --status   what runs right now: containers, Ollama + loaded model, GPU, API, UI, knowledge base, backups
#   ./start.sh --follow   live output of every part, each line tagged (Ctrl+C stops watching, not the services)
#   ./start.sh --follow api ollama    only these parts (api ui db searxng pgadmin ollama)

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
# --stop: the API finishes open requests and closes its database pool, Next.js stops at once; after this the
# processes are forced (SIGKILL)
STOP_WAIT_SECONDS=15
# compose prints one line per service plus the error itself; this many lines show the cause without the pull progress
COMPOSE_ERROR_LINES=5
# the API and UI logs are appended across restarts; at a start, one bigger than this is moved to <name>.log.1
# (one old file kept): room for many days of chats, still small enough to open in an editor
LOG_ROTATE_BYTES=$((20 * 1024 * 1024))
# --follow first shows this many recent lines per part, so what is happening has some context
FOLLOW_CONTEXT_LINES=5
# --follow parts and their tag colours (ANSI): one colour per kind — host processes, containers, the model
FOLLOW_PARTS=(api ui db searxng pgadmin ollama)
declare -A FOLLOW_COLOR=([api]=32 [ui]=35 [db]=36 [searxng]=36 [pgadmin]=36 [ollama]=33)

TOOLS=0; NO_UI=0; STOP=0; STATUS=0; FOLLOW=0; FOLLOW_ONLY=()
while (($#)); do
    case "$1" in
        --tools) TOOLS=1 ;;
        --no-ui) NO_UI=1 ;;
        --stop)  STOP=1 ;;
        --status) STATUS=1 ;;
        --follow) FOLLOW=1; shift; FOLLOW_ONLY=("$@"); break ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)"; exit 2 ;;
    esac
    shift
done

http_ok()   { curl -fs -o /dev/null --max-time 3 "$1"; }  # quiet: wait_until polls it and reports the outcome itself

wait_for() {  # wait_for <seconds> <command...> — true as soon as the command is, false when time runs out
    local deadline=$((SECONDS + $1)); shift
    while ((SECONDS < deadline)); do
        "$@" && return 0
        sleep "$POLL_SECONDS"
    done
    return 1
}

wait_until() {  # wait_until <seconds> <what> <command...>
    local seconds="$1" what="$2"; shift 2
    wait_for "$seconds" "$@" && return 0
    bad "$what did not become ready within $seconds s"
    return 1
}

# Start a command in its own process group so --stop can end it with all its children (npm -> next -> node)
start_bg() {  # start_bg <name> <dir> <command...>
    local name="$1" dir="$2"; shift 2
    local log="$LOGS/$name.log"
    mkdir -p "$LOGS"
    # appended, so a restart keeps the earlier lines (ISSUES_LOG O48); a big log becomes <name>.log.1 first
    if [[ -f "$log" ]] && (( $(stat -c %s "$log") > LOG_ROTATE_BYTES )); then mv -f "$log" "$log.1"; fi
    printf '\n===== %s  %s started =====\n' "$(date '+%F %T')" "$name" >>"$log"
    (cd "$dir" && setsid nohup "$@" >>"$log" 2>&1 & echo $! >"$LOGS/$name.pid")
}

# ── stopping (ISSUES_LOG O40): found by port, not only by PID file ───────────────────────────────────
# the PIDs listening on a TCP port (ss shows them for this user's processes only)
port_pids() { ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u; }
port_closed() { ! port_open "$1"; }

# true when the process runs from this project: its working directory is the project or inside it
in_project() {
    local cwd
    cwd="$(readlink "/proc/$1/cwd" 2>/dev/null)" || return 1
    [[ "$cwd" == "$ROOT" || "$cwd" == "$ROOT/"* ]]
}

# what to signal for a PID: its whole process group (npm -> sh -> node, started with setsid) when every
# member is this project's and it is not this script's own group; else the process alone
stop_target() {
    local pid="$1" pgid member
    pgid="$(ps -o pgid= -p "$pid" | tr -d ' ')"
    if [[ -n "$pgid" && "$pgid" != "$(ps -o pgid= -p $$ | tr -d ' ')" ]]; then
        for member in $(pgrep -g "$pgid"); do
            in_project "$member" || { echo "$pid"; return; }
        done
        echo "-$pgid"
        return
    fi
    echo "$pid"
}

stop_service() {  # stop_service <name> <port>
    local name="$1" port="$2" pidfile="$LOGS/$1.pid" pids=() targets=() pid target foreign=0
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then pids+=("$(cat "$pidfile")"); fi
    for pid in $(port_pids "$port"); do pids+=("$pid"); done
    for pid in "${pids[@]}"; do
        if in_project "$pid"; then
            target="$(stop_target "$pid")"
            [[ " ${targets[*]} " == *" $target "* ]] || targets+=("$target")
        else
            bad "$name: port $port is used by '$(ps -o args= -p "$pid" | cut -c1-60)' — not this project's, left alone"
            foreign=1
        fi
    done
    rm -f "$pidfile"
    if ((${#targets[@]} == 0)); then
        if ((foreign)); then
            :                                   # said above
        elif port_open "$port"; then
            bad "$name: port $port is used by another user's process — left alone"
        else
            skip "$name not running"
        fi
        return
    fi
    kill -TERM -- "${targets[@]}" 2>/dev/null
    if wait_for "$STOP_WAIT_SECONDS" port_closed "$port"; then
        ok "$name stopped (port $port free)"
        return
    fi
    kill -KILL -- "${targets[@]}" 2>/dev/null
    if wait_for "$STOP_WAIT_SECONDS" port_closed "$port"; then
        ok "$name stopped — forced after $STOP_WAIT_SECONDS s"
    else
        bad "$name: port $port still in use — see: ss -ltnp 'sport = :$port'"
    fi
}

# One line from /api/health: what the health check and --status print
api_summary() {
    curl -fsS --max-time 5 "http://127.0.0.1:$API_PORT/api/health" | "$PYTHON" -c '
import json, sys
h = json.load(sys.stdin)
image = (h.get("image_pipeline") or {}).get("state")
print(f"   \033[32mOK\033[0m   API up - model {h.get("model")}, Ollama reachable: {h.get("llm_reachable")}, "
      f"image model: {image}, jobs waiting: {h.get("queue_depth")}")'
}

pid_note() {  # pid_note <name> — "(pid N, log logs/<name>.log)" when this script started it
    local pidfile="$LOGS/$1.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo " (pid $(cat "$pidfile"), log logs/$1.log)"
    fi
}

show_status() {
    step "Containers"
    if ! docker info >/dev/null 2>&1; then
        bad "Docker is not reachable"
    else
        local any=0 svc state status
        while IFS=$'\t' read -r svc state status; do
            any=1
            if [[ "$state" == running ]]; then ok "$svc: $status"; else bad "$svc: $status"; fi
        done < <(compose ps -a --format '{{.Service}}\t{{.State}}\t{{.Status}}')
        ((any)) || bad "none — start them with ./start.sh"
        status="$(docker ps --filter name='^portainer$' --format '{{.Status}}')"
        [[ -n "$status" ]] && ok "portainer: $status (https://localhost:9443)"
    fi

    step "Ollama"
    if http_ok "$OLLAMA_URL/api/version"; then
        curl -fsS --max-time 5 "$OLLAMA_URL/api/ps" | (cd "$ROOT" && "$PYTHON" -c '
import json, sys
sys.path.insert(0, ".")
try:
    from src.core.config import Config
    embed_model = Config.from_env().model_embed
except Exception:  # a broken .env must not break the status view; the API check reports it
    from src.core.config import Config
    embed_model = Config.model_embed
models = json.load(sys.stdin).get("models") or []
GIB = 1024 ** 3
for m in models:
    size, vram = m.get("size", 0), m.get("size_vram", 0)
    if m["name"] == embed_model and vram < size:
        # src/brain/embed.py asks for num_gpu=0: the chat model keeps the GPU; a search costs ~0.1 s on the CPU
        where = "on the CPU, as intended: the GPU stays free for the chat model"
    elif vram >= size:
        where = "all on the GPU"
    elif vram == 0:
        where = "on the CPU (slow): it did not fit next to what the GPU already held"
    else:
        where = f"{vram / GIB:.1f} of {size / GIB:.1f} GB on the GPU, the rest on the CPU (slower)"
    print(f"   \033[32mOK\033[0m   loaded: {m["name"]} ({size / GIB:.1f} GB, {where})")
if not models:
    print("   \033[90m--   running, no model loaded (it loads on the first chat)\033[0m")')
    else
        bad "not running — start it:  sudo systemctl start ollama"
    fi

    step "GPU"
    if command -v nvidia-smi >/dev/null; then
        nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits |
            while IFS=, read -r name used total busy; do
                ok "$name: ${used// /} / ${total// /} MiB used, ${busy// /} % busy"
            done
    else
        skip "nvidia-smi not found"
    fi

    step "API (port $API_PORT)"
    if port_open "$API_PORT"; then
        api_summary || bad "listening, but /api/health does not answer — see logs/api.log"
        ok "listening$(pid_note api)"
    else
        bad "not running — start it:  ./start.sh"
    fi

    step "UI (port $UI_PORT)"
    if port_open "$UI_PORT"; then ok "http://localhost:$UI_PORT$(pid_note ui)"; else bad "not running — start it:  ./start.sh"; fi

    step "Knowledge base"
    if db_running; then
        local row
        row="$(db_sql "$DB_NAME" "SELECT count(*) FILTER (WHERE enabled), count(*) FILTER (WHERE enabled AND last_checked_at IS NOT NULL),
                   (SELECT count(*) FROM brain.documents), (SELECT count(*) FROM brain.chunks),
                   EXISTS (SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING (pid)
                            WHERE l.locktype = 'advisory' AND l.granted AND a.usename = 'brain_app')
              FROM brain.sources" 2>/dev/null)"
        if [[ -n "$row" ]]; then
            IFS=$'\t' read -r enabled checked docs chunks refreshing <<<"$row"
            ok "$checked of $enabled sources fetched at least once — $docs documents, $chunks chunks"
            if [[ "$refreshing" == t ]]; then
                ok "a refresh is running; latest: $(grep -h "brain refresh" "$LOGS/api.log" 2>/dev/null | tail -n 1 | sed 's/^.*brain\.scheduler: //')"
            else
                skip "no refresh running"
            fi
        else
            bad "not set up yet — ./start.sh does it"
        fi
    else
        bad "database not running"
    fi

    step "Backups ($BACKUP_DIR)"
    local newest
    newest="$(ls -1t "$BACKUP_DIR"/multeagent-*.tar.gz 2>/dev/null | head -n 1)"
    if [[ -n "$newest" ]]; then
        ok "newest: $(basename "$newest") ($(du -h "$newest" | cut -f1), $(( ($(date +%s) - $(stat -c %Y "$newest")) / 3600 )) h ago)"
    else
        bad "none yet — run ./backup.sh"
    fi
}

# Live output of every part, each line tagged; Ctrl+C ends only the watching
follow() {
    local parts=("$@") part color
    ((${#parts[@]})) || parts=("${FOLLOW_PARTS[@]}")
    for part in "${parts[@]}"; do
        [[ -n "${FOLLOW_COLOR[$part]:-}" ]] || { bad "unknown part: $part (${FOLLOW_PARTS[*]})"; return 2; }
    done
    trap 'trap - INT TERM; kill 0' INT TERM
    echo "Watching: ${parts[*]} — Ctrl+C to stop watching (everything keeps running)"
    for part in "${parts[@]}"; do
        color="${FOLLOW_COLOR[$part]}"
        case "$part" in
            api|ui)  tail -n "$FOLLOW_CONTEXT_LINES" -F "$LOGS/$part.log" 2>/dev/null ;;
            ollama)  journalctl -u ollama -f -n "$FOLLOW_CONTEXT_LINES" -o cat 2>&1 ;;
            *)       compose logs -f --tail "$FOLLOW_CONTEXT_LINES" --no-log-prefix "$part" 2>&1 ;;
        esac | sed -u "s/^/$(printf '\033[%sm%-8s\033[0m| ' "$color" "$part")/" &
    done
    wait
}

if ((STATUS)); then
    show_status
    exit 0
fi
if ((FOLLOW)); then
    follow "${FOLLOW_ONLY[@]}"
    exit $?
fi

if ((STOP)); then
    step "Stopping"
    stop_service api "$API_PORT"
    stop_service ui "$UI_PORT"
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
    api_summary
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
echo "What runs:    ./start.sh --status"
echo "Live output:  ./start.sh --follow      (Ctrl+C stops watching only)"
echo "Stop API+UI:  ./start.sh --stop"
