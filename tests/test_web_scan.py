"""V2 web_scan (doc/SECURITY_TOOLS_DECISION.md §14): only plain read-only Nuclei templates may run; Nuclei's JSONL →
findings in security_check. Offline: Nuclei is never run here."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from src.core.config import Config
from src.servers import security_scan as sec
from src.servers import web_scan as ws

SIGNED = "\n# digest: 4a0a00473045022100abc\n"


def t(info: dict, **protocols) -> dict:
    return {"id": "x", "info": {"name": "X", "severity": "medium", **info}, **protocols}


GET = {"method": "GET", "path": ["{{BaseURL}}/.git/config"], "matchers": [{"type": "word", "words": ["[core]"]}]}


@pytest.mark.parametrize("doc, rel, reason", [
    # kept: a plain GET of the device itself, a TLS check, an admin page (info, its folder only)
    (t({"tags": "exposure,git"}, http=[GET]), "http/exposures/configs/git-config.yaml", None),
    (t({"tags": "ssl", "severity": "low"}, ssl=[{"address": "{{Host}}:{{Port}}"}]), "ssl/weak-cipher.yaml", None),
    (t({"tags": "panel", "severity": "info"}, http=[GET]), "http/exposed-panels/grafana.yaml", None),
    (t({"tags": "cve", "severity": "high"}, http=[{"raw": ["GET /version HTTP/1.1\nHost: {{Hostname}}\n\n"]}]),
     "http/cves/2024/CVE-2024-1.yaml", None),
    # refused: what §2 forbids, or could reach beyond the device
    (t({"tags": "cve,lfi,apache"}, http=[GET]), "http/cves/2021/CVE-2021-41773.yaml", "tag lfi"),
    (t({"tags": "cve"}, http=[{"method": "GET", "path": ["{{BaseURL}}/icons/.%2e/%2e%2e/etc/passwd"]}]),
     "http/cves/2021/x.yaml", "attack-looking request (%2e%2e)"),
    (t({"tags": "login"}, http=[{"method": "POST", "path": ["{{BaseURL}}/login"], "body": "user=admin"}]),
     "http/vulnerabilities/x.yaml", "request uses body"),
    (t({"tags": "x"}, http=[{"method": "GET", "path": ["{{BaseURL}}/{{p}}"], "payloads": {"p": ["a", "b"]}}]),
     "http/misconfiguration/x.yaml", "request uses payloads"),
    (t({"tags": "x"}, http=[{"raw": ["POST /api HTTP/1.1\nHost: {{Hostname}}\n\n{\"a\":1}"]}]),
     "http/vulnerabilities/x.yaml", "method POST"),
    (t({"tags": "x"}, http=[{"raw": ["GET /a HTTP/1.1\nHost: {{Hostname}}\n\nbody"]}]),
     "http/vulnerabilities/x.yaml", "request has a body"),
    (t({"tags": "x"}, http=[{"method": "GET", "path": ["{{BaseURL}}/?u=http://{{interactsh-url}}"]}]),
     "http/vulnerabilities/x.yaml", "attack-looking request ({{interactsh)"),
    (t({"tags": "x"}, http=[{"method": "GET", "path": ["https://api.example.com/{{Host}}"]}]),
     "http/misconfiguration/x.yaml", "requests another host"),
    (t({"tags": "x"}, http=[{**GET, "redirects": True}]), "http/misconfiguration/x.yaml",
     "follows redirects (could leave the device)"),
    (t({"tags": "x"}, http=[GET], flow="http(1) && javascript(1)"), "http/misconfiguration/x.yaml", "protocol flow"),
    (t({"tags": "x"}, javascript=[{"code": "x"}]), "javascript/x.yaml", "folder javascript/"),
    (t({"tags": "x"}, network=[{"inputs": [{"data": "x"}]}]), "http/misconfiguration/x.yaml", "protocol network"),
    (t({"tags": "default-login"}, http=[GET]), "http/default-logins/x.yaml", "folder http/default-logins/"),
    (t({"tags": "tech", "severity": "info"}, http=[GET]), "http/misconfiguration/x.yaml", "info only"),
    (t({"tags": "x"}, http=[GET], **{"self-contained": True}), "http/misconfiguration/x.yaml",
     "self-contained (own target)"),
])
def test_only_plain_read_only_templates_are_allowed(doc, rel, reason):
    assert ws.review(doc, rel) == reason


def test_allowlist_is_built_from_signed_templates_and_explained(tmp_path):
    templates = tmp_path / "templates"
    files = {
        "http/exposures/configs/git-config.yaml": (t({"tags": "exposure"}, http=[GET]), True),
        "http/exposed-panels/grafana.yaml": (t({"tags": "panel", "severity": "info"}, http=[GET]), True),
        "http/cves/2021/CVE-2021-41773.yaml": (t({"tags": "cve,lfi"}, http=[GET]), True),
        "http/misconfiguration/unsigned.yaml": (t({"tags": "x"}, http=[GET]), False),
    }
    for rel, (doc, signed) in files.items():
        path = templates / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc) + (SIGNED if signed else ""), encoding="utf-8")
    (templates / ".github").mkdir()
    (templates / ".github" / "ci.yaml").write_text("on: push\n", encoding="utf-8")
    review = ws.write_allowlist(tmp_path)
    kept = (tmp_path / ws.ALLOWLIST_NAME).read_text(encoding="utf-8").split()
    assert sorted(Path(p).name for p in kept) == ["git-config.yaml", "grafana.yaml"]
    assert "2 of 4 templates may run" in review
    assert "| tag lfi | 1 |" in review and "| not signed | 1 |" in review
    assert (tmp_path / ws.REVIEW_NAME).read_text(encoding="utf-8") == review


def test_nuclei_runs_only_the_allowlist_and_sends_nothing_out():
    cmd = ws.command("/tmp/targets.txt", Path("/data/nuclei"))
    assert cmd[cmd.index("-t") + 1] == "/data/nuclei/allowlist.txt"
    assert {"-dut", "-ni", "-duc", "-or", "-jsonl"} <= set(cmd)
    assert cmd[cmd.index("-pt") + 1] == "http,ssl"
    assert int(cmd[cmd.index("-rl") + 1]) <= 30
    assert not {"-fr", "-headless", "-code", "-lfa", "-ev", "-dashboard", "-pd", "-dast", "-ai"} & set(cmd)
    assert "default-login" in cmd[cmd.index("-etags") + 1].split(",")


def test_web_urls_come_from_the_open_web_ports():
    urls = ws.web_urls("192.168.88.53", [22, 80, 443, 8080, 3389], sec.HTTP_PORTS, sec.TLS_HTTP_PORTS)
    assert urls == ["http://192.168.88.53:80", "https://192.168.88.53:443", "http://192.168.88.53:8080"]


JSONL = "\n".join(json.dumps(r) for r in [
    {"template-id": "git-config", "info": {"name": "Git Config - Detect", "severity": "medium",
                                           "description": "Git configuration was exposed.", "remediation": ""},
     "host": "http://192.168.88.53:80", "matched-at": "http://192.168.88.53:80/.git/config", "port": "80"},
    {"template-id": "CVE-2024-1", "info": {"name": "nginx leak", "severity": "high",
                                           "classification": {"cve-id": ["cve-2024-1"]}, "remediation": "Update"},
     "matched-at": "https://192.168.88.53:443/status", "port": 443},
    {"template-id": "git-config", "info": {"name": "dup"}, "matched-at": "http://192.168.88.53:80/.git/config"},
    {"template-id": "grafana-panel", "info": {"name": "Grafana Login Panel", "severity": "info"},
     "matched-at": "http://192.168.88.53:8080/login", "port": "8080"},
]) + "\nnot json\n"


def test_nuclei_jsonl_becomes_findings_worst_first():
    found = ws.parse_jsonl(JSONL)
    assert [(f.template, f.severity, f.port) for f in found] == [
        ("CVE-2024-1", "high", 443), ("git-config", "medium", 80), ("grafana-panel", "info", 8080)]
    assert found[0].cves == ["CVE-2024-1"] and found[0].fix == "Update"
    host = sec.HostReport(ip="192.168.88.53", web=found)
    rows = [(f.risk, f.port, f.title) for f in sec.web_findings(host)]
    assert rows == [(sec.HIGH, 443, "nginx leak (CVE-2024-1)"), (sec.MEDIUM, 80, "Git Config - Detect"),
                    (sec.INFO, 8080, "Admin page reachable: Grafana Login Panel")]


CFG = dataclasses.replace(Config.from_env(), security_scan_enabled=True)


def test_one_device_gets_web_checks_a_network_gets_a_hint(monkeypatch, tmp_path):
    cfg = dataclasses.replace(CFG, nuclei_dir=tmp_path)
    report = sec.HostReport(ip="192.168.88.53", services=[sec.Service(80, "HTTP"), sec.Service(22, "SSH")])
    notes = sec.run_web_checks(report, cfg, should_continue=lambda: True, progress=lambda m: None)
    assert "not installed" in notes[0]                                        # no nuclei / allow-list yet
    seen = []
    monkeypatch.setattr(ws, "available", lambda d: True)
    monkeypatch.setattr(ws, "run", lambda urls, d, **kw: (seen.append(urls) or ws.parse_jsonl(JSONL), ""))
    notes = sec.run_web_checks(report, cfg, should_continue=lambda: True, progress=lambda m: None)
    assert seen == [["http://192.168.88.53:80"]] and len(report.web) == 3 and notes[0].startswith("Web checks:")
    result = sec.SecurityResult("192.168.88.53", [report], sec.web_findings(report), 1.0)
    from io import BytesIO

    from openpyxl import load_workbook

    sheet = load_workbook(BytesIO(sec.xlsx_bytes(result)))["Web checks"]
    assert sheet.max_row == 4 and sheet["F2"].value == "https://192.168.88.53:443/status"


def stats(requests: int, total: int) -> str:
    return json.dumps({"duration": "0:05:00", "requests": str(requests), "total": str(total), "percent": "70"})


def test_a_cut_short_or_dropped_port_is_reported_not_silent():
    full = "[INF] x\n" + stats(10, 2600) + "\n" + stats(2604, 2598) + "\n"
    assert ws.coverage_note(full, "") == ""                              # the whole check ran
    dropped = stats(7000, 10400) + "\n"
    note = ws.coverage_note(dropped, "")
    assert note.startswith("Nuclei skipped part") and "7,000 of 10,400" in note and "(67 %)" in note
    assert ws.coverage_note(dropped, "web checks stopped at the 480 s limit").endswith("(67 %)")
    assert ws.coverage_note("no stats here", "") == ""


def test_time_limit_grows_with_the_web_ports():
    assert ws.time_limit_for(["a"]) == ws.SECONDS_PER_URL
    assert ws.time_limit_for(["a"] * 4) == 4 * ws.SECONDS_PER_URL       # measured: 86 s per port at 30/s
    assert ws.time_limit_for(["a"] * 8) == ws.TIME_LIMIT_SECONDS
    cmd = ws.command("/tmp/t.txt", Path("/d"))
    assert int(cmd[cmd.index("-mhe") + 1]) >= 30 and {"-stats", "-sj"} <= set(cmd)


def test_our_own_self_signed_finding_is_not_repeated_by_nuclei():
    w = ws.WebFinding("self-signed-ssl", "Self Signed SSL Certificate", "low", "192.168.88.53:443", 443, [], "", "")
    host = sec.HostReport(ip="192.168.88.53", services=[sec.Service(443, "HTTPS", tls="self-signed certificate")],
                          web=[w])
    assert sec.web_findings(host) == []
    host.services[0].tls = ""
    assert len(sec.web_findings(host)) == 1
