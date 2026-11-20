"""Settings → Logs (BACKLOG L30 T5): health checks with what to do, and the app log's warnings and errors."""

from __future__ import annotations

import json
import os
import time
from collections import namedtuple
from datetime import datetime, timezone
from types import SimpleNamespace

from src.core import health

NOW = datetime.now(timezone.utc)
Usage = namedtuple("Usage", "total used free")


def test_the_app_log_shows_warnings_and_errors_newest_first(tmp_path):
    log = tmp_path / "api.log"
    log.write_text("\n".join([
        "2026-11-20 09:00:00,001 INFO src.jobs.worker: job_timing job=1 total=3s",
        "2026-11-20 09:01:00,002 WARNING src.tools.web_search: web page not read (https://x): timeout",
        "2026-11-20 09:02:00,003 ERROR src.jobs.worker: job 7 failed: KeyError",
        "  a traceback line that is not a log line",
        "2026-11-20 09:03:00,004 WARNING src.brain.learned: learned recall skipped",
    ]))
    lines = health.app_log(path=log)
    assert [(l["level"], l["where"]) for l in lines] == [
        ("warning", "src.brain.learned"), ("error", "src.jobs.worker"), ("warning", "src.tools.web_search")]
    assert [l["message"] for l in health.app_log("error", path=log)] == ["job 7 failed: KeyError"]
    assert len(health.app_log(limit=1, path=log)) == 1
    assert health.app_log(path=tmp_path / "missing.log") == []


def test_disk_space_turns_amber_then_red(monkeypatch):
    for free, status in [(50, "ok"), (15, "warn"), (5, "bad")]:
        monkeypatch.setattr(health.shutil, "disk_usage", lambda _p, f=free: Usage(100e9, (100 - f) * 1e9, f * 1e9))
        assert health._disk()["status"] == status


def test_backups_are_checked_for_age_and_a_copy_off_the_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "backup_dir", lambda: tmp_path)
    monkeypatch.setattr(health, "_env_text", lambda: "")
    monkeypatch.delenv("BACKUP_COPY_TO", raising=False)
    assert health._backups(NOW)["status"] == "bad"                         # none at all
    old = tmp_path / "multeagent-20261101-023000.tar.gz"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 9 * 86400,) * 2)
    assert health._backups(NOW)["status"] == "bad"                         # 9 days old
    fresh = tmp_path / "multeagent-20261120-023000.tar.gz"
    fresh.write_bytes(b"x")
    check = health._backups(NOW)
    assert check["status"] == "warn" and "only on this disk" in check["detail"]
    monkeypatch.setenv("BACKUP_COPY_TO", "/mnt/nas")
    assert health._backups(NOW)["status"] == "ok"


def test_the_dependency_scan_counts_vulnerabilities(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "ROOT", tmp_path)
    assert health._dependency_scan(NOW)["status"] == "warn"                # not run yet
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / health.DEP_SCAN_FILE).write_text(json.dumps({"Results": [
        {"Target": "requirements.txt", "Vulnerabilities": [{"Severity": "HIGH"}, {"Severity": "LOW"}]},
        {"Target": "frontend/package-lock.json", "Vulnerabilities": [{"Severity": "CRITICAL"}]},
        {"Target": "clean", "Vulnerabilities": None},
    ]}))
    check = health._dependency_scan(NOW)
    assert check["status"] == "bad" and check["detail"].startswith("1 critical, 1 high, 1 low")


def test_the_gpu_line(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda _name: "/usr/bin/nvidia-smi")
    fake = lambda *a, **k: SimpleNamespace(stdout="5800, 8192, 61\n")       # noqa: E731
    check = health._gpu(run=fake)
    assert check["status"] == "ok" and check["detail"] == "5.7 of 8.0 GB in use · 61 °C"
    full = lambda *a, **k: SimpleNamespace(stdout="8100, 8192, 80\n")       # noqa: E731
    assert health._gpu(run=full)["status"] == "warn"


def test_checks_come_worst_first_and_a_problem_says_what_to_do(monkeypatch):
    monkeypatch.setattr(health, "_database", lambda db: health._check("Database", "ok", "answers"))
    monkeypatch.setattr(health, "_knowledge_base", lambda c: health._check("Knowledge base", "ok", "answers"))
    monkeypatch.setattr(health, "_model", lambda c: health._check("Model (Ollama)", "bad", "down", "restart it"))
    monkeypatch.setattr(health, "_workers", lambda c: health._check("Job workers", "ok", "3 of 3"))
    monkeypatch.setattr(health, "_queue", lambda db, c, now: health._check("Questions", "warn", "many", "look"))
    monkeypatch.setattr(health, "_disk", lambda: health._check("Disk", "ok", "plenty", "ignored when ok"))
    monkeypatch.setattr(health, "_backups", lambda now: health._check("Backups", "ok", "fresh"))
    monkeypatch.setattr(health, "_gpu", lambda: None)
    monkeypatch.setattr(health, "_dependency_scan", lambda now: health._check("Dependency scan", "ok", "clean"))
    found = health.checks(None, None)
    assert [c["status"] for c in found][:2] == ["bad", "warn"] and found[0]["fix"] == "restart it"
    assert next(c for c in found if c["name"] == "Disk")["fix"] == ""      # an ok check needs no advice
