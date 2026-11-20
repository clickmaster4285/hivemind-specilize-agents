#!/usr/bin/env bash
# Install MulteAgent as systemd services (BACKLOG L30 T1) — run ONCE, with sudo, from the project:
#
#   sudo deploy/install-services.sh            install + enable: API, UI, daily backup timer; then start them
#   sudo deploy/install-services.sh --remove   stop, disable and remove them (./start.sh works as before)
#
# The services run as the user who called sudo, from this folder. Before installing, stop what ./start.sh started:
#   ./start.sh --stop
# Afterwards:   systemctl status multeagent-api multeagent-ui     journalctl -u multeagent-api -f
#               sudo systemctl restart multeagent-api              systemctl list-timers multeagent-backup.timer
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNITS=(multeagent-api.service multeagent-ui.service multeagent-backup.service multeagent-backup.timer)
TARGET=/etc/systemd/system

[[ $EUID -eq 0 ]] || { echo "run with sudo:  sudo $0 $*"; exit 1; }
RUN_AS="${SUDO_USER:-}"
[[ -n "$RUN_AS" && "$RUN_AS" != root ]] || { echo "run it with sudo from your own account (not as root)"; exit 1; }

if [[ "${1:-}" == "--remove" ]]; then
    systemctl disable --now multeagent-backup.timer multeagent-ui.service multeagent-api.service 2>/dev/null || true
    for unit in "${UNITS[@]}"; do rm -f "$TARGET/${unit:?}"; done
    systemctl daemon-reload
    echo "removed — start the app with ./start.sh again"
    exit 0
fi

for port in 3000 8787; do
    if ss -ltnH "sport = :$port" | grep -q .; then
        echo "port $port is in use — stop the running app first:  ./start.sh --stop   (as $RUN_AS, without sudo)"
        exit 1
    fi
done
[[ -x "$ROOT/.venv/bin/python" ]] || { echo "no virtual environment at $ROOT/.venv"; exit 1; }
[[ -d "$ROOT/frontend/node_modules" ]] || { echo "frontend packages missing: cd frontend && npm install"; exit 1; }
sudo -u "$RUN_AS" mkdir -p "$ROOT/logs"

for unit in "${UNITS[@]}"; do
    sed -e "s|@USER@|$RUN_AS|g" -e "s|@ROOT@|$ROOT|g" "$ROOT/deploy/systemd/$unit" >"$TARGET/$unit"
    chmod 644 "$TARGET/$unit"
done
systemctl daemon-reload
systemctl enable --now multeagent-api.service multeagent-ui.service multeagent-backup.timer
echo
systemctl --no-pager --lines=0 status multeagent-api.service multeagent-ui.service || true
echo
echo "Installed. The UI may take a minute on first start if it has to build. Next backup:"
systemctl list-timers --no-pager multeagent-backup.timer | head -3
