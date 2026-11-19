"""Exact software versions from nmap's service detection (doc/SECURITY_TOOLS_DECISION.md §10, V1).

`security_check` reads each open port's greeting itself; nmap -sV knows ~12,000 service signatures and also names
the product in the standard CPE form (`cpe:/a:openbsd:openssh:8.9p1`) that the CVE list is keyed by (cve_db.py).

Same safety as the rest of the check: nmap runs as a fixed argument list (never a shell), only on addresses and
ports security_check already found open on a private network, as the normal user (connect scan, no root), with no
NSE scripts, a time limit per host, and its XML read with defusedxml.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field

from defusedxml import ElementTree

logger = logging.getLogger(__name__)

# nmap's default probe depth (0–9): the depth its own documentation recommends for identification
VERSION_INTENSITY = 7
# a subprocess that ignores nmap's own --host-timeout is killed this much later
KILL_GRACE_SECONDS = 10
# the CPE part that names software (a), as opposed to an operating system (o) or hardware (h): only software
# versions are matched to CVEs — "linux_kernel" without a version would match every kernel CVE
SOFTWARE_CPE = "cpe:/a:"


@dataclass
class Detected:
    """What nmap read on one port."""
    product: str = ""          # "OpenSSH"
    version: str = ""          # "8.9p1 Ubuntu 3ubuntu0.10" — as the service announces it
    extrainfo: str = ""        # "Ubuntu Linux; protocol 2.0"
    cpes: list[str] = field(default_factory=list)   # software CPEs only, e.g. "cpe:/a:openbsd:openssh:8.9p1"
    hostname: str = ""         # the name the service announced (SMB, SMTP, HTTP…), if any
    devicetype: str = ""       # nmap's device class for the product: "printer", "webcam", "router", "storage-misc"…

    @property
    def label(self) -> str:
        """"OpenSSH 8.9p1 Ubuntu 3ubuntu0.10" — the software as a person reads it."""
        return " ".join(p for p in (self.product, self.version) if p)


def available() -> bool:
    return shutil.which("nmap") is not None


def command(ip: str, ports: list[int], host_timeout_s: int) -> list[str]:
    """The fixed nmap command line for one host (no user or model text can reach it: ip and ports are checked)."""
    return ["nmap", "-sT", "-sV", "--version-intensity", str(VERSION_INTENSITY), "-Pn", "-n",
            "--host-timeout", f"{host_timeout_s}s", "-p", ",".join(str(int(p)) for p in sorted(ports)),
            "-oX", "-", ip]


def parse_xml(xml_text: str) -> dict[int, Detected]:
    """nmap -oX output → port → what was detected (ports with no service product are left out)."""
    out: dict[int, Detected] = {}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return out
    for port in root.iter("port"):
        state = port.find("state")
        service = port.find("service")
        if state is None or state.get("state") != "open" or service is None or not service.get("product"):
            continue
        cpes = [c.text.strip() for c in service.findall("cpe") if c.text and c.text.startswith(SOFTWARE_CPE)]
        out[int(port.get("portid", "0"))] = Detected(
            product=service.get("product", ""), version=service.get("version", ""),
            extrainfo=service.get("extrainfo", ""), cpes=cpes, hostname=service.get("hostname", ""),
            devicetype=service.get("devicetype", ""))
    return out


def detect(ip: str, ports: list[int], host_timeout_s: int) -> dict[int, Detected]:
    """Run nmap -sV on one host's open ports; {} when nmap is missing, fails or runs out of time."""
    if not ports or not available():
        return {}
    try:
        done = subprocess.run(command(ip, ports, host_timeout_s), capture_output=True, text=True,
                              timeout=host_timeout_s + KILL_GRACE_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("nmap -sV on %s failed: %s", ip, exc)
        return {}
    return parse_xml(done.stdout)
