"""image_scan — known vulnerabilities in the Docker images on this PC, by Trivy (doc/SECURITY_TOOLS_DECISION.md §10, V3).

Trivy reads each image's installed packages (Debian, Alpine … and language packages) and compares them with the
distributions' own security lists, so it knows fixes the OS vendor backported — the exact answer V1's version
matching can only call "possible". It answers "CVE-x in openssl 3.0.2-0ubuntu1.10, fixed in 1.12".

Safety: the image names come from `docker image ls`, never from the user's or the model's text (their words only pick
among them); Trivy and docker run as fixed argument lists, read-only (`--scanners vuln`: no secret or config
scanning), admins only (worker ADMIN_TOOLS). Trivy downloads its vulnerability database itself (ghcr.io); nothing
about the images is sent out. Trivy itself must be installed by the user from a verified release (March 2026
supply-chain attack on v0.69.4–0.69.6: SECURITY_TOOLS_DECISION.md §12).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.core.config import Config

logger = logging.getLogger(__name__)

# the first scan also downloads Trivy's database (~ tens of MB); a big image's layers take minutes to read
IMAGE_TIMEOUT_SECONDS = 600
# images checked in one request: this PC's own images are a handful; more is a registry, not a chat answer
MAX_IMAGES = 30
# the worst vulnerabilities shown per image in the chat; every one is in the Excel file
SHOWN_PER_IMAGE = 5
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
SEVERITY_LABEL = {"CRITICAL": "🔴 Critical", "HIGH": "🟠 High", "MEDIUM": "🟡 Medium", "LOW": "⚪ Low",
                  "UNKNOWN": "⚪ Unknown"}
NO_IMAGE = "<none>"          # docker's name for an untagged layer


class ImageScanError(RuntimeError):
    """The scan could not run (no Trivy, no docker, no images)."""


@dataclass
class Vuln:
    id: str
    package: str
    installed: str
    fixed: str            # "" = no fix released yet
    severity: str
    title: str


@dataclass
class ImageReport:
    image: str
    os: str = ""
    vulns: list[Vuln] = field(default_factory=list)
    error: str = ""

    def count(self, severity: str) -> int:
        return sum(v.severity == severity for v in self.vulns)

    @property
    def fixable(self) -> int:
        return sum(bool(v.fixed) for v in self.vulns)


def available() -> bool:
    return shutil.which("trivy") is not None and shutil.which("docker") is not None


def local_images() -> list[str]:
    """repository:tag of every tagged image on this PC, in docker's order (newest first)."""
    done = subprocess.run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
                          capture_output=True, text=True, timeout=30, check=False)
    if done.returncode != 0:
        raise ImageScanError(f"docker could not list the images: {done.stderr.strip()[:200]}")
    names = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return list(dict.fromkeys(n for n in names if NO_IMAGE not in n))


def pick_images(images: list[str], wanted: str | None) -> list[str]:
    """The images the user named (a part of the name: "postgres", "searxng"), or all when none is named."""
    words = [w for w in re.findall(r"[\w./-]+", (wanted or "").lower()) if len(w) > 1]
    if not words:
        return images[:MAX_IMAGES]
    chosen = [i for i in images if any(w in i.lower() for w in words)]
    return chosen[:MAX_IMAGES]


def command(image: str) -> list[str]:
    return ["trivy", "image", "--quiet", "--format", "json", "--scanners", "vuln",
            "--timeout", f"{IMAGE_TIMEOUT_SECONDS}s", image]


