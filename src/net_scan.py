"""network_scan — find the devices on a private network and return a table (doc/TOOLS_DECISION.md, Option A).

Safe by construction, not by policy: whatever text asks for a scan (a user, or a web page trying
prompt injection), this module can only do a small, read-only sweep of a PRIVATE network:
  - private ranges only (10/8, 172.16/12, 192.168/16) — public addresses are refused
  - at most NETWORK_SCAN_MAX_HOSTS addresses (default 1024, a /22) and a hard time limit
  - `ping` / `arp` run as fixed argument lists (never through a shell); a fixed list of TCP ports
  - Stop is honoured; NETWORK_SCAN_ENABLED=false switches the tool off
The scan runs where the server runs: it sees the server's network.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from src.config import Config

logger = logging.getLogger(__name__)

TIME_LIMIT_SECONDS = 60
WORKERS = 64
PING_TIMEOUT_MS = 400
TCP_TIMEOUT = 0.3
# devices that ignore ping often answer on one of these (web, ssh, file sharing, RDP, printers, cameras)
PROBE_PORTS = (80, 443, 445, 22)
REPORT_PORTS = (22, 53, 80, 139, 443, 445, 554, 3389, 8080, 9100)
PORT_NAMES = {22: "SSH", 53: "DNS", 80: "HTTP", 139: "NetBIOS", 443: "HTTPS", 445: "SMB",
              554: "RTSP (camera)", 3389: "RDP", 8080: "HTTP-alt", 9100: "printer"}
PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
_SUBNET_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})(?:\s*/\s*(\d{1,2}))?\b")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ScanError(RuntimeError):
    """The scan was refused (not private, too big, switched off) or could not start."""


@dataclass
class Device:
    ip: str
    name: str = ""
    mac: str = ""
    ports: list[int] = field(default_factory=list)
    found_by: str = "ping"
    vendor: str = ""


@dataclass
class ScanResult:
    subnet: str
    scanned: int
    devices: list[Device]
    seconds: float
    stopped: bool = False
    timed_out: bool = False


# ── what to scan ─────────────────────────────────────────────────────────
def validate_subnet(subnet: str, max_hosts: int) -> ipaddress.IPv4Network:
    try:
        net = ipaddress.ip_network(subnet.strip(), strict=False)
    except ValueError as exc:
        raise ScanError(f"not a network address: {subnet!r} (example: 192.168.1.0/24)") from exc
    if not isinstance(net, ipaddress.IPv4Network):
        raise ScanError("only IPv4 networks can be scanned")
    if not any(net.subnet_of(p) for p in PRIVATE_NETS):
        raise ScanError(f"{net} is not a private network — only 10.x, 172.16–31.x and 192.168.x can be scanned")
    if net.num_addresses > max_hosts:
        raise ScanError(f"{net} has {net.num_addresses} addresses; the limit is {max_hosts}. "
                        "Scan a smaller part, e.g. a /24.")
    return net


def subnet_from_text(text: str) -> str | None:
    """'scan 192.168.1.0/24' or 'check 10.0.0.5' → the network named in the message, if any."""
    m = _SUBNET_RE.search(text or "")
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2) or 24}"


def local_subnet() -> str:
    """The server's own LAN: the address Windows uses for outgoing traffic, with its netmask."""
    import psutil

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))          # no packet is sent; it only picks the route
        ip = probe.getsockname()[0]
    finally:
        probe.close()
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET and a.address == ip and a.netmask:
                return str(ipaddress.ip_network(f"{ip}/{a.netmask}", strict=False))
    return str(ipaddress.ip_network(f"{ip}/24", strict=False))


# ── probes (fixed commands, no shell) ────────────────────────────────────
def _ping(ip: str) -> bool:
    cmd = (["ping", "-n", "1", "-w", str(PING_TIMEOUT_MS), ip] if sys.platform == "win32"
           else ["ping", "-c", "1", "-W", "1", ip])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return False
    # Windows returns 0 for "Destination host unreachable" too: a real reply has a TTL
    return out.returncode == 0 and "ttl=" in out.stdout.lower()


