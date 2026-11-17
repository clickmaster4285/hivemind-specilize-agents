"""V3 image_scan (doc/SECURITY_TOOLS_DECISION.md §10, §12): Trivy's JSON → vulnerabilities per image → table + xlsx.
Offline: Trivy and docker are never run here."""

from __future__ import annotations

import dataclasses
import json

from src.core.config import Config
from src.servers import image_scan
from src.servers.image_scan import ImageReport, parse_report, pick_images

TRIVY_JSON = json.dumps({
    "ArtifactName": "pgvector/pgvector:0.8.0-pg17",
    "Metadata": {"OS": {"Family": "debian", "Name": "12.9"}},
    "Results": [
        {"Target": "pgvector/pgvector:0.8.0-pg17 (debian 12.9)", "Class": "os-pkgs", "Vulnerabilities": [
            {"VulnerabilityID": "CVE-2024-0001", "PkgName": "libssl3", "InstalledVersion": "3.0.15-1",
             "FixedVersion": "3.0.16-1", "Severity": "HIGH", "Title": "openssl: a bug"},
            {"VulnerabilityID": "CVE-2024-0002", "PkgName": "zlib1g", "InstalledVersion": "1.2.13",
             "FixedVersion": "", "Severity": "CRITICAL", "Title": "zlib: overflow"},
            {"VulnerabilityID": "CVE-2024-0001", "PkgName": "libssl3", "Severity": "HIGH"},       # repeated
            {"VulnerabilityID": "CVE-2024-0003", "PkgName": "bash", "Severity": "low"}]},
        {"Target": "usr/local/bin/gosu", "Class": "lang-pkgs", "Vulnerabilities": None}],
})


def test_trivy_json_becomes_one_row_per_cve_and_package_worst_first():
    r = parse_report("pgvector/pgvector:0.8.0-pg17", TRIVY_JSON)
    assert r.os == "debian 12.9" and not r.error
    assert [(v.id, v.severity, v.fixed) for v in r.vulns] == [
        ("CVE-2024-0002", "CRITICAL", ""), ("CVE-2024-0001", "HIGH", "3.0.16-1"), ("CVE-2024-0003", "LOW", "")]
    assert (r.count("CRITICAL"), r.count("HIGH"), r.fixable) == (1, 1, 1)
    assert parse_report("x", "not json").error


def test_the_users_words_only_pick_among_local_images():
    images = ["pgvector/pgvector:0.8.0-pg17", "searxng/searxng:2026.9.30", "dpage/pgadmin4:9.8"]
    assert pick_images(images, None) == images
    assert pick_images(images, "postgres pgvector") == ["pgvector/pgvector:0.8.0-pg17"]
    assert pick_images(images, "nginx; rm -rf /") == []                    # never becomes a command
    assert image_scan.command("pgvector/pgvector:0.8.0-pg17")[:6] == [
        "trivy", "image", "--quiet", "--format", "json", "--scanners"]


def test_report_and_excel():
    reports = [parse_report("pgvector/pgvector:0.8.0-pg17", TRIVY_JSON),
               ImageReport("old:1", error="Trivy did not finish")]
    text = image_scan.markdown(reports, 12.0)
    assert "🔴 1 critical · 🟠 1 high" in text and "CVE-2024-0002" in text and "not checked: Trivy did not finish" in text
    assert "no fix yet" in text and "3.0.16-1" in text
    assert image_scan.xlsx_bytes(reports)[:2] == b"PK"


def test_without_trivy_the_job_says_how_to_get_it(monkeypatch):
    monkeypatch.setattr(image_scan, "available", lambda: False)
    events: list[dict] = []
    output, errors, _ = image_scan.run_image_scan("scan my docker images", job_id="j", config=Config.from_env(),
                                                   on_progress=events.append)
    assert "Trivy is not installed" in output and errors
    assert events[-1]["type"] == "session_done"


def test_the_tool_is_offered_only_when_trivy_is_there(monkeypatch):
    from src.llm.assistant import tool_schemas

    cfg = dataclasses.replace(Config.from_env(), security_scan_enabled=True)
    names = lambda: {t["function"]["name"] for t in tool_schemas(cfg, None)}      # noqa: E731
    monkeypatch.setattr(image_scan, "available", lambda: False)
    assert "image_scan" not in names()
    monkeypatch.setattr(image_scan, "available", lambda: True)
    assert "image_scan" in names()


def test_an_image_is_also_found_by_what_it_runs_and_its_container():
    images = ["pgvector/pgvector:0.8.0-pg17", "dpage/pgadmin4:9.8"]
    also = {"pgvector/pgvector:0.8.0-pg17": "postgres docker-entrypoint.sh multeagent-db-1",
            "dpage/pgadmin4:9.8": "entrypoint.sh multeagent-pgadmin-1"}
    assert pick_images(images, "postgres") == []                             # the name alone does not say it
    assert pick_images(images, "postgres", also) == ["pgvector/pgvector:0.8.0-pg17"]
    assert pick_images(images, "db", also) == ["pgvector/pgvector:0.8.0-pg17"]


def test_trivy_image_sends_nothing_about_the_images():
    assert {"--disable-telemetry", "--skip-version-check", "--offline-scan"} <= set(image_scan.command("x:1"))
