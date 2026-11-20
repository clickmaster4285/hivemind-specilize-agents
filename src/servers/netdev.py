"""network_device — read a router or switch (BACKLOG L19 D2): MikroTik RouterOS and Cisco IOS over SSH; switches
that have no usable command line (TP-Link, D-Link, Netgear…) over SNMP (src/servers/snmp.py, the device saved on
port 161 with its community string).

Read-only: each platform has a fixed list of print/show commands (PLATFORMS below) and nothing else is ever
sent; SNMP sends only GET / GETBULK. The platform is the one the device names in its SSH banner. Login and
host-key pinning are the server tools' (linux_audit.connect). Output from the device is untrusted text: parsed,
never executed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.core.config import Config
from src.servers.linux_audit import AuditError, connect
from src.servers.server_vault import ServerLogin, VaultError
from src.tools.tool_run import ToolRun

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
# Cisco IOS / IOS-XE: an exec channel is not paged, so no `terminal length 0` is needed (it would need a shell)
CISCO = Platform("Cisco IOS", "Cisco", {
    "version": "show version",
    "ports": "show interfaces status",
    "ip": "show ip interface brief",
    "macs": "show mac address-table",
    "arp": "show arp",
    "log": "show logging",
})
PLATFORMS = (MIKROTIK, CISCO)


@dataclass
class DeviceReport:
    host: str
    platform: str
    name: str = ""
    info: dict[str, str] = field(default_factory=dict)            # model, version, uptime, cpu, memory…
    interfaces: list[dict[str, str]] = field(default_factory=list)
    leases: list[dict[str, str]] = field(default_factory=list)
    arp: list[dict[str, str]] = field(default_factory=list)
    macs: list[dict[str, str]] = field(default_factory=list)      # a switch's MAC table: who is on which port
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


# ── Cisco IOS output ───────────────────────────────────────────────────

CISCO_MAC_RE = re.compile(r"\b([0-9a-f]{4})\.([0-9a-f]{4})\.([0-9a-f]{4})\b", re.I)
MAC_ROW_RE = re.compile(r"^\s*(\d+|All)\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s+(\S+)\s+(\S+)", re.I | re.M)
ARP_ROW_RE = re.compile(r"^Internet\s+(\S+)\s+\S+\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s+\S+\s+(\S+)",
                        re.I | re.M)
LOG_LINE_RE = re.compile(r"%[A-Z0-9_]+-\d-[A-Z0-9_]+:")       # an IOS message: %LINK-3-UPDOWN: …


def cisco_mac(text: str) -> str:
    """0011.2233.4455 → 00:11:22:33:44:55, the form every other table here uses."""
    m = CISCO_MAC_RE.search(text)
    digits = "".join(m.groups()).lower() if m else ""
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)) if digits else ""


def parse_version(text: str) -> dict[str, str]:
    def find(pattern: str) -> str:
        m = re.search(pattern, text, re.M | re.I)
        return m.group(1).strip() if m else ""
    return {"name": find(r"^(\S+) uptime is"), "uptime": find(r"uptime is (.+)$"),
            "version": find(r"Version ([^,\s]+)"),
            "model": find(r"^Model number\s*:\s*(\S+)") or find(r"^cisco (\S+)"),
            "serial": find(r"^System serial number\s*:\s*(\S+)") or find(r"Processor board ID (\S+)")}


def parse_columns(text: str) -> list[dict[str, str]]:
    """A fixed-width IOS table (`show interfaces status`): the header's word positions cut every row, so a blank
    Name column or a two-word Status stays in its own column."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    head = next((i for i, line in enumerate(lines) if line.lstrip().startswith("Port") and "Status" in line), None)
    if head is None:
        return []
    starts = [m.start() for m in re.finditer(r"\S+", lines[head])]
    names = lines[head].split()
    rows = []
    for line in lines[head + 1:]:
        cells = [line[a:b].strip() for a, b in zip(starts, [*starts[1:], len(line) + 1])]
        if cells and cells[0]:
            rows.append(dict(zip(names, cells)))
    return rows


