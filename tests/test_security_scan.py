"""security_check (src/security_scan.py): targets, banners, certificates, rules, output, admin-only."""

from __future__ import annotations

import socket
import threading
from dataclasses import replace

import pytest

from src.servers import security_scan as sec
from src.core.config import Config
from src.servers.net_scan import ScanError

CFG = replace(Config.from_env(), security_scan_enabled=True, network_scan_max_hosts=1024)


# ── what to check ────────────────────────────────────────────────────────
def test_targets(monkeypatch):
    monkeypatch.setattr(sec, "own_ips", lambda: {"192.168.88.36"})
    monkeypatch.setattr(sec, "local_subnet", lambda: "192.168.88.0/24")
    assert sec.resolve_target("this pc", "", CFG) == ("this PC", [], True)
    assert sec.resolve_target(None, "is my computer secure?", CFG)[2] is True
    assert sec.resolve_target("192.168.88.10", "", CFG) == ("192.168.88.10", ["192.168.88.10"], False)
    assert sec.resolve_target("192.168.88.36", "", CFG)[2] is True              # its own address = this PC
    assert sec.resolve_target("10.0.0.0/24", "", CFG) == ("10.0.0.0/24", None, False)
    assert sec.resolve_target(None, "which servers have risky ports", CFG) == ("192.168.88.0/24", None, False)


@pytest.mark.parametrize("target", ["8.8.8.8", "1.1.1.0/24"])
def test_public_addresses_are_refused(target):
    with pytest.raises(ScanError, match="not a private"):
        sec.resolve_target(target, "", CFG)


def test_switched_off():
    with pytest.raises(ScanError, match="switched off"):
        sec.check("this pc", "", replace(CFG, security_scan_enabled=False))


# ── what services say ────────────────────────────────────────────────────
@pytest.mark.parametrize("port, banner, product", [
    (22, "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6", "OpenSSH 8.9p1"),
    (22, "SSH-2.0-o4o3e", "unknown software (announces 'o4o3e')"),     # router hiding its software
    (22, "SSH-2.0-dropbear_2020.81", "dropbear 2020.81"),
    (80, "HTTP/1.1 200 OK\r\nServer: nginx/1.18.0 (Ubuntu)\r\n\r\n", "nginx/1.18.0 (Ubuntu)"),
    (80, "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n", "web server (answer 401, no Server header)"),
    (21, "220 (vsFTPd 3.0.3)", "(vsFTPd 3.0.3)"),
    (5900, "RFB 003.008", "VNC protocol 003.008"),
    (22, "", ""),
])
def test_product_from_banner(port, banner, product):
    assert sec.product_from_banner(port, banner) == product


def test_banner_is_read_from_a_real_socket():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def answer():
        conn, _ = server.accept()
        conn.sendall(b"SSH-2.0-OpenSSH_7.4\r\n")
        conn.close()

    threading.Thread(target=answer, daemon=True).start()
    try:
        assert sec.product_from_banner(22, sec._read_banner("127.0.0.1", port)) == "OpenSSH 7.4"
    finally:
        server.close()


@pytest.mark.parametrize("message, risk", [
    ("certificate has expired", sec.MEDIUM),
    ("self-signed certificate", sec.LOW),
    ("unable to get local issuer certificate", sec.LOW),
])
def test_certificate_problems(message, risk):
    assert sec.classify_tls_error(message)[1] == risk


def test_firewall_json_from_any_powershell_version():
    assert sec.parse_firewall('[{"Name":"Domain","Enabled":1},{"Name":"Public","Enabled":false}]') == \
        {"Domain": True, "Public": False}
    assert sec.parse_firewall('{"Name":"Private","Enabled":"True"}') == {"Private": True}


# ── the rules ────────────────────────────────────────────────────────────
def _host(*services: sec.Service) -> sec.HostReport:
    return sec.HostReport(ip="192.168.88.66", services=list(services))