def _port_open(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
            return True
    except OSError:
        return False


def _check(ip: str) -> Device | None:
    if _ping(ip):
        found_by = "ping"
    elif any(_port_open(ip, p) for p in PROBE_PORTS):
        found_by = "tcp"
    else:
        return None
    return _enrich(Device(ip=ip, found_by=found_by))


def _enrich(device: Device) -> Device:
    """Open ports and a name: the Windows (NetBIOS) name first, like Advanced IP Scanner, else DNS."""
    device.ports = [p for p in REPORT_PORTS if _port_open(device.ip, p)]
    device.name = _netbios_name(device.ip)
    if not device.name:
        try:
            device.name = socket.gethostbyaddr(device.ip)[0]
        except OSError:
            pass
    return device


# NetBIOS node-status request for "*" (RFC 1002): what Windows itself sends to learn a PC's name
_NBSTAT_QUERY = (b"\x13\x37\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x20" + b"CK" + b"A" * 30 + b"\x00\x00\x21\x00\x01")


def _netbios_name(ip: str, timeout: float = 0.5) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(_NBSTAT_QUERY, (ip, 137))
            data, _ = sock.recvfrom(1024)
    except OSError:
        return ""
    return parse_nbstat(data)


def parse_nbstat(data: bytes) -> str:
    """The unique workstation name (suffix 0x00) from a node-status answer, or ''."""
    try:
        count = data[56]
        for i in range(count):
            entry = data[57 + 18 * i: 57 + 18 * (i + 1)]
            name, suffix, flags = entry[:15], entry[15], int.from_bytes(entry[16:18], "big")
            if suffix == 0x00 and not flags & 0x8000:          # not a group name
                return name.decode("ascii", "replace").strip()
    except (IndexError, ValueError):
        pass
    return ""


# ── manufacturer from the MAC address (IEEE public OUI list, downloaded once) ──
OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
_oui: dict[str, str] | None = None
_oui_lock = __import__("threading").Lock()


def _oui_file():
    from src.config import ROOT

    return ROOT / "data" / "net" / "oui.csv"


def manufacturer(mac: str) -> str:
    if not mac:
        return ""
    first = int(mac[:2], 16)
    if first & 0x02:                       # "locally administered": phones/PCs hiding their real MAC
        return "Private (random MAC)"
    table = _load_oui()
    return table.get(mac.replace(":", "")[:6].upper(), "")


def _load_oui() -> dict[str, str]:
    global _oui
    with _oui_lock:
        if _oui is not None:
            return _oui
        path = _oui_file()
        if not path.is_file():
            try:
                import requests

                resp = requests.get(OUI_URL, timeout=20, headers={"User-Agent": "MulteAgent network_scan"})
                resp.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(resp.content)
            except Exception as exc:  # noqa: BLE001 — no vendor names, the scan still works
                logger.info("OUI list not available: %s", exc)
                _oui = {}
                return _oui
        import csv

        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            _oui = {row[1].strip().upper(): row[2].strip() for row in csv.reader(fh) if len(row) > 2 and len(row[1]) == 6}
        return _oui


def _arp_table() -> dict[str, str]:
    """ip → MAC from the system's ARP cache (filled by the sweep itself)."""
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW).stdout
    except (subprocess.TimeoutExpired, OSError):
        return {}
    table = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})", line)
        if m:
            table[m.group(1)] = m.group(2).replace("-", ":").lower()
    return table


def _own_macs() -> dict[str, str]:
    """ip → MAC of this computer's own network adapters."""
    try:
        import psutil
    except ImportError:
        return {}
    out = {}
    for addrs in psutil.net_if_addrs().values():
        ip = next((a.address for a in addrs if a.family == socket.AF_INET), None)
        mac = next((a.address for a in addrs if a.family == psutil.AF_LINK), None)
        if ip and mac:
            out[ip] = mac.replace("-", ":").lower()
    return out


# ── the scan ─────────────────────────────────────────────────────────────
def scan(
    subnet: str | None,
    config: Config,
    *,
    should_continue: Callable[[], bool] = lambda: True,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> ScanResult:
    if not config.network_scan_enabled:
        raise ScanError("network scanning is switched off (NETWORK_SCAN_ENABLED=false)")
    net = validate_subnet(subnet or local_subnet(), config.network_scan_max_hosts)
    hosts = [str(h) for h in net.hosts()] or [str(net.network_address)]
    started = time.monotonic()
    devices: list[Device] = []
    stopped = timed_out = False
    pool = ThreadPoolExecutor(max_workers=min(WORKERS, len(hosts)))
    try:
        futures = [pool.submit(_check, ip) for ip in hosts]
        for done, fut in enumerate(as_completed(futures), 1):
            if not should_continue():
                stopped = True
                break
            if time.monotonic() - started > TIME_LIMIT_SECONDS:
                timed_out = True
                break
            device = fut.result()
            if device:
                devices.append(device)
            if done % 16 == 0 or done == len(hosts):
                progress(done, len(hosts))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    arp = _arp_table()
    if not stopped and not timed_out:
        # PCs behind Windows Firewall drop ping, but every device must answer ARP: the sweep's own
        # pings filled the ARP table, so devices in it that did not answer ping are alive too
        found = {d.ip for d in devices}
        silent = [ip for ip, mac in arp.items()
                  if ip not in found and ipaddress.ip_address(ip) in net and ip != str(net.broadcast_address)
                  and not mac.startswith(("ff:ff:ff", "01:00:5e"))]
        with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(silent)))) as extra:
            devices += list(extra.map(_enrich, [Device(ip=ip, found_by="arp") for ip in silent]))
    macs = {**arp, **_own_macs()}                # Windows does not list itself in the ARP table
    for d in devices:
        d.mac = macs.get(d.ip, "")
        d.vendor = manufacturer(d.mac)
    devices.sort(key=lambda d: ipaddress.ip_address(d.ip))
    return ScanResult(str(net), len(hosts), devices, round(time.monotonic() - started, 1), stopped, timed_out)