def parse_ip_brief(text: str) -> list[dict[str, str]]:
    """`show ip interface brief`: Interface, IP, OK?, Method, Status (may be "administratively down"), Protocol."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[0] == "Interface":
            continue
        rows.append({"name": parts[0], "ip": parts[1], "status": " ".join(parts[4:-1]), "protocol": parts[-1]})
    return rows


def _port_status(status: str) -> str:
    status = status.lower()
    return "disabled" if "disabled" in status or "administratively" in status else \
        "up" if status in ("connected", "up") else "down"


def _cisco(outputs: dict[str, str], host: str) -> DeviceReport:
    version = parse_version(outputs["version"])
    ports = parse_columns(outputs["ports"])            # a switch; a router answers this with an error
    interfaces = ([{"name": r.get("Port", ""), "type": r.get("Type", ""), "status": _port_status(r.get("Status", "")),
                    "vlan": r.get("Vlan", ""), "speed": r.get("Speed", ""), "description": r.get("Name", "")}
                   for r in ports] if ports else
                  [{"name": r["name"], "type": r["ip"] if r["ip"] != "unassigned" else "", "status":
                    _port_status(r["status"]), "vlan": "", "speed": "", "description": ""}
                   for r in parse_ip_brief(outputs["ip"])])
    return DeviceReport(
        host=host, platform=CISCO.name, name=version["name"],
        info={k: version[k] for k in ("model", "version", "serial", "uptime")},
        interfaces=interfaces,
        macs=[{"vlan": v, "mac": cisco_mac(mac), "type": kind.lower(), "port": port}
              for v, mac, kind, port in MAC_ROW_RE.findall(outputs["macs"])],
        arp=[{"ip": ip, "mac": cisco_mac(mac), "interface": iface}
             for ip, mac, iface in ARP_ROW_RE.findall(outputs["arp"])],
        log=[line.rstrip() for line in outputs["log"].splitlines() if LOG_LINE_RE.search(line)][-LOG_LINES:],
    )


READERS: dict[str, Callable[[dict[str, str], str], DeviceReport]] = {MIKROTIK.name: _mikrotik, CISCO.name: _cisco}


# ── SNMP (switches without a usable command line) ──────────────────────

SNMP_PLATFORM = "SNMP"
SYS_DESCR, SYS_UPTIME, SYS_NAME = "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.3.0", "1.3.6.1.2.1.1.5.0"
# IF-MIB columns: one walk each, indexed by ifIndex
IF_COLUMNS = {"descr": "1.3.6.1.2.1.2.2.1.2", "iftype": "1.3.6.1.2.1.2.2.1.3", "mac": "1.3.6.1.2.1.2.2.1.6",
              "admin": "1.3.6.1.2.1.2.2.1.7", "oper": "1.3.6.1.2.1.2.2.1.8", "name": "1.3.6.1.2.1.31.1.1.1.1",
              "speed": "1.3.6.1.2.1.31.1.1.1.15", "alias": "1.3.6.1.2.1.31.1.1.1.18"}
Q_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"      # Q-BRIDGE: vlan.mac → bridge port (VLAN-aware switches)
FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"            # BRIDGE-MIB: mac → bridge port (older or simpler switches)
BRIDGE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"  # bridge port → ifIndex, so a MAC gets its port's name
ARP_MAC = "1.3.6.1.2.1.4.22.1.2"               # ipNetToMediaPhysAddress: ifIndex.a.b.c.d → MAC (routing switches)
IF_UP, IF_ADMIN_DOWN = 1, 2                    # ifOperStatus up(1); ifAdminStatus down(2) = switched off
IF_TYPES = {6: "ethernet", 24: "loopback", 53: "virtual", 71: "wifi", 135: "vlan", 136: "vlan", 161: "lag"}


def _column(walk: Callable[[str], list], oid: str) -> dict[str, Any]:
    from src.servers import snmp
    return {snmp.index_suffix(o, oid): v for o, v in walk(oid)}


def snmp_report(host: str, walk: Callable[[str], list], get: Callable[[list[str]], dict]) -> DeviceReport:
    """A switch read over SNMP: walk(oid) and get(oids) are bound to its address and community."""
    from src.servers import snmp

    system = get([SYS_DESCR, SYS_UPTIME, SYS_NAME])
    cols = {key: _column(walk, oid) for key, oid in IF_COLUMNS.items()}
    # ifName ("Gi1/0/1", "1/0/1") when the switch fills it, else ifDescr (a Value is always truthy: compare text)
    names = {i: str(cols["name"].get(i, "")) or str(cols["descr"][i]) for i in cols["descr"]}
    interfaces = []
    for index in sorted(cols["descr"], key=lambda i: int(i) if i.isdigit() else 0):
        admin, oper = (getattr(cols[k].get(index), "number", None) for k in ("admin", "oper"))
        speed = getattr(cols["speed"].get(index), "number", None)
        interfaces.append({"name": names[index], "description": str(cols["alias"].get(index) or ""),
                           "type": IF_TYPES.get(getattr(cols["iftype"].get(index), "number", None) or 0, ""),
                           "status": "disabled" if admin == IF_ADMIN_DOWN else "up" if oper == IF_UP else "down",
                           "speed": f"{speed} Mb/s" if speed else "", "mac": cols["mac"][index].mac
                           if index in cols["mac"] else ""})
    port_if = {bp: str(v) for bp, v in _column(walk, BRIDGE_PORT_IFINDEX).items()}
    macs = []
    for index, port in _column(walk, Q_FDB_PORT).items():
        found = snmp.vlan_mac_from_index(index)
        if found:
            macs.append({"vlan": str(found[0]), "mac": found[1], "type": "dynamic",
                         "port": names.get(port_if.get(str(port), ""), str(port))})
    if not macs:                                   # no Q-BRIDGE: the plain bridge table
        for index, port in _column(walk, FDB_PORT).items():
            if mac := snmp.mac_from_index(index):
                macs.append({"vlan": "", "mac": mac, "type": "dynamic",
                             "port": names.get(port_if.get(str(port), ""), str(port))})
    arp = []
    for index, value in _column(walk, ARP_MAC).items():
        if_index, _, ip = index.partition(".")
        if value.mac:
            arp.append({"ip": ip, "mac": value.mac, "interface": names.get(if_index, if_index)})
    uptime = system.get(SYS_UPTIME)
    return DeviceReport(
        host=host, platform=SNMP_PLATFORM, name=str(system.get(SYS_NAME) or ""),
        info={"model": str(system.get(SYS_DESCR) or ""), "uptime": snmp.uptime_text(uptime.number or 0)
              if uptime is not None else ""},
        interfaces=interfaces, arp=arp, macs=macs)


def uses_snmp(login: ServerLogin) -> bool:
    """A router / switch saved on SNMP's port is read over SNMP; its password is the community string."""
    from src.servers.net_scan import SNMP_PORT
    return login.port == SNMP_PORT


