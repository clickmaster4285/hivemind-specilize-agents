"""Health checks for Settings → Logs (BACKLOG L30 T5; the user's choice, 2026-11-20: alerts in a Logs tab, not email
or Telegram). Each check says ok / warn / bad and, when not ok, what to do. Cheap: run when an admin opens the tab.

Also the app log reader: the warnings and errors of logs/api.log, read from the end of the file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.core.config import ROOT, Config

OK, WARN, BAD = "ok", "warn", "bad"

DISK_WARN_FREE, DISK_BAD_FREE = 0.20, 0.10          # share of the disk still free
BACKUP_WARN_AGE, BACKUP_BAD_AGE = timedelta(days=2), timedelta(days=7)   # the timer runs daily
FAILED_WINDOW = timedelta(hours=24)
FAILED_WARN_SHARE = 0.20                              # one answer in five failing is worth a look
GPU_WARN_USED = 0.95                                  # the chat model alone fills ~70% of the 8 GB card
SCAN_WARN_AGE = timedelta(days=14)                    # the scan timer runs weekly
LOG_TAIL_BYTES = 2 * 1024 * 1024                      # how far back the app log is read
LOG_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (WARNING|ERROR|CRITICAL) ([\w.]+): (.*)$")
DEP_SCAN_FILE = "dep-scan.json"                       # written by deploy/scan-deps.sh


def _check(name: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix if status != OK else ""}


def _database(db: Session) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        return _check("Database", OK, "answers")
    except Exception as exc:  # noqa: BLE001
        return _check("Database", BAD, f"does not answer: {type(exc).__name__}", "docker compose up -d db")


def _knowledge_base(config: Config) -> dict[str, str]:
    if not config.brain_enabled:
        return _check("Knowledge base", OK, "switched off (BRAIN_ENABLED=false)")
    try:
        from src.brain.db import connect

        with connect(config) as conn:
            conn.execute("SELECT 1")
        return _check("Knowledge base", OK, "answers")
    except Exception as exc:  # noqa: BLE001
        return _check("Knowledge base", BAD, f"does not answer: {type(exc).__name__}",
                      "check the database container and BRAIN_DATABASE_URL")


def _model(config: Config) -> dict[str, str]:
    from src.llm.llm_client import LLMClient

    if LLMClient(config).ping():
        return _check("Model (Ollama)", OK, f"{config.llm_model} reachable")
    return _check("Model (Ollama)", BAD, "Ollama does not answer — no question can be answered",
                  "sudo systemctl restart ollama")


def _disk() -> dict[str, str]:
    usage = shutil.disk_usage(ROOT)
    free = usage.free / usage.total
    status = BAD if free < DISK_BAD_FREE else WARN if free < DISK_WARN_FREE else OK
    return _check("Disk", status, f"{usage.free / 1e9:.0f} GB free of {usage.total / 1e9:.0f} GB ({free:.0%})",
                  "remove old backups or files, or add a disk")


def backup_dir() -> Path:
    """Where backup.sh writes (BACKUP_DIR, as in deploy/shell-common.sh)."""
    return Path(os.getenv("BACKUP_DIR") or ROOT / "backups")


def _backups(now: datetime) -> dict[str, str]:
    archives = sorted(backup_dir().glob("multeagent-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    if not archives:
        return _check("Backups", BAD, "no backup yet", "sudo deploy/install-services.sh (daily backup), or ./backup.sh")
    age = now - datetime.fromtimestamp(archives[-1].stat().st_mtime, timezone.utc)
    status = BAD if age > BACKUP_BAD_AGE else WARN if age > BACKUP_WARN_AGE else OK
    detail = f"last {archives[-1].name}, {_ago(age)} ago"
    if not (os.getenv("BACKUP_COPY_TO") or "BACKUP_COPY_TO=" in _env_text()):
        detail += " · only on this disk"            # a disk failure would take the backups with it
        status = WARN if status == OK else status
    return _check("Backups", status, detail,
                  "see logs/backup.log; set BACKUP_COPY_TO in .env for a copy off this disk")


def _env_text() -> str:
    try:
        return (ROOT / ".env").read_text(encoding="utf-8")
    except OSError:
        return ""


def _workers(config: Config) -> dict[str, str]:
    from src.jobs import worker

    alive = sum(t.is_alive() for t in worker._worker_threads)
    status = BAD if not alive else WARN if alive < config.worker_threads else OK
    return _check("Job workers", status, f"{alive} of {config.worker_threads} running",
                  "restart the API (sudo systemctl restart multeagent-api)")


def _queue(db: Session, config: Config, now: datetime) -> dict[str, str]:
    from src.core.models_db import Job
    from src.jobs.jobs import queue_depth

    waiting = queue_depth(db)
    since = now - FAILED_WINDOW
    done, failed = db.execute(select(func.count(), func.count().filter(Job.status == "failed"))
                              .where(Job.finished_at >= since)).one()
    share = failed / done if done else 0.0
    status = WARN if waiting >= config.queue_depth_limit // 2 or share > FAILED_WARN_SHARE else OK
    return _check("Questions", status, f"{waiting} waiting · {failed} of {done} failed in 24 h",
                  "open App log below for the errors")


def _gpu(run: Callable[..., Any] = subprocess.run) -> dict[str, str] | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = run(["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=5, check=True).stdout.strip().splitlines()[0]
        used, total, temp = (float(v) for v in out.split(","))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return _check("GPU", WARN, "nvidia-smi gave no answer")
    status = WARN if used / total > GPU_WARN_USED else OK
    return _check("GPU", status, f"{used / 1024:.1f} of {total / 1024:.1f} GB in use · {temp:.0f} °C",
                  "a picture and the chat model at once? it frees itself after the job")


def _dependency_scan(now: datetime) -> dict[str, str]:
    path = ROOT / "logs" / DEP_SCAN_FILE
    if not path.exists():
        return _check("Dependency scan", WARN, "not run yet", "./deploy/scan-deps.sh (weekly once services are installed)")
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _check("Dependency scan", WARN, "last result could not be read", "./deploy/scan-deps.sh")
    counts: dict[str, int] = {}
    for result in found.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            counts[vuln.get("Severity", "UNKNOWN")] = counts.get(vuln.get("Severity", "UNKNOWN"), 0) + 1
    age = now - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    serious = counts.get("CRITICAL", 0) + counts.get("HIGH", 0)
    status = BAD if counts.get("CRITICAL") else WARN if serious or age > SCAN_WARN_AGE else OK
    detail = (", ".join(f"{n} {s.lower()}" for s, n in sorted(counts.items())) or "no known vulnerabilities") + \
        f" · scanned {_ago(age)} ago"
    return _check("Dependency scan", status, detail, "update the packages named in logs/dep-scan.json, then rescan")


def _ago(age: timedelta) -> str:
    hours = age.total_seconds() / 3600
    return f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} days"


def checks(db: Session, config: Config) -> list[dict[str, str]]:
    """Every check, worst first."""
    now = datetime.now(timezone.utc)
    found = [_database(db), _knowledge_base(config), _model(config), _workers(config), _queue(db, config, now),
             _disk(), _backups(now), _gpu(), _dependency_scan(now)]
    order = {BAD: 0, WARN: 1, OK: 2}
    return sorted((c for c in found if c), key=lambda c: order[c["status"]])


def app_log(level: str = "warning", limit: int = 200, path: Path | None = None) -> list[dict[str, str]]:
    """The app log's newest warnings / errors (level "error": errors only), newest first — from the last
    LOG_TAIL_BYTES of logs/api.log, so a big log costs nothing."""
    path = path or ROOT / "logs" / "api.log"
    try:
        with path.open("rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - LOG_TAIL_BYTES))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    wanted = {"ERROR", "CRITICAL"} if level == "error" else {"WARNING", "ERROR", "CRITICAL"}
    out = []
    for line in reversed(lines):
        m = LOG_LINE_RE.match(line)
        if m and m.group(2) in wanted:
            out.append({"at": m.group(1), "level": m.group(2).lower(), "where": m.group(3), "message": m.group(4)})
            if len(out) >= limit:
                break
    return out