def parse_report(image: str, json_text: str) -> ImageReport:
    """Trivy's JSON → the image's vulnerabilities, worst first (one row per CVE and package)."""
    report = ImageReport(image)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        report.error = "Trivy's answer could not be read"
        return report
    meta_os = (data.get("Metadata") or {}).get("OS") or {}
    report.os = " ".join(str(meta_os.get(k) or "") for k in ("Family", "Name")).strip()
    seen: set[tuple[str, str]] = set()
    for result in data.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            key = (str(v.get("VulnerabilityID") or ""), str(v.get("PkgName") or ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            severity = str(v.get("Severity") or "UNKNOWN").upper()
            report.vulns.append(Vuln(key[0], key[1], str(v.get("InstalledVersion") or ""),
                                     str(v.get("FixedVersion") or ""),
                                     severity if severity in SEVERITIES else "UNKNOWN",
                                     str(v.get("Title") or v.get("Description") or "")[:200]))
    report.vulns.sort(key=lambda v: (SEVERITIES.index(v.severity), not v.fixed, v.id))
    return report


def scan_image(image: str) -> ImageReport:
    try:
        done = subprocess.run(command(image), capture_output=True, text=True,
                              timeout=IMAGE_TIMEOUT_SECONDS + 30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ImageReport(image, error=f"Trivy did not finish: {exc}")
    if done.returncode != 0:
        return ImageReport(image, error=(done.stderr.strip().splitlines() or ["Trivy failed"])[-1][:200])
    return parse_report(image, done.stdout)


# ── presentation ─────────────────────────────────────────────────────────
def _cell(text: str) -> str:
    return (text or "—").replace("|", "/").replace("\n", " ")


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join(lines + ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows])


def markdown(reports: list[ImageReport], seconds: float) -> str:
    total = {s: sum(r.count(s) for r in reports) for s in SEVERITIES}
    parts = [f"Checked **{len(reports)} Docker image{'s' if len(reports) != 1 else ''}** on this PC in {seconds:.0f} s: "
             f"**🔴 {total['CRITICAL']} critical · 🟠 {total['HIGH']} high · 🟡 {total['MEDIUM']} medium · "
             f"⚪ {total['LOW']} low** known vulnerabilities in their packages."]
    parts.append(_table(["Image", "OS", "Critical", "High", "Medium", "Low", "Fix available"],
                        [[r.image, r.os, str(r.count("CRITICAL")), str(r.count("HIGH")), str(r.count("MEDIUM")),
                          str(r.count("LOW")), f"{r.fixable} of {len(r.vulns)}"] if not r.error
                         else [r.image, "—", "—", "—", "—", "—", f"not checked: {r.error}"] for r in reports]))
    for r in reports:
        worst = [v for v in r.vulns if v.severity in ("CRITICAL", "HIGH")][:SHOWN_PER_IMAGE]
        if worst:
            parts.append(f"### {r.image} — worst first\n" + _table(
                ["Severity", "CVE", "Package", "Installed", "Fixed in", "What it is"],
                [[SEVERITY_LABEL[v.severity], v.id, v.package, v.installed, v.fixed or "no fix yet", v.title]
                 for v in worst]))
    parts.append("_Read-only check by Trivy of the packages inside each image (the distributions' own fix lists, so "
                 "backported fixes count). To fix: pull a newer tag of the image (`docker pull`), then recreate the "
                 "container; \"no fix yet\" means the vendor has not released one. Every vulnerability is in the "
                 "Excel file._")
    return "\n\n".join(parts)


def xlsx_bytes(reports: list[ImageReport]) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Vulnerabilities"
    header = ["Image", "Severity", "CVE", "Package", "Installed", "Fixed in", "Title"]
    ws.append(header)
    for r in reports:
        for v in r.vulns:
            ws.append([r.image, v.severity, v.id, v.package, v.installed, v.fixed, v.title])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col, width in enumerate((30, 10, 20, 24, 24, 24, 80), start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── as a chat job ────────────────────────────────────────────────────────
def run_image_scan(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    image: str | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'check my docker images for vulnerabilities' → a table per image + .xlsx. No model."""
    from src.jobs import live
    from src.tools.artifacts import artifact_event, write_bytes

    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Image scan", "stage": "chat",
             "accent": "#c45c5c", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "image_scan"})
    try:
        if not available():
            raise ImageScanError("Trivy is not installed on this PC (an admin installs a verified release — "
                                 "doc/SECURITY_TOOLS_DECISION.md §12)")
        everything = local_images()
        images = pick_images(everything, image)
        if not images:
            raise ImageScanError(f"no Docker image on this PC matches '{image}'; there are: {', '.join(everything)}"
                                 if everything else "there are no Docker images on this PC")
        reports: list[ImageReport] = []
        for n, name in enumerate(images, start=1):
            if live.is_stopped(job_id):
                break
            emit({"type": "status", "agent_id": "assistant", "message": f"Checking {name} ({n}/{len(images)})…"})
            reports.append(scan_image(name))
        output = markdown(reports, time.monotonic() - started)
        meta = write_bytes(config, job_id, "docker-images-vulnerabilities.xlsx", xlsx_bytes(reports), kind="xlsx")
        emit(artifact_event(meta, job_id=job_id))
    except ImageScanError as exc:
        output = f"The image check was not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker
        logger.exception("image scan failed")
        output = f"The image check failed: {exc}"
        errors.append(str(exc))
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
