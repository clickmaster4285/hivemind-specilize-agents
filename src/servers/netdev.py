"""network_device — read a router or switch (BACKLOG L19 D2): MikroTik RouterOS now, Cisco IOS next.

Read-only: each platform has a fixed list of print/show commands (PLATFORMS below) and nothing else is ever
sent. The platform is the one the device names in its SSH banner. Login and host-key pinning are the
server tools' (linux_audit.connect). Output from the device is untrusted text: parsed, never executed.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.core.config import Config
from src.servers.linux_audit import AuditError, connect
from src.servers.server_vault import ServerLogin, VaultError

logger = logging.getLogger("multeagent.netdev")

COMMAND_TIMEOUT = 20      # s per command: a router answers a print at once; a hung session should not hang the job
LOG_LINES = 30            # the end of the device log: recent events, without flooding the sheet
LOG_LINES_IN_CHAT = 10    # of those, what the chat shows (the rest are in the .xlsx)


class DeviceError(RuntimeError):
    """The device could not be read (unknown platform, login refused, a command failed)."""


@dataclass(frozen=True)
class Platform:
    name: str
    banner: str                   # how its SSH server names itself ("SSH-2.0-<banner>…")
    commands: dict[str, str]      # section → the read-only command that fills it


MIKROTIK = Platform("MikroTik RouterOS", "ROSSSH", {
    "identity": "/system identity print",
    "resource": "/system resource print",
    "interfaces": "/interface print terse without-paging",
    "leases": "/ip dhcp-server lease print terse without-paging",
    "arp": "/ip arp print terse without-paging",
    "log": "/log print without-paging",
})
PLATFORMS = (MIKROTIK,)


@dataclass
class DeviceReport:
    host: str
    platform: str
    name: str = ""
    info: dict[str, str] = field(default_factory=dict)            # model, version, uptime, cpu, memory…
    interfaces: list[dict[str, str]] = field(default_factory=list)
    leases: list[dict[str, str]] = field(default_factory=list)
    arp: list[dict[str, str]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


def platform_of(banner: str) -> Platform | None:
    software = banner.split("-", 2)[-1]                           # "SSH-2.0-ROSSSH" → "ROSSSH"
    return next((p for p in PLATFORMS if software.startswith(p.banner)), None)


# ── RouterOS output ────────────────────────────────────────────────────

PAIR_RE = re.compile(r'([\w.-]+)=("(?:[^"\\]|\\.)*"|\S*)')


def parse_colon(text: str) -> dict[str, str]:
    """`print` of one item: '   uptime: 3w2d4h' lines → {"uptime": "3w2d4h"}."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and " " not in key.strip():
            out[key.strip()] = value.strip()
    return out


def parse_terse(text: str) -> list[dict[str, str]]:
    """`print terse`: one item per line, ' 0  R name=ether1 type=ether …' → {"flags": "R", "name": "ether1", …}.
    The letters before the first key=value are the item's flags (R running, X disabled, D dynamic…)."""
    rows = []
    for line in text.splitlines():
        first = PAIR_RE.search(line)
        if first is None:
            continue
        head = line[:first.start()].split()
        row = {"flags": "".join(t for t in head if not t.isdigit())}
        row.update((k, v.strip('"')) for k, v in PAIR_RE.findall(line))
        rows.append(row)
    return rows


def _mikrotik(outputs: dict[str, str], host: str) -> DeviceReport:
    res = parse_colon(outputs["resource"])
    memory = f"{res['free-memory']} of {res['total-memory']}" if "free-memory" in res and "total-memory" in res else ""
    return DeviceReport(
        host=host, platform=MIKROTIK.name, name=parse_colon(outputs["identity"]).get("name", ""),
        info={"model": res.get("board-name", ""), "version": res.get("version", ""), "uptime": res.get("uptime", ""),
              "cpu load": res.get("cpu-load", ""), "memory free": memory},
        interfaces=[{"name": r.get("name", ""), "type": r.get("type", ""), "mac": r.get("mac-address", ""),
                     "status": "disabled" if "X" in r["flags"] else "up" if "R" in r["flags"] else "down"}
                    for r in parse_terse(outputs["interfaces"])],
        leases=[{"ip": r.get("address", ""), "mac": r.get("mac-address", ""), "host": r.get("host-name", ""),
                 "status": r.get("status", "")} for r in parse_terse(outputs["leases"])],
        arp=[{"ip": r.get("address", ""), "mac": r.get("mac-address", ""), "interface": r.get("interface", "")}
             for r in parse_terse(outputs["arp"])],
        log=[line.rstrip() for line in outputs["log"].splitlines() if line.strip()][-LOG_LINES:],
    )


READERS: dict[str, Callable[[dict[str, str], str], DeviceReport]] = {MIKROTIK.name: _mikrotik}


# ── reading a device ───────────────────────────────────────────────────

def _run(client: Any, command: str) -> str:
    _stdin, stdout, _stderr = client.exec_command(command, timeout=COMMAND_TIMEOUT)
    return stdout.read().decode("utf-8", errors="replace")


