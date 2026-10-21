"""ping_host — is one device up? (doc/SECURITY_TOOLS_DECISION.md, option D; IT specialist).

"ping 192.168.88.18 and check it is running" → close to the machine, no model:
  - the system's own `ping` (fixed arguments, 4 echoes): replies, response times, packet loss, TTL
  - the device's name (Windows NetBIOS, then DNS), MAC address and manufacturer (ARP table, IEEE list)
  - which common ports answer — so a PC that blocks ping still shows as "up"
Private addresses only (like network_scan); a typo such as 192.16.88.18 (public) is refused with a
suggestion (192.168.88.18) instead of being silently "corrected". Read-only.
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from src.config import Config
from src.net_scan import (
    PORT_NAMES,
    PRIVATE_NETS,
    REPORT_PORTS,
    ScanError,
    _arp_table,
    _netbios_name,
    _own_macs,
    _port_open,
    manufacturer,
)

logger = logging.getLogger(__name__)

PING_COUNT = 4
PING_TIMEOUT_MS = 1000
NAME_TIMEOUT = 2.0        # seconds to wait for a reverse-DNS name after the ping is done
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


@dataclass
class PingResult:
    sent: int
    times_ms: list[float] = field(default_factory=list)
    ttl: int | None = None

    @property
    def received(self) -> int:
        return len(self.times_ms)

    @property
    def loss_percent(self) -> int:
        return round(100 * (self.sent - self.received) / self.sent) if self.sent else 100


@dataclass
class HostReport:
    target: str
    ip: str
    ping: PingResult
    name: str = ""
    mac: str = ""
    vendor: str = ""
    open_ports: list[int] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def up(self) -> bool:
        return self.ping.received > 0 or bool(self.open_ports)


def typo_hint(address: str) -> str:
    """192.16.88.18 / 192.186.1.5 / 192.68.0.1 — likely meant 192.168.x.y."""
    parts = address.split(".")
    if len(parts) == 4 and parts[0] == "192" and parts[1] in {"16", "18", "68", "186", "618", "1668"}:
        return f"192.168.{parts[2]}.{parts[3]}"
    return ""


def resolve(target: str) -> str:
    """A private IPv4 address (a name is looked up first); anything else is refused, with a typo hint."""
    target = (target or "").strip()
    if not target:
        raise ScanError("which device? Give its IP address, e.g. 192.168.1.20")
    try:
        ip = ipaddress.ip_address(target)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(target))
        except (OSError, ValueError) as exc:
            raise ScanError(f"cannot find a device called '{target}'") from exc
    if not isinstance(ip, ipaddress.IPv4Address) or not any(ip in net for net in PRIVATE_NETS):
        hint = typo_hint(str(ip))
        raise ScanError(f"{ip} is not a private (local network) address — only 10.x, 172.16–31.x and 192.168.x "
                        "can be checked" + (f". Did you mean **{hint}**?" if hint else ""))
    return str(ip)


def pick_target(query: str, host_arg: str | None) -> str:
    """The address exactly as the user typed it wins (the model silently "corrects" typos), then the
    model's argument (a device name like "nas01")."""
    if m := _IP_RE.search(query or ""):
        return m.group(1)
    return (host_arg or "").strip()


def parse_ping(output: str, sent: int) -> PingResult:
    """Windows ("time=1ms TTL=64", "time<1ms") and Linux ("time=0.41 ms ttl=64") reply lines."""
    result = PingResult(sent=sent)
    for line in output.splitlines():
        time_m = re.search(r"time[=<]\s*([\d.]+)\s*ms", line, re.I)
        ttl_m = re.search(r"ttl[=:]\s*(\d+)", line, re.I)
        if time_m and ttl_m:
            result.times_ms.append(float(time_m.group(1)))
            result.ttl = result.ttl or int(ttl_m.group(1))
    return result


def ping(ip: str, count: int = PING_COUNT) -> PingResult:
    cmd = (["ping", "-n", str(count), "-w", str(PING_TIMEOUT_MS), ip] if sys.platform == "win32"
           else ["ping", "-c", str(count), "-W", "1", ip])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=count * 2 + 5, creationflags=_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return PingResult(sent=count)
    return parse_ping(out.stdout, count)