def test_rules_for_a_host_seen_from_outside():
    findings = sec.host_findings(_host(
        sec.Service(23, "Telnet"), sec.Service(80, "HTTP"), sec.Service(22, "SSH", product="OpenSSH 7.4"),
        sec.Service(443, "HTTPS", tls="certificate EXPIRED"),
    ))
    titles = {(f.risk, f.title) for f in findings}
    assert (sec.HIGH, "Telnet open") in titles
    assert (sec.MEDIUM, "Certificate expired") in titles
    assert (sec.LOW, "Old SSH server (OpenSSH 7.4)") in titles
    assert not any(f.title == "Web page without HTTPS" for f in findings)       # 443 is there


def test_http_without_https_and_many_ports():
    plain = sec.host_findings(_host(sec.Service(80, "HTTP")))
    assert [f.title for f in plain] == ["Web page without HTTPS"]
    many = sec.host_findings(_host(*[sec.Service(p, "x") for p in range(10001, 10014)]))
    assert any(f.title == "13 services open" for f in many)


def test_rules_for_this_pc():
    view = sec.LocalView(
        listeners=[sec.Listener("0.0.0.0, ::", 5432, "postgres.exe", True),
                   sec.Listener("127.0.0.1", 6379, "redis.exe", False)],           # local only: no finding
        firewall={"Domain": True, "Public": False}, rdp_on=True, smb1_on=True)
    titles = [f.title for f in sec.local_findings(view)]
    assert "Windows Firewall OFF (Public profile)" in titles
    assert "Remote Desktop is on" in titles and "SMBv1 is on" in titles
    assert "PostgreSQL listening on the network (postgres.exe)" in titles
    assert not any("Redis" in t for t in titles)


# ── output ───────────────────────────────────────────────────────────────
def _result(hosts: list[sec.HostReport]) -> sec.SecurityResult:
    findings = [f for h in hosts for f in sec.host_findings(h)]
    return sec.SecurityResult("192.168.88.0/24", hosts, sorted(findings, key=sec._order), 3.2)


def test_network_findings_are_grouped_with_hosts_in_number_order():
    cams = [sec.HostReport(ip=f"192.168.88.{n}", services=[sec.Service(554, "RTSP (camera)")]) for n in (64, 254, 21)]
    rows = sec.grouped_rows(_result(cams))
    assert len(rows) == 1
    assert rows[0][2] == "3 hosts: 192.168.88.21, 192.168.88.64, 192.168.88.254"
    text = sec.markdown(_result(cams))
    assert "Findings (grouped" in text and "🟠 3 medium" in text


def test_long_service_lists_go_to_the_excel_file_only():
    hosts = [sec.HostReport(ip=f"10.0.0.{n}", services=[sec.Service(22, "SSH")]) for n in range(1, 40)]
    text = sec.markdown(_result(hosts))
    assert "**39 open services**" in text and "| 10.0.0.1 |" not in text
    assert sec.xlsx_bytes(_result(hosts))[:2] == b"PK"                     # a real .xlsx (zip)


def test_this_pc_section_and_known_ports():
    view = sec.LocalView(listeners=[sec.Listener("0.0.0.0", 8787, "python.exe", True),
                                    sec.Listener("0.0.0.0", 49664, "lsass.exe", True)],
                         firewall={"Domain": True}, rdp_on=False, smb1_on=False, api_port=8787)
    result = sec.SecurityResult("this PC", [sec.HostReport(ip="this PC", name="PC1", local=view)], [], 1.0)
    text = sec.markdown(result)
    assert "Windows Firewall: Domain on · Remote Desktop: off · SMBv1: off" in text
    assert "MulteAgent API" in text and "Windows dynamic port (RPC)" in text


# ── admin only ───────────────────────────────────────────────────────────
def test_only_admins_run_it(monkeypatch):
    from src.jobs import worker

    assert "security_check" in worker.ADMIN_TOOLS and worker.DIRECT_TOOLS["security_check"] is sec.run_security_check
    finished, events = [], []
    monkeypatch.setattr(worker, "_finish", lambda config, job_id, errors, **kw: finished.append(job_id))
    worker._refuse(CFG, "job-1", "check this pc", events.append, "security_check")
    done = next(e for e in events if e["type"] == "agent_done")
    assert "only be run by an administrator" in done["output"] and finished == ["job-1"]
