#!/usr/bin/env bash
# Scan our own dependencies for known vulnerabilities (BACKLOG L30 T5): the Python packages installed in .venv (what
# really runs — requirements.txt only has version ranges) and the frontend's package-lock.json. One JSON result,
# logs/dep-scan.json, which Settings → Logs → Health reads. Weekly through multeagent-depscan.timer
# (sudo deploy/install-services.sh); run by hand any time:  ./deploy/scan-deps.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/shell-common.sh"

TRIVY="${TRIVY:-$(command -v trivy || echo "$HOME/.local/bin/trivy")}"
[[ -x "$TRIVY" ]] || { bad "trivy not found (it is in ~/.local/bin on this PC; set TRIVY=/path/to/trivy)"; exit 1; }
mkdir -p "$ROOT/logs"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

step "Python packages (.venv)"
"$TRIVY" rootfs --scanners vuln --format json --quiet --output "$work/python.json" "$ROOT/.venv"
ok "scanned"
step "Frontend packages (package-lock.json)"
"$TRIVY" fs --scanners vuln --format json --quiet --output "$work/frontend.json" "$ROOT/frontend/package-lock.json"
ok "scanned"

# one result for the Logs tab: the targets of both scans; written whole, then moved, so a reader never sees half
"$ROOT/.venv/bin/python" - "$work/python.json" "$work/frontend.json" "$ROOT/logs/dep-scan.json.tmp" <<'PY'
import json, sys
results = []
for path in sys.argv[1:3]:
    results += json.load(open(path, encoding="utf-8")).get("Results") or []
json.dump({"Results": results}, open(sys.argv[3], "w", encoding="utf-8"))
counts = {}
for r in results:
    for v in r.get("Vulnerabilities") or []:
        counts[v["Severity"]] = counts.get(v["Severity"], 0) + 1
print("   " + (", ".join(f"{n} {s.lower()}" for s, n in sorted(counts.items())) or "no known vulnerabilities"))
PY
mv -f "$ROOT/logs/dep-scan.json.tmp" "$ROOT/logs/dep-scan.json"
ok "logs/dep-scan.json — see Settings → Logs → Health"
