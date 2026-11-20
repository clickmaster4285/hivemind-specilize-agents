#!/usr/bin/env bash
# Install MulteAgent as systemd services (BACKLOG L30 T1) — run ONCE, with sudo, from the project:
#
#   sudo deploy/install-services.sh            install + enable: API, UI, daily backup, weekly dependency scan, log
#                                              rotation; then start them (run again after an update: it is safe)
#   sudo deploy/install-services.sh --remove   stop, disable and remove them (./start.sh works as before)
#
# The services run as the user who called sudo, from this folder. Before installing, stop what ./start.sh started:
#   ./start.sh --stop
# Afterwards:   systemctl status multeagent-api multeagent-ui     journalctl -u multeagent-api -f
#               sudo systemctl restart multeagent-api              systemctl list-timers multeagent-backup.timer
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNITS=(multeagent-api.service multeagent-ui.service multeagent-backup.service multeagent-backup.timer
       multeagent-depscan.service multeagent-depscan.timer)
LOGROTATE=/etc/logrotate.d/multeagent
TARGET=/etc/systemd/system

[[ $EUID -eq 0 ]] || { echo "run with sudo:  sudo $0 $*"; exit 1; }
RUN_AS="${SUDO_USER:-}"
[[ -n "$RUN_AS" && "$RUN_AS" != root ]] || { echo "run it with sudo from your own account (not as root)"; exit 1; }

if [[ "${1:-}" == "--remove" ]]; then
    systemctl disable --now multeagent-depscan.timer multeagent-backup.timer multeagent-ui.service \
        multeagent-api.service 2>/dev/null || true
    for unit in "${UNITS[@]}"; do rm -f "$TARGET/${unit:?}"; done
    rm -f "$LOGROTATE"
    systemctl daemon-reload
    echo "removed — start the app with ./start.sh again"
    exit 0
fi

# already installed (an update): the ports are our own services' — no need to stop them first
installed=0
systemctl is-active --quiet multeagent-api.service && installed=1
for port in 3000 8787; do
    if ((!installed)) && ss -ltnH "sport = :$port" | grep -q .; then
        echo "port $port is in use — stop the running app first:  ./start.sh --stop   (as $RUN_AS, without sudo)"
        exit 1
    fi
done
[[ -x "$ROOT/.venv/bin/python" ]] || { echo "no virtual environment at $ROOT/.venv"; exit 1; }
[[ -d "$ROOT/frontend/node_modules" ]] || { echo "frontend packages missing: cd frontend && npm install"; exit 1; }
sudo -u "$RUN_AS" mkdir -p "$ROOT/logs"

RUN_HOME="$(getent passwd "$RUN_AS" | cut -d: -f6)"
fill() { sed -e "s|@USER@|$RUN_AS|g" -e "s|@ROOT@|$ROOT|g" -e "s|@HOME@|$RUN_HOME|g" "$1"; }
for unit in "${UNITS[@]}"; do
    fill "$ROOT/deploy/systemd/$unit" >"$TARGET/$unit"
    chmod 644 "$TARGET/$unit"
done
fill "$ROOT/deploy/logrotate/multeagent" >"$LOGROTATE"      # logs rotated daily, 14 kept (BACKLOG L30 T5)
chmod 644 "$LOGROTATE"
systemctl daemon-reload
systemctl enable --now multeagent-api.service multeagent-ui.service multeagent-backup.timer multeagent-depscan.timer
echo
systemctl --no-pager --lines=0 status multeagent-api.service multeagent-ui.service || true
echo
if ((installed)); then
    echo "Updated. Load new code with:  sudo systemctl restart multeagent-api multeagent-ui"
else
    echo "Installed. The UI may take a minute on first start if it has to build."
fi
systemctl list-timers --no-pager multeagent-backup.timer multeagent-depscan.timer | head -4