def os_hint(ttl: int | None) -> str:
    """The starting TTL tells the system family (hops on a LAN lower it by 0–2)."""
    if ttl is None:
        return ""
    if ttl <= 64:
        return "likely Linux, macOS, Android or a network device"
    if ttl <= 128:
        return "likely Windows"
    return "likely a router or network equipment"


def _reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


def _result_or_empty(future, timeout: float) -> str:
    try:
        return future.result(timeout=timeout)
    except Exception:  # noqa: BLE001 — a slow or failed name lookup only leaves the name empty
        return ""


def check(target: str, config: Config) -> HostReport:
    if not config.network_scan_enabled:
        raise ScanError("network checks are switched off (NETWORK_SCAN_ENABLED=false)")
    started = time.monotonic()
    ip = resolve(target)
    # everything at once: the ping (~3 s for 4 echoes) is the slowest part; a reverse-DNS lookup of a
    # device without a DNS name can hang ~5 s on Windows, so it gets NAME_TIMEOUT and is otherwise skipped
    pool = ThreadPoolExecutor(max_workers=len(REPORT_PORTS) + 3)
    try:
        pinged = pool.submit(ping, ip)
        netbios = pool.submit(_netbios_name, ip)
        dns = pool.submit(_reverse_dns, ip)
        ports = {port: pool.submit(_port_open, ip, port) for port in REPORT_PORTS}
        report = HostReport(target=target, ip=ip, ping=pinged.result(),
                            open_ports=[p for p, f in ports.items() if f.result()])
        report.name = netbios.result() or _result_or_empty(dns, NAME_TIMEOUT)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    report.mac = {**_arp_table(), **_own_macs()}.get(ip, "")   # filled by the ping on the same network
    report.vendor = manufacturer(report.mac)
    report.seconds = round(time.monotonic() - started, 1)
    return report


def markdown(r: HostReport) -> str:
    p = r.ping
    if p.received:
        times = p.times_ms
        head = (f"**{r.ip} is up** — {p.received}/{p.sent} replies, {p.loss_percent}% loss, "
                f"{sum(times) / len(times):.0f} ms average (min {min(times):g}, max {max(times):g} ms)")
        if p.ttl:
            head += f", TTL {p.ttl} ({os_hint(p.ttl)})"
        head += "."
    elif r.open_ports:
        head = (f"**{r.ip} is up, but does not answer ping** (a firewall blocks it) — it answers on port "
                f"{', '.join(str(x) for x in r.open_ports)}.")
    else:
        head = (f"**{r.ip} does not answer** — no ping replies ({p.sent} sent) and none of the common ports "
                f"({', '.join(str(x) for x in REPORT_PORTS)}) open. It is off, disconnected, on another network, "
                "or blocks everything.")
    ports = ", ".join(f"{x} {PORT_NAMES.get(x, '')}".strip() for x in r.open_ports) or "none of the common ones"
    rows = [("Name", r.name or "—"), ("MAC address", r.mac or "— (not on this network segment, or no reply)"),
            ("Manufacturer", r.vendor or "—"), ("Open ports", ports)]
    table = "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)
    return (f"{head}\n\n{table}\n\n_Checked in {r.seconds:g} s from this server: {p.sent} pings + "
            f"{len(REPORT_PORTS)} common ports. Read-only._")


def run_host_check(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    host: str | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'ping 192.168.88.18' / 'is the printer at 10.0.0.5 up?' → up/down, latency, name, MAC, ports. No model."""
    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Ping", "stage": "chat",
             "accent": "#3aa89a", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "ping_host"})
    target = pick_target(query, host)
    try:
        emit({"type": "status", "agent_id": "assistant", "message": f"Pinging {target or 'the device'}…"})
        output = markdown(check(target, config))
    except ScanError as exc:
        output = f"Not checked: {exc}"
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker
        logger.exception("host check failed")
        output = f"The check failed: {exc}"
        errors.append(str(exc))
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