# ── presentation ─────────────────────────────────────────────────────────
def rows(result: ScanResult) -> list[list[str]]:
    header = ["IP address", "Name", "Manufacturer", "MAC address", "Open ports", "Found by"]
    body = [[d.ip, d.name or "—", d.vendor or "—", d.mac or "—",
             ", ".join(f"{p} {PORT_NAMES.get(p, '')}".strip() for p in d.ports) or "—", d.found_by]
            for d in result.devices]
    return [header, *body]


def markdown_table(result: ScanResult) -> str:
    table = rows(result)
    lines = ["| " + " | ".join(table[0]) + " |", "|" + "---|" * len(table[0])]
    lines += ["| " + " | ".join(cell.replace("|", "/") for cell in row) + " |" for row in table[1:]]
    return "\n".join(lines)


def summary(result: ScanResult) -> str:
    text = (f"Scanned **{result.subnet}** ({result.scanned} addresses) in {result.seconds:g} s — "
            f"**{len(result.devices)} device{'s' if len(result.devices) != 1 else ''} answered**.")
    if result.stopped:
        text += " _Stopped early: the list is incomplete._"
    if result.timed_out:
        text += f" _Time limit ({TIME_LIMIT_SECONDS} s) reached: the list may be incomplete._"
    return text


NOTES = ("Found by: ping, an open port (tcp), or only the network's address table (arp — e.g. PCs whose firewall "
         "blocks ping). Names come from Windows (NetBIOS) or DNS and are often missing for phones and IoT devices; "
         "the manufacturer comes from the MAC address (IEEE list).")


def xlsx_bytes(result: ScanResult) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Devices"
    for row in rows(result):
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDEF", (16, 26, 28, 20, 48, 10)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    info = wb.create_sheet("Scan")
    for key, value in (("Network", result.subnet), ("Addresses checked", result.scanned),
                       ("Devices found", len(result.devices)), ("Seconds", result.seconds),
                       ("Complete", "no" if (result.stopped or result.timed_out) else "yes")):
        info.append([key, value])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── as a chat job ────────────────────────────────────────────────────────
def run_network_scan(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    subnet: str | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'check all the IPs … table' → scan → table in the chat + downloadable .xlsx. No LLM involved,
    so the result is exact and fast. → (markdown, errors, seconds)"""
    from src import live
    from src.artifacts import artifact_event, write_bytes

    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Network scan", "stage": "chat",
             "accent": "#3aa89a", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "network_scan"})
    try:
        subnet = (subnet or '').strip() or subnet_from_text(query) or local_subnet()
        emit({"type": "status", "agent_id": "assistant", "message": f"Scanning {subnet}…"})
        result = scan(
            subnet, config,
            should_continue=lambda: not live.is_stopped(job_id),
            progress=lambda done, total: emit({"type": "status", "agent_id": "assistant",
                                               "message": f"Scanning {subnet}… {done}/{total} addresses"}),
        )
        output = f"{summary(result)}\n\n{markdown_table(result)}\n\n_{NOTES}_" if result.devices else summary(result)
        if result.devices:
            meta = write_bytes(config, job_id, f"network-scan-{result.subnet.replace('/', '_')}.xlsx",
                               xlsx_bytes(result), kind="xlsx")
            emit(artifact_event(meta, job_id=job_id))
        if result.stopped:
            agent["role"] = "Stopped"
    except ScanError as exc:
        output = f"The scan was not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker
        logger.exception("network scan failed")
        output = f"The scan failed: {exc}"
        errors.append(str(exc))
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