def read_snmp(login: ServerLogin) -> DeviceReport:
    from src.servers import snmp

    community = login.password or ""
    return snmp_report(login.host,
                       walk=lambda oid: snmp.walk(login.host, oid, community=community, port=login.port),
                       get=lambda oids: snmp.get(login.host, oids, community=community, port=login.port))


# ── reading a device ───────────────────────────────────────────────────

def _run(client: Any, command: str) -> str:
    _stdin, stdout, _stderr = client.exec_command(command, timeout=COMMAND_TIMEOUT)
    return stdout.read().decode("utf-8", errors="replace")


def read_device(login: ServerLogin, *,
                open_session: Callable[[ServerLogin], tuple[Any, str]] = connect) -> tuple[DeviceReport, str]:
    """Log in, find the platform from the banner, run its read-only commands, parse them.
    → (report, the host key fingerprint seen)."""
    if uses_snmp(login):
        try:
            return read_snmp(login), ""                       # SNMP has no host key to pin
        except Exception as exc:                              # SnmpError: the reason is already plain words
            raise DeviceError(str(exc)) from exc
    client, fingerprint = open_session(login)
    try:
        banner = client.get_transport().remote_version or ""
        platform = platform_of(banner)
        if platform is None:
            raise DeviceError(f"{login.host} calls itself '{banner}' — not a platform MulteAgent can read yet "
                              f"({', '.join(p.name for p in PLATFORMS)})")
        outputs = {}
        for section, command in platform.commands.items():
            transport = client.get_transport()
            if not getattr(transport, "is_active", lambda: True)():   # some IOS close the session after a command
                client.close()
                client, _fp = open_session(login)
            outputs[section] = _run(client, command)
    finally:
        client.close()
    return READERS[platform.name](outputs, login.host), fingerprint


