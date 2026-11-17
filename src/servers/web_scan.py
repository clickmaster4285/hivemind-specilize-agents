"""Web checks by Nuclei on one device's web ports (doc/SECURITY_TOOLS_DECISION.md §14, V2).

A Nuclei template says: request this page, and if the reply looks like *that*, report *this* — an exposed `.git`
folder, a debug page, a known admin panel, a misconfigured header, a CVE whose page gives it away. Strong where V1
(versions → CVEs) is blind, but some community templates confirm a hole by *exploiting* it (run a command, inject
SQL, read /etc/passwd through a path traversal), which §2 forbids ("no exploits, no password guessing, no flooding").

So Nuclei never gets the template folder, only OUR allow-list of it, built here from each template's content
(`python -m src.servers.web_scan --build`, reviewed by an admin): HTTP and TLS templates whose every request is a
plain GET/HEAD to the target itself — no payload lists, fuzzing, request body, callback server, redirects or
attack-looking path, and no attack tag. Nuclei then runs with: signed templates only (-dut), no callback server
(-ni), no update check (-duc), a low rate limit, private addresses only, a time limit and the Stop button.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ALLOWLIST_NAME = "allowlist.txt"
REVIEW_NAME = "allowlist-review.md"
TEMPLATES_NAME = "templates"
SECONDS_PER_URL = 120        # measured: ~2,600 requests per web port at 30/s = 86 s (192.168.88.53, 2026-11-17)
TIME_LIMIT_SECONDS = 600     # whole run, however many web ports
MAX_HOST_ERRORS = 50         # Nuclei drops a port after this many failed requests (12 seen on a healthy one)
COMPLETE_SHARE = 0.95        # requests sent / planned that counts as the whole check
STATS_SECONDS = 10
RATE_LIMIT = 30              # requests per second in all (Nuclei's default is 150)
CONCURRENCY = 10             # templates at once
REQUEST_TIMEOUT_SECONDS = 5
MAX_URLS = 8                 # web ports of one device
SHOWN = 15                   # findings in the chat; every one is in the Excel file

PROTOCOLS = ("http", "ssl")                       # what may run; every other protocol is refused
REFUSED_PROTOCOLS = ("requests", "network", "tcp", "dns", "file", "headless", "code", "javascript", "whois",
                     "websocket", "workflows", "flow")
SAFE_METHODS = {"GET", "HEAD"}
# tags of templates that attack, guess passwords, flood, or reach outside the network
ATTACK_TAGS = {"intrusive", "dos", "fuzz", "fuzzing", "dast", "brute-force", "bruteforce", "default-login",
               "rce", "sqli", "lfi", "rfi", "traversal", "ssrf", "xxe", "ssti", "injection", "deserialization",
               "file-upload", "fileupload", "upload", "oast", "oob", "crlf", "auth-bypass", "takeover", "osint",
               "unauth-rce", "cmdi", "code-injection", "xss", "redirect", "race"}
# a path or raw request that looks like an exploit attempt (case-insensitive substrings)
ATTACK_MARKS = ("../", "..%2f", "%2e%2e", "..\\", "..;", "/etc/passwd", "win.ini", "<script", "javascript:", "union",
                "select%20", "sleep(", "${", "{{rand", "cmd=", "exec", "eval(", "system(", ";id", "|id", "`",
                "%0d%0a", "%0a", "onerror", "alert(", "{{interactsh", "/bin/", "powershell")
TARGET_VARS = ("{{BaseURL}}", "{{RootURL}}", "{{Hostname}}", "{{Host}}")
SEVERITIES = ("critical", "high", "medium", "low", "info")
# info-level templates are kept only where they mean something to an admin: an admin page reachable
INFO_FOLDERS = ("http/exposed-panels/",)
REFUSED_FOLDERS = ("http/fuzzing/", "http/default-logins/", "http/takeovers/", "http/osint/", "http/technologies/",
                   "dast/", "headless/", "code/", "javascript/", "file/", "cloud/", "workflows/", "helpers/",
                   "profiles/", "network/", "dns/")


class WebScanError(RuntimeError):
    """Nuclei could not run."""


# ── the allow-list ───────────────────────────────────────────────────────
def _tags(info: dict) -> set[str]:
    tags = info.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    return {str(t).strip().lower() for t in tags if str(t).strip()}


def _strings(value) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)


def _request_problem(req: dict) -> str | None:
    """Why one HTTP request is not a plain read of the target, or None."""
    if not isinstance(req, dict):
        return "unreadable request"
    for key in ("payloads", "fuzzing", "attack", "body", "race", "race_count", "unsafe", "pipeline", "threads"):
        if req.get(key):
            return f"request uses {key}"
    if req.get("redirects") or req.get("host-redirects"):
        return "follows redirects (could leave the device)"
    paths = list(_strings(req.get("path"))) if req.get("path") is not None else []
    raws = list(_strings(req.get("raw"))) if req.get("raw") is not None else []
    if not paths and not raws:
        return "no request path"
    if paths and str(req.get("method") or "GET").upper() not in SAFE_METHODS:
        return f"method {str(req.get('method')).upper()}"
    for p in paths:
        if not p.strip().startswith(TARGET_VARS):
            return "requests another host"
    for raw in raws:
        first, _, rest = raw.strip().partition("\n")
        method = first.split(" ", 1)[0].upper()
        if method not in SAFE_METHODS:
            return f"method {method}"
        head, sep, body = rest.replace("\r\n", "\n").partition("\n\n")
        if sep and body.strip():
            return "request has a body"
    text = " ".join(paths + raws).lower()
    mark = next((m for m in ATTACK_MARKS if m in text), None)
    return f"attack-looking request ({mark})" if mark else None


def review(doc: dict, rel_path: str) -> str | None:
    """Why a template may NOT run (a short reason), or None when it is a plain read-only check."""
    if not isinstance(doc, dict) or not isinstance(doc.get("info"), dict):
        return "not a template"
    rel = rel_path.replace("\\", "/")
    folder = next((f for f in REFUSED_FOLDERS if rel.startswith(f)), None)
    if folder:
        return f"folder {folder}"
    info = doc["info"]
    attack = sorted(_tags(info) & ATTACK_TAGS)
    if attack:
        return f"tag {attack[0]}"
    severity = str(info.get("severity") or "info").lower()
    if severity not in SEVERITIES:
        return "no severity"
    if severity == "info" and not rel.startswith(INFO_FOLDERS):
        return "info only"
    refused = next((k for k in REFUSED_PROTOCOLS if k in doc), None)
    if refused:
        return f"protocol {refused}"
    if doc.get("self-contained") or any(isinstance(r, dict) and r.get("self-contained")
                                        for k in PROTOCOLS for r in (doc.get(k) or [])):
        return "self-contained (own target)"
    if not any(doc.get(k) for k in PROTOCOLS):
        return "no http/ssl request"
    for req in doc.get("http") or []:
        problem = _request_problem(req)
        if problem:
            return problem
    for req in doc.get("ssl") or []:
        address = str((req or {}).get("address") or "")
        if address and not address.startswith(TARGET_VARS):
            return "requests another host"
    if any("{{interactsh" in s for s in _strings(doc)):
        return "callback server (interactsh)"
    return None


def build_allowlist(templates: Path) -> tuple[list[Path], Counter, Counter]:
    """Every template under `templates` → (the ones that may run, refusals by reason, kept by severity)."""
    import yaml

    kept: list[Path] = []
    refused: Counter = Counter()
    severity: Counter = Counter()
    for path in sorted(templates.rglob("*.yaml")):
        rel = path.relative_to(templates).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            doc = yaml.safe_load(text)
        except (OSError, yaml.YAMLError):
            refused["unreadable"] += 1
            continue
        if isinstance(doc, dict) and "info" in doc and "# digest:" not in text:
            refused["not signed"] += 1
            continue
        reason = review(doc, rel)
        if reason is None:
            kept.append(path)
            severity[str(doc["info"].get("severity") or "info").lower()] += 1
        elif isinstance(doc, dict) and "info" in doc:
            refused[re.sub(r" \(.*\)$", "", reason)] += 1
    return kept, refused, severity


def write_allowlist(nuclei_dir: Path) -> str:
    """Build data/nuclei/allowlist.txt and allowlist-review.md; → the review text."""
    templates = nuclei_dir / TEMPLATES_NAME
    if not templates.is_dir():
        raise WebScanError(f"no templates in {templates} (SECURITY_TOOLS_DECISION.md §14)")
    kept, refused, severity = build_allowlist(templates)
    (nuclei_dir / ALLOWLIST_NAME).write_text("".join(f"{p}\n" for p in kept), encoding="utf-8")
    total = len(kept) + sum(refused.values())
    lines = [f"# Nuclei allow-list review — {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"{len(kept)} of {total} templates may run (read-only GET/HEAD checks of the device itself).", "",
             "| Kept, by severity | Templates |", "|---|---|"]
    lines += [f"| {s} | {severity[s]} |" for s in SEVERITIES if severity[s]]
    lines += ["", "| Refused because | Templates |", "|---|---|"]
    lines += [f"| {reason} | {n} |" for reason, n in refused.most_common()]
    text = "\n".join(lines) + "\n"
    (nuclei_dir / REVIEW_NAME).write_text(text, encoding="utf-8")
    return text


# ── running Nuclei ───────────────────────────────────────────────────────
@dataclass
class WebFinding:
    template: str
    name: str
    severity: str
    url: str
    port: int | None
    cves: list[str] = field(default_factory=list)
    description: str = ""
    fix: str = ""


def available(nuclei_dir: Path) -> bool:
    return shutil.which("nuclei") is not None and (nuclei_dir / ALLOWLIST_NAME).is_file()


def command(targets_file: str, nuclei_dir: Path) -> list[str]:
    """The fixed Nuclei command line: only the allow-list, nothing sent anywhere but the targets."""
    return ["nuclei", "-l", targets_file, "-t", str(nuclei_dir / ALLOWLIST_NAME),
            "-pt", ",".join(PROTOCOLS), "-etags", ",".join(sorted(ATTACK_TAGS)),
            "-dut",                       # signed templates only (a changed template is skipped)
            "-ni",                        # no interactsh: no callback server on the internet
            "-duc",                       # no update check
            "-rl", str(RATE_LIMIT), "-c", str(CONCURRENCY), "-bs", "4",
            "-timeout", str(REQUEST_TIMEOUT_SECONDS), "-retries", "0", "-mhe", str(MAX_HOST_ERRORS),
            "-jsonl", "-silent", "-nc", "-or",
            "-stats", "-sj", "-si", str(STATS_SECONDS)]   # progress to stderr: was the whole check done?   # -or: no raw request / response in the output


def web_urls(ip: str, ports: Iterable[int], http_ports: set[int], tls_http_ports: set[int]) -> list[str]:
    urls = [f"https://{ip}:{p}" if p in tls_http_ports else f"http://{ip}:{p}"
            for p in sorted(ports) if p in http_ports or p in tls_http_ports]
    return urls[:MAX_URLS]


def parse_jsonl(text: str) -> list[WebFinding]:
    """Nuclei's -jsonl lines → findings, worst first, one per template and URL."""
    out: dict[tuple[str, str], WebFinding] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        info = row.get("info") or {}
        url = str(row.get("matched-at") or row.get("url") or row.get("host") or "")
        key = (str(row.get("template-id") or ""), url)
        if not key[0] or key in out:
            continue
        port = row.get("port")
        try:
            port = int(port) if port not in (None, "") else None
        except (TypeError, ValueError):
            port = None
        cves = (info.get("classification") or {}).get("cve-id") or []
        out[key] = WebFinding(
            template=key[0], name=str(info.get("name") or key[0]),
            severity=str(info.get("severity") or "info").lower(), url=url, port=port,
            cves=[str(c).upper() for c in (cves if isinstance(cves, list) else [cves]) if c],
            description=" ".join(str(info.get("description") or "").split())[:240],
            fix=" ".join(str(info.get("remediation") or "").split())[:240])
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(out.values(), key=lambda f: (order.get(f.severity, 9), f.template, f.url))