def read_device(login: ServerLogin, *,
                open_session: Callable[[ServerLogin], tuple[Any, str]] = connect) -> tuple[DeviceReport, str]:
    """Log in, find the platform from the banner, run its read-only commands, parse them.
    → (report, the host key fingerprint seen)."""
    client, fingerprint = open_session(login)
    try:
        banner = client.get_transport().remote_version or ""
        platform = platform_of(banner)
        if platform is None:
            raise DeviceError(f"{login.host} calls itself '{banner}' — not a platform MulteAgent can read yet "
                              f"({', '.join(p.name for p in PLATFORMS)})")
        outputs = {section: _run(client, command) for section, command in platform.commands.items()}
    finally:
        client.close()
    return READERS[platform.name](outputs, login.host), fingerprint


def test_login(login: ServerLogin) -> dict[str, str]:
    """Adding a device: one read proves the login and the platform → host key to pin + what to show."""
    report, fingerprint = read_device(login)
    return {"host_key": fingerprint,
            "os": " · ".join(p for p in (report.platform, report.info.get("model", ""),
                                         report.info.get("version", "")) if p),
            "access": f"{len(report.interfaces)} interfaces, {len(report.leases)} DHCP leases"}


# ── as a chat job ──────────────────────────────────────────────────────

SHEETS = (("Interfaces", "interfaces", ("name", "type", "status", "mac")),
          ("DHCP leases", "leases", ("ip", "mac", "host", "status")),
          ("ARP", "arp", ("ip", "mac", "interface")))


def _table(rows: list[dict[str, str]], columns: tuple[str, ...]) -> str:
    lines = ["| " + " | ".join(c.capitalize() for c in columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(r.get(c, "").replace("|", "/") for c in columns) + " |" for r in rows]
    return "\n".join(lines)


def markdown(rep: DeviceReport) -> str:
    about = " · ".join(f"{k} {v}" for k, v in rep.info.items() if v)
    up = sum(i["status"] == "up" for i in rep.interfaces)
    parts = [f"**{rep.name or rep.host}** ({rep.host}, {rep.platform}) — {about}.",
             f"**Interfaces** — {up} of {len(rep.interfaces)} up\n\n" + _table(rep.interfaces, SHEETS[0][2])]
    if rep.leases:
        parts.append(f"**DHCP leases** — {len(rep.leases)} devices\n\n" + _table(rep.leases, SHEETS[1][2]))
    parts.append(f"**ARP table** — {len(rep.arp)} entries (all in the .xlsx).")
    if rep.log:
        recent = "\n".join(rep.log[-LOG_LINES_IN_CHAT:]).replace("```", "'''")    # device text cannot close the block
        parts.append(f"**Recent log** (last {min(LOG_LINES_IN_CHAT, len(rep.log))} lines)\n\n```text\n{recent}\n```")
    return "\n\n".join(parts)


def xlsx_bytes(rep: DeviceReport) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.active.title = "Device"
    for key, value in (("Address", rep.host), ("Name", rep.name), ("Platform", rep.platform), *rep.info.items()):
        wb.active.append([key, value])
    for title, attr, columns in SHEETS:
        ws = wb.create_sheet(title)
        ws.append([c.capitalize() for c in columns])
        for row in getattr(rep, attr):
            ws.append([row.get(c, "") for c in columns])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"
    log = wb.create_sheet("Log")
    for line in rep.log:
        log.append([line])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_network_device(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    device: str | None = None,
    user_id: int | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'show me the router' → its login from Settings → Devices → status, interfaces, who is connected,
    recent log + .xlsx. No model: the tables are the device's own answers."""
    from src.servers import server_vault
    from src.tools.artifacts import artifact_event, write_bytes

    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Network device", "stage": "chat",
             "accent": "#3b82c4", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "network_device"})
    try:
        devices = server_vault.list_servers(user_id, "network") if user_id is not None else []
        named = device or next((d.host for d in devices if d.host in query), None)
        login = server_vault.find(user_id, named, kind="network") if user_id is not None else None
        if login is None and not devices:
            output = ("No router or switch login for this session yet. Add it in **Settings → Devices** (type: Router "
                      "/ switch) — it stays in memory for this session only, never in the chat.")
        elif login is None:
            output = "Which device? Added this session: " + ", ".join(f"{d.label} ({d.host})" for d in devices) + "."
        else:
            emit({"type": "status", "agent_id": "assistant", "message": f"Reading {login.host}…"})
            rep, _fingerprint = read_device(login)
            output = markdown(rep)
            meta = write_bytes(config, job_id, f"device-{rep.host}.xlsx", xlsx_bytes(rep), kind="xlsx")
            emit(artifact_event(meta, job_id=job_id))
    except (DeviceError, AuditError, VaultError) as exc:
        output = f"The device could not be read: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("network device read failed: %s", type(exc).__name__)
        output = f"The device could not be read ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
