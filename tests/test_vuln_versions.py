"""V1 vulnerability finder (doc/SECURITY_TOOLS_DECISION.md §10): nmap -sV output → versions → CVEs from the offline
NVD list → findings. Offline: nmap's XML and the NVD feed are samples; nothing is run or downloaded."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.core.config import Config
from src.servers import cve_db, nmap_versions, security_scan
from src.servers.cve_db import CveHit, in_range, parse_cpe, version_key
from src.servers.security_scan import HostReport, SecurityResult, Service

NMAP_XML = """<?xml version="1.0"?><nmaprun><host><ports>
<port protocol="tcp" portid="22"><state state="open"/><service name="ssh" product="OpenSSH"
 version="8.9p1 Ubuntu 3ubuntu0.10" extrainfo="Ubuntu Linux; protocol 2.0"><cpe>cpe:/a:openbsd:openssh:8.9p1</cpe>
 <cpe>cpe:/o:linux:linux_kernel</cpe></service></port>
<port protocol="tcp" portid="5432"><state state="open"/><service name="postgresql" product="PostgreSQL DB"
 version="9.6.0 or later"><cpe>cpe:/a:postgresql:postgresql</cpe></service></port>
<port protocol="tcp" portid="3000"><state state="open"/><service name="ppp" servicefp="SF:..."/></port>
<port protocol="tcp" portid="81"><state state="closed"/><service name="http" product="nginx"/></port>
</ports></host></nmaprun>"""


def _feed(*cves: dict) -> dict:
    return {"vulnerabilities": [{"cve": c} for c in cves]}


def _cve(cve_id: str, score: float, *matches: dict, status: str = "Analyzed") -> dict:
    return {"id": cve_id, "vulnStatus": status, "published": "2024-07-01T00:00:00",
            "descriptions": [{"lang": "en", "value": f"{cve_id} description"}],
            "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": score, "baseSeverity": "HIGH"}}]},
            "configurations": [{"nodes": [{"cpeMatch": list(matches)}]}]}


SSH_ALL_BEFORE_9_8 = {"vulnerable": True, "criteria": "cpe:2.3:a:openbsd:openssh:*:*:*:*:*:*:*:*",
                      "versionStartIncluding": "8.5", "versionEndExcluding": "9.8"}
SSH_EXACT_8_9_P1 = {"vulnerable": True, "criteria": "cpe:2.3:a:openbsd:openssh:8.9:p1:*:*:*:*:*:*"}
SSH_OLD_ONLY = {"vulnerable": True, "criteria": "cpe:2.3:a:openbsd:openssh:*:*:*:*:*:*:*:*",
                "versionEndIncluding": "7.4"}
OS_ONLY = {"vulnerable": True, "criteria": "cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*"}


def test_nmap_xml_keeps_open_software_with_products():
    found = nmap_versions.parse_xml(NMAP_XML)
    assert sorted(found) == [22, 5432]
    assert found[22].label == "OpenSSH 8.9p1 Ubuntu 3ubuntu0.10"
    assert found[22].cpes == ["cpe:/a:openbsd:openssh:8.9p1"]                # the OS CPE is not software
    assert nmap_versions.parse_xml("not xml") == {}


def test_nmap_command_is_a_fixed_list():
    cmd = nmap_versions.command("192.168.1.5", [443, 22], 60)
    assert cmd[0] == "nmap" and "-sV" in cmd and "--script" not in cmd
    assert cmd[cmd.index("-p") + 1] == "22,443" and cmd[-1] == "192.168.1.5"


def test_versions_and_cpes():
    assert version_key("8.9p1") > version_key("8.9") and version_key("1.10.0") > version_key("1.9.9")
    assert parse_cpe("cpe:/a:openbsd:openssh:8.9p1") == ("a", "openbsd", "openssh", "8.9p1")
    assert parse_cpe("cpe:2.3:a:openbsd:openssh:8.9:p1:*:*:*:*:*:*") == ("a", "openbsd", "openssh", "8.9p1")
    row = {"version": None, "start_incl": "8.5", "start_excl": None, "end_incl": None, "end_excl": "9.8"}
    assert in_range("8.9p1", row) and not in_range("9.8", row) and not in_range("8.4", row)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "nvd.sqlite"
    feeds = {"modified": _feed(_cve("CVE-2024-6387", 8.1, SSH_ALL_BEFORE_9_8),
                               _cve("CVE-2023-0001", 9.8, SSH_EXACT_8_9_P1),
                               _cve("CVE-2016-0001", 5.0, SSH_OLD_ONLY),
                               _cve("CVE-2024-9999", 7.0, OS_ONLY))}
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    seen: list[str] = []

    def fetch(name: str) -> dict:
        seen.append(name)
        return feeds.get(name, _feed())

    cve_db.update(path, fetch=fetch, now=now, say=lambda _m: None)
    assert seen[0] == str(cve_db.FIRST_FEED_YEAR) and seen[-1] == "modified"   # first build: every year
    seen.clear()
    cve_db.update(path, fetch=fetch, now=now + timedelta(days=1), say=lambda _m: None)
    assert seen == ["modified"]                                                 # then only the changes
    return path


def test_lookup_matches_ranges_and_exact_versions_worst_first(db):
    hits = cve_db.lookup(db, "cpe:/a:openbsd:openssh:8.9p1")
    assert [h.id for h in hits] == ["CVE-2023-0001", "CVE-2024-6387"]
    assert cve_db.lookup(db, "cpe:/a:openbsd:openssh:9.9") == []
    assert cve_db.lookup(db, "cpe:/a:postgresql:postgresql") == []             # no version, no guess
    assert cve_db.status(db)["cves"] == 4


def test_a_renamed_vendor_is_found_but_a_shared_name_is_not(tmp_path):
    path = tmp_path / "nvd.sqlite"
    conn = cve_db.connect(path)
    nginx = {"vulnerable": True, "criteria": "cpe:2.3:a:f5:nginx:*:*:*:*:*:*:*:*", "versionEndExcluding": "1.20.1"}
    shared = [{"vulnerable": True, "criteria": f"cpe:2.3:a:{v}:router:*:*:*:*:*:*:*:*"} for v in ("acme", "zeta")]
    cve_db.import_feed(conn, _feed(_cve("CVE-2021-23017", 7.7, nginx), _cve("CVE-2020-0001", 9.0, shared[0]),
                                   _cve("CVE-2020-0002", 9.0, shared[1])))
    conn.close()
    assert [h.id for h in cve_db.lookup(path, "cpe:/a:igor_sysoev:nginx:1.18.0")] == ["CVE-2021-23017"]
    assert cve_db.lookup(path, "cpe:/a:other:router:1.0") == []


def test_a_rejected_cve_is_removed(db):
    conn = cve_db.connect(db)
    cve_db.import_feed(conn, _feed(_cve("CVE-2023-0001", 9.8, SSH_EXACT_8_9_P1, status="Rejected")))
    conn.close()
    assert [h.id for h in cve_db.lookup(db, "cpe:/a:openbsd:openssh:8.9p1")] == ["CVE-2024-6387"]


def test_check_reads_versions_and_reports_cves(db, monkeypatch):
    monkeypatch.setattr(nmap_versions, "available", lambda: True)
    monkeypatch.setattr(nmap_versions, "detect", lambda ip, ports, timeout: nmap_versions.parse_xml(NMAP_XML))
    config = replace(Config.from_env(), cve_db_path=db)
    host = HostReport(ip="192.168.1.5", services=[Service(22, "SSH", "OpenSSH_8.9p1"), Service(5432, "PostgreSQL")])
    notes = security_scan.read_versions([host], config, should_continue=lambda: True, progress=lambda _m: None)
    assert notes == []
    assert host.services[0].product == "OpenSSH 8.9p1 Ubuntu 3ubuntu0.10"
    assert [h.id for h in host.services[0].cves] == ["CVE-2023-0001", "CVE-2024-6387"]
    [finding] = security_scan.vulnerability_findings(host)
    assert finding.risk == security_scan.HIGH and finding.title.startswith("2 known CVEs for OpenSSH")
    result = SecurityResult("192.168.1.5", [host], [finding], 1.0)
    text = security_scan.markdown(result)
    assert "### Known vulnerabilities" in text and "CVE-2023-0001 (9.8)" in text
    assert security_scan.xlsx_bytes(result)[:2] == b"PK"


def test_without_nmap_or_a_list_the_check_says_so(tmp_path, monkeypatch):
    config = replace(Config.from_env(), cve_db_path=tmp_path / "none.sqlite")
    host = HostReport(ip="192.168.1.5", services=[Service(22, "SSH")])
    monkeypatch.setattr(nmap_versions, "available", lambda: False)
    [note] = security_scan.read_versions([host], config, should_continue=lambda: True, progress=lambda _m: None)
    assert "nmap is not installed" in note
    monkeypatch.setattr(nmap_versions, "available", lambda: True)
    monkeypatch.setattr(nmap_versions, "detect", lambda ip, ports, timeout: {})
    [note] = security_scan.read_versions([host], config, should_continue=lambda: True, progress=lambda _m: None)
    assert "cve_db --update" in note


def test_cve_risk_bands():
    assert [security_scan.cve_risk(s) for s in (9.8, 7.0, 5.0, 2.0, None)] == ["high", "high", "medium", "low", "low"]
    assert CveHit("x", None, "", "").score is None