def time_limit_for(urls: list[str]) -> float:
    return min(TIME_LIMIT_SECONDS, SECONDS_PER_URL * max(1, len(urls)))


def progress_of(stderr: str) -> tuple[int, int] | None:
    """Nuclei's last -stats -sj line → (requests sent, requests planned). Fewer sent than planned when the run
    was cut short or Nuclei dropped a port that kept failing (-mhe) — which it reports only in debug output."""
    for line in reversed(stderr.splitlines()):
        if line.startswith("{") and '"total"' in line:
            try:
                data = json.loads(line)
                return int(data["requests"]), int(data["total"])
            except (ValueError, KeyError, TypeError):
                return None
    return None


def coverage_note(stderr: str, note: str) -> str:
    done = progress_of(stderr)
    if not done or not done[1] or done[0] >= done[1] * COMPLETE_SHARE:
        return note
    share = f"{done[0]:,} of {done[1]:,} planned requests were sent ({done[0] * 100 // done[1]} %)"
    if note:
        return f"{note}; {share}"
    return f"Nuclei skipped part of the web checks: {share} — a web port kept failing and was dropped"


def run(urls: list[str], nuclei_dir: Path, *, should_continue: Callable[[], bool] = lambda: True,
        time_limit: float | None = None) -> tuple[list[WebFinding], str]:
    """Run Nuclei on the URLs → (findings, a note: "" or why it stopped / failed / skipped a port)."""
    if not urls:
        return [], ""
    time_limit = time_limit_for(urls) if time_limit is None else time_limit
    with tempfile.TemporaryDirectory(prefix="multeagent-nuclei-") as tmp:
        targets = Path(tmp) / "targets.txt"
        targets.write_text("\n".join(urls) + "\n", encoding="utf-8")
        out_path = Path(tmp) / "out.jsonl"
        started = time.monotonic()
        note = ""
        err_path = Path(tmp) / "err.txt"          # a file, not a pipe: a full pipe would stall Nuclei
        with out_path.open("w", encoding="utf-8") as out, err_path.open("w", encoding="utf-8") as err_file, \
                subprocess.Popen(command(str(targets), nuclei_dir), stdout=out, stderr=err_file, text=True) as proc:
            while proc.poll() is None:
                if not should_continue():
                    note = "stopped before the web checks finished"
                    break
                if time.monotonic() - started > time_limit:
                    note = f"web checks stopped at the {time_limit:.0f} s limit; the findings so far are shown"
                    break
                time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        found = parse_jsonl(out_path.read_text(encoding="utf-8", errors="replace"))
        err = err_path.read_text(encoding="utf-8", errors="replace")
        note = coverage_note(err, note)
        if not note and proc.returncode not in (0, None) and not found:
            said = [line for line in err.splitlines() if line.strip() and not line.startswith("{")]
            note = f"Nuclei failed: {(said or ['no message'])[-1][:160]}"
        return found, note


# ── admin command ────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    import argparse

    from src.core.config import Config

    parser = argparse.ArgumentParser(description="Nuclei web checks: build the reviewed allow-list (§14)")
    parser.add_argument("--build", action="store_true", help="review every template and write the allow-list")
    args = parser.parse_args(argv)
    if not args.build:
        parser.print_help()
        return 1
    nuclei_dir = Config.from_env().nuclei_dir
    print(write_allowlist(nuclei_dir))
    print(f"Written: {nuclei_dir / ALLOWLIST_NAME} and {nuclei_dir / REVIEW_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