def test_login(login: ServerLogin) -> dict[str, str]:
    """Adding a device: one read proves the login and the platform → host key to pin + what to show."""
    report, fingerprint = read_device(login)
    return {"host_key": fingerprint,
            "os": " · ".join(p for p in (report.platform, report.info.get("model", ""),
                                         report.info.get("version", "")) if p),
            "access": f"{len(report.interfaces)} interfaces, "
                      + (f"{len(report.macs)} MAC addresses" if report.macs or not report.leases
                         else f"{len(report.leases)} DHCP leases")}


# ── as a chat job ──────────────────────────────────────────────────────

# section title, report field, the columns it may have — a column no row fills is left out (a Cisco switch has
# VLAN and speed per port, a MikroTik a MAC)
SHEETS = (("Interfaces", "interfaces", ("name", "description", "type", "status", "vlan", "speed", "mac")),
          ("DHCP leases", "leases", ("ip", "mac", "host", "status")),
          ("ARP", "arp", ("ip", "mac", "interface")),
          ("MAC table", "macs", ("vlan", "mac", "type", "port")))
MACS_IN_CHAT = 30         # a switch can know hundreds of addresses: the chat shows the first ones, the .xlsx all


def _filled(rows: list[dict[str, str]], columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(c for c in columns if any(r.get(c) for r in rows)) or columns[:1]


def _table(rows: list[dict[str, str]], columns: tuple[str, ...]) -> str:
    lines = ["| " + " | ".join(c.capitalize() for c in columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(r.get(c, "").replace("|", "/") for c in columns) + " |" for r in rows]
    return "\n".join(lines)


def markdown(rep: DeviceReport) -> str:
    about = " · ".join(f"{k} {v}" for k, v in rep.info.items() if v)
    up = sum(i["status"] == "up" for i in rep.interfaces)
    parts = [f"**{rep.name or rep.host}** ({rep.host}, {rep.platform}) — {about}.",
             f"**Interfaces** — {up} of {len(rep.interfaces)} up\n\n"
             + _table(rep.interfaces, _filled(rep.interfaces, SHEETS[0][2]))]
    if rep.leases:
        parts.append(f"**DHCP leases** — {len(rep.leases)} devices\n\n" + _table(rep.leases, SHEETS[1][2]))
    if rep.macs:
        ports = len({m["port"] for m in rep.macs})
        more = f" (first {MACS_IN_CHAT}; all in the .xlsx)" if len(rep.macs) > MACS_IN_CHAT else ""
        parts.append(f"**Who is connected (MAC table)** — {len(rep.macs)} addresses on {ports} ports{more}\n\n"
                     + _table(rep.macs[:MACS_IN_CHAT], _filled(rep.macs, SHEETS[3][2])))
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
    for title, attr, wanted in SHEETS:
        if not getattr(rep, attr) and attr != "interfaces":
            continue                                   # a router has no MAC table, a switch no DHCP leases
        columns = _filled(getattr(rep, attr), wanted)
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
    run = ToolRun(on_progress, query=query, job_id=job_id, role="Network device", model="network_device", accent="#3b82c4")
    errors: list[str] = []
    try:
        devices = server_vault.list_servers(user_id, "network") if user_id is not None else []
        named = device or next((d.host for d in devices if d.host in query), None)
        login = server_vault.find(user_id, named, kind="network") if user_id is not None else None
        if login is None and not devices:
            output = ("No router or switch login for this session yet. Add it in **Settings → Devices** (type: Router "
                      "/ switch) — it is saved for your account, never written in the chat.")
        elif login is None:
            output = f"Which device? Yours: {server_vault.names(devices)}."
        else:
            run.status(f"Reading {login.shown}…")
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
    return run.finish(output, errors)
