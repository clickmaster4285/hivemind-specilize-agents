#!/usr/bin/env bash
# Serve the UI as a production build (the multeagent-ui service, BACKLOG L30 T1): build it again first when its
# code changed since the last build (ui_build_needed in shell-common.sh), then run the UI's own server
# (frontend/server.mjs: Next.js + the caller's address for the API's limits). A failed build stops here, so the
# service shows the failure (journalctl -u multeagent-ui) instead of serving an old or half-made build.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/shell-common.sh"
cd "$ROOT/frontend"
if ui_build_needed; then
    echo "the frontend changed since the last build — building it"
    npm run build
fi
export NODE_ENV=production
exec node server.mjs
