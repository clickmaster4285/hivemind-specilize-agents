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

from src.core.config import Config

logger = logging.getLogger(__name__)

TIME_LIMIT_SECONDS = 60
WORKERS = 64
PING_TIMEOUT_MS = 400
TCP_TIMEOUT = 0.3
# devices that ignore ping often answer on one of these (web, ssh, file sharing, RDP, printers, cameras)
PROBE_PORTS = (80, 443, 445, 22)
REPORT_PORTS = (22, 53, 80, 139, 443, 445, 502, 554, 3389, 8000, 8080, 9100, 37777)
PORT_NAMES = {22: "SSH", 53: "DNS", 80: "HTTP", 139: "NetBIOS", 443: "HTTPS", 445: "SMB",
              502: "Modbus", 554: "RTSP (camera)", 3389: "RDP", 8000: "Hikvision", 8080: "HTTP-alt",
              9100: "printer", 37777: "Dahua"}
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
    type: str = ""        # inferred kind (router, printer, ip camera, pc, phone, nas, server, …) — classify()
    model: str = ""       # model / identification offered by SSDP / mDNS / SNMP (phase 2–3); often empty
    serial: str = ""      # serial number — only SNMP (public) or the authenticated SSH path provide it


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
    from src.core.config import ROOT

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


# ── device type (inferred from signals already collected — no extra traffic) ──
# OUI company-name substrings (lowercased) → device type. First match wins.
VENDOR_TYPES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("hewlett packard", "hp inc", "canon", "brother", "epson", "lexmark", "kyocera",
      "ricoh", "xerox", "zebra", "seiko epson"), "printer"),
    (("hikvision", "dahua", "axis comm", "uniview", "reolink", "amcrest", "hanwha", "vivotek"), "ip camera"),
    (("sma solar", "fronius", "solaredge", "growatt", "sungrow", "goodwe", "deye", "sofar solar",
      "enphase", "victron"), "inverter"),
    (("mikrotik", "ubiquiti", "tp-link", "netgear", "d-link", "zyxel", "aruba", "ruckus",
      "cambium", "fortinet", "sophos"), "router"),
    (("cisco", "juniper", "arista", "hewlett packard enterprise", "extreme networks"), "switch"),
    (("synology", "qnap", "western digital", "buffalo", "netgear storage"), "nas"),
    (("raspberry pi",), "server"),
    (("apple",), "phone"),                       # refined to pc below if it shares files / allows RDP
    (("samsung electro", "xiaomi", "huawei", "oneplus", "guangdong oppo", "vivo mobile",
      "motorola mobility", "google, inc", "googlefi"), "phone"),
    (("dell", "lenovo", "asustek", "acer", "micro-star", "gigabyte", "intel corporate",
      "liteon", "wistron", "compal", "pegatron"), "pc"),
    (("sonos", "roku", "amazon techno", "google home", "nest labs", "espressif",
      "tuya", "sichuan ai-link", "shenzhen"), "iot device"),
)

# Explicit words in a device's name or model (NetBIOS/DNS, or an SSDP/SNMP model string) are the
# strongest signal — checked before ports/vendor. First match wins.
NAME_TYPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bnvr|network video recorder"), "nvr"),
    (re.compile(r"inverter|sun2000|solaredge|growatt|sungrow|goodwe|fronius|\bsma\b|enphase|\bdeye\b"), "inverter"),
    (re.compile(r"\bnas\b|synology|qnap|diskstation|terramaster"), "nas"),
    (re.compile(r"camera|ip ?cam|webcam|\bipc\b|doorbell|surveillance"), "ip camera"),
    (re.compile(r"printer|laserjet|officejet|\bmfp\b|scanner|workcentre"), "printer"),
    (re.compile(r"\bswitch\b|catalyst|nexus"), "switch"),
    (re.compile(r"router|gateway|archer|\bunifi\b|mikrotik|\bomada\b|access ?point|\bap\b|\beero\b|\bdeco\b"), "router"),
    (re.compile(r"\btv\b|roku|chromecast|fire ?stick|apple ?tv|bravia|smart ?tv|\bshield\b"), "media player"),
    (re.compile(r"iphone|ipad|galaxy|pixel|redmi|oneplus|android"), "phone"),
    (re.compile(r"esp32|esp8266|tasmota|shelly|sonoff|\btuya\b|smart ?plug|thermostat|\bhue\b"), "iot device"),
    (re.compile(r"macbook|\bimac\b|thinkpad|desktop|laptop|workstation|-pc\b|\bpc-"), "pc"),
)


def classify(d: Device, gateway_ip: str = "") -> str:
    """A device's kind from name/model words + ports + vendor + whether it's the gateway. '' when unsure.
    Returns plain words that match the diagram icon vocabulary (frontend/src/lib/deviceIcons.ts)."""
    ports = set(d.ports)
    vendor = (d.vendor or "").lower()
    text = f"{d.name} {d.model}".lower()
    if gateway_ip and d.ip == gateway_ip:
        return "router"
    for rx, kind in NAME_TYPES:                   # an explicit word in the name/model is decisive
        if rx.search(text):
            return kind
    if 9100 in ports:
        return "printer"
    if 502 in ports:                              # Modbus TCP — solar inverters / industrial controllers
        return "inverter"
    if ports & {554, 8000, 37777}:                # RTSP / Hikvision / Dahua
        return "ip camera"
    for needles, kind in VENDOR_TYPES:
        if any(n in vendor for n in needles):
            if kind == "phone" and ({445, 139, 3389} & ports):   # a Mac/phone that shares files is a computer
                return "pc"
            return kind
    if {445, 139} & ports or 3389 in ports or d.name:   # Windows file sharing / RDP / a NetBIOS-or-DNS name
        return "pc"
    if 22 in ports and not ({80, 443} & ports):
        return "server"
    return ""                                     # web-only or silent: leave to SSDP/SNMP (phase 2–3)


def default_gateway() -> str:
    """Best-effort default-gateway IP — one signal for classify(); '' if it can't be read."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True, text=True,
                                 timeout=5, creationflags=_NO_WINDOW).stdout
            for line in out.splitlines():
                p = line.split()
                if len(p) >= 3 and p[0] == "0.0.0.0" and p[1] == "0.0.0.0":
                    try:
                        return str(ipaddress.ip_address(p[2]))
                    except ValueError:
                        continue
        else:
            out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"default via (\d{1,3}(?:\.\d{1,3}){3})", out)
            if m:
                return m.group(1)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


# ── identification: SSDP/UPnP + mDNS (read-only LAN discovery; best-effort) ──
SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_MSEARCH = ("M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 1\r\n"
                "ST: ssdp:all\r\n\r\n").encode()
# UPnP deviceType class word → our vocabulary
UPNP_TYPES = {"internetgatewaydevice": "router", "wandevice": "router", "wlanaccesspoint": "router",
              "mediarenderer": "media player", "mediaserver": "nas", "printer": "printer",
              "digitalsecuritycamera": "ip camera"}


def _http_header(text: str, name: str) -> str:
    m = re.search(rf"^{name}\s*:\s*(.+?)\s*$", text, re.I | re.M)
    return m.group(1) if m else ""


def ssdp_locations(timeout: float = 2.5) -> dict[str, str]:
    """ip → first UPnP description URL (the LOCATION header) from one multicast M-SEARCH."""
    out: dict[str, str] = {}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        sock.sendto(SSDP_MSEARCH, SSDP_ADDR)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                break
            loc = _http_header(data.decode("latin-1", "replace"), "location")
            if loc and addr[0] not in out:
                out[addr[0]] = loc
    except OSError:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return out


def parse_upnp(xml: str) -> dict[str, str]:
    """friendlyName / modelName+modelNumber / manufacturer / mapped type from a UPnP description XML."""
    def tag(name: str) -> str:
        m = re.search(rf"<(?:\w+:)?{name}>(.*?)</(?:\w+:)?{name}>", xml, re.I | re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    model = " ".join(x for x in (tag("modelName"), tag("modelNumber")) if x).strip()
    upnp_type = ""
    m = re.search(r":device:([A-Za-z0-9]+):", tag("deviceType"))
    if m:
        upnp_type = UPNP_TYPES.get(m.group(1).lower(), "")
    return {"name": tag("friendlyName"), "model": model,
            "manufacturer": tag("manufacturer"), "type": upnp_type}


def ssdp_describe(location: str, timeout: float = 2.0) -> dict[str, str]:
    try:
        import requests

        resp = requests.get(location, timeout=timeout, headers={"User-Agent": "MulteAgent network_scan"})
        resp.raise_for_status()
        return parse_upnp(resp.text)
    except Exception:  # noqa: BLE001 — identification is best-effort; never fail the scan
        return {}


def _dns_query(qname: str, qtype: int) -> bytes:
    header = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"       # id 0, no flags, 1 question
    body = b"".join(bytes([len(p)]) + p.encode("ascii") for p in qname.split(".")) + b"\x00"
    return header + body + qtype.to_bytes(2, "big") + b"\x00\x01"      # class IN


def _read_dns_name(data: bytes, off: int) -> tuple[str, int]:
    """A DNS name at `off`, following compression pointers. Returns (name, offset-after-the-name-field)."""
    labels: list[str] = []
    jumped = False
    after = off
    for _ in range(128):                                              # guard against pointer loops
        length = data[off]
        if length & 0xC0 == 0xC0:                                     # compression pointer
            if not jumped:
                after = off + 2
            off = ((length & 0x3F) << 8) | data[off + 1]
            jumped = True
            continue
        off += 1
        if length == 0:
            break
        labels.append(data[off:off + length].decode("ascii", "replace"))
        off += length
    if not jumped:
        after = off
    return ".".join(labels), after


def parse_ptr_answer(data: bytes) -> str:
    """The first PTR target's leading label (e.g. 'my-printer' from 'my-printer.local'), or ''."""
    try:
        qd = int.from_bytes(data[4:6], "big")
        an = int.from_bytes(data[6:8], "big")
        if an < 1:
            return ""
        off = 12
        for _ in range(qd):
            _, off = _read_dns_name(data, off)
            off += 4                                                  # QTYPE + QCLASS
        _, off = _read_dns_name(data, off)                           # answer name
        rtype = int.from_bytes(data[off:off + 2], "big")
        off += 8                                                      # TYPE + CLASS + TTL
        off += 2                                                      # RDLENGTH
        if rtype != 12:                                              # not PTR
            return ""
        name, _ = _read_dns_name(data, off)
        return name.split(".")[0]
    except (IndexError, ValueError):
        return ""


def mdns_name(ip: str, timeout: float = 0.5) -> str:
    """Unicast reverse mDNS (PTR of x.x.x.x.in-addr.arpa, port 5353) → the device's hostname, or ''."""
    reverse = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(_dns_query(reverse, 12), (ip, 5353))
            data, _ = s.recvfrom(1024)
    except OSError:
        return ""
    return parse_ptr_answer(data)


# ── identification: SNMP v2c (community "public", read-only GET on udp/161) ──
# Managed gear — switches, printers, many cameras/NAS/UPS — answers SNMP GET with the standard
# MIB-2 / ENTITY-MIB / Printer-MIB objects below, which is where a model and (the only place a
# sweep can read one) a serial number come from. We only ever GET, and only the common community.
SNMP_PORT = 161
SNMP_OIDS = {                                       # OID → the field it fills
    "1.3.6.1.2.1.1.5.0": "name",                    # sysName
    "1.3.6.1.2.1.47.1.1.1.1.13.1": "model",         # entPhysicalModelName.1
    "1.3.6.1.2.1.47.1.1.1.1.11.1": "serial",        # entPhysicalSerialNum.1
    "1.3.6.1.2.1.43.5.1.1.17.1": "prt_serial",      # prtGeneralSerialNumber.1 (printers)
}
_SNMP_REQUEST_ID = 0x4D41                           # fixed arbitrary id ("MA"); we never correlate replies


def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _ber(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(body)) + body


def _ber_int(n: int) -> bytes:
    return _ber(0x02, n.to_bytes((n.bit_length() // 8) + 1, "big", signed=True))


def _encode_oid(oid: str) -> bytes:
    arcs = [int(p) for p in oid.split(".")]
    body = bytearray([40 * arcs[0] + arcs[1]])
    for arc in arcs[2:]:
        chunk = bytearray([arc & 0x7F])
        arc >>= 7
        while arc:
            chunk.insert(0, 0x80 | (arc & 0x7F))
            arc >>= 7
        body += chunk
    return _ber(0x06, bytes(body))


def _snmp_get_request(oids: list[str], community: str) -> bytes:
    varbinds = b"".join(_ber(0x30, _encode_oid(o) + _ber(0x05, b"")) for o in oids)   # OID + NULL
    pdu = _ber(0xA0, _ber_int(_SNMP_REQUEST_ID) + _ber_int(0) + _ber_int(0) + _ber(0x30, varbinds))
    return _ber(0x30, _ber_int(1) + _ber(0x04, community.encode()) + pdu)             # version 1 = SNMPv2c


def _ber_tlv(data: bytes, off: int) -> tuple[int, bytes, int]:
    tag = data[off]
    first = data[off + 1]
    off += 2
    if first < 0x80:
        length = first
    else:
        n = first & 0x7F
        length = int.from_bytes(data[off:off + n], "big")
        off += n
    return tag, data[off:off + length], off + length


def _decode_oid(data: bytes) -> str:
    arcs = [data[0] // 40, data[0] % 40]
    val = 0
    for b in data[1:]:
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            arcs.append(val)
            val = 0
    return ".".join(str(a) for a in arcs)


def parse_snmp(data: bytes) -> dict[str, str]:
    """{oid: value} from an SNMPv2c GetResponse — printable strings and integers; error varbinds skipped."""
    try:
        _, msg, _ = _ber_tlv(data, 0)                       # outer SEQUENCE
        _, _, off = _ber_tlv(msg, 0)                        # version
        _, _, off = _ber_tlv(msg, off)                      # community
        _, pdu, _ = _ber_tlv(msg, off)                      # Response PDU (0xA2)
        _, _, off = _ber_tlv(pdu, 0)                        # request-id
        _, _, off = _ber_tlv(pdu, off)                      # error-status
        _, _, off = _ber_tlv(pdu, off)                      # error-index
        _, vbs, _ = _ber_tlv(pdu, off)                      # varbind list
        out: dict[str, str] = {}
        off = 0
        while off < len(vbs):
            _, vb, off = _ber_tlv(vbs, off)                 # one varbind
            _, oid_raw, vo = _ber_tlv(vb, 0)
            vtag, vval, _ = _ber_tlv(vb, vo)
            if vtag == 0x04:                                # OCTET STRING
                text = re.sub(r"\s+", " ", vval.decode("latin-1", "replace")).strip()
                if text:
                    out[_decode_oid(oid_raw)] = text
            elif vtag == 0x02:                              # INTEGER
                out[_decode_oid(oid_raw)] = str(int.from_bytes(vval, "big"))
            # 0x80/0x81/0x82 = noSuchObject / noSuchInstance / endOfMibView → no value, skip
        return out
    except (IndexError, ValueError):
        return {}


def snmp_get(ip: str, oids: list[str], *, community: str = "public", timeout: float = 0.6) -> dict[str, str]:
    """One read-only SNMPv2c GET for `oids` → {oid: value}; {} when the device does not answer."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(_snmp_get_request(oids, community), (ip, SNMP_PORT))
            data, _ = s.recvfrom(4096)
    except OSError:
        return {}
    return parse_snmp(data)


def snmp_identify(ip: str, *, community: str = "public") -> dict[str, str]:
    """Best-effort name / model / serial for managed gear via SNMP (community 'public'). {} if silent."""
    got = snmp_get(ip, list(SNMP_OIDS), community=community)
    roles = {role: got[oid] for oid, role in SNMP_OIDS.items() if got.get(oid)}
    out = {k: roles[k] for k in ("name", "model") if roles.get(k)}
    serial = roles.get("serial") or roles.get("prt_serial")
    if serial:
        out["serial"] = serial
    return out


def identify(devices: list[Device], *, deadline: float) -> None:
    """Fill model / name / serial (and refine type) from SSDP, mDNS and SNMP. Best-effort; mutates in place."""
    by_ip = {d.ip: d for d in devices}
    try:
        locations = ssdp_locations(timeout=min(2.5, max(0.5, deadline - time.monotonic())))
    except Exception:  # noqa: BLE001
        locations = {}
    for ip, location in locations.items():
        d = by_ip.get(ip)
        if d is None or time.monotonic() > deadline:
            continue
        info = ssdp_describe(location)
        d.model = d.model or info.get("model", "")
        d.name = d.name or info.get("name", "")
        d.vendor = d.vendor or info.get("manufacturer", "")
        d.type = d.type or info.get("type", "")
    # mDNS reverse name for devices still unnamed (in parallel, within the time budget)
    unnamed = [d for d in devices if not d.name]
    if unnamed and time.monotonic() < deadline:
        with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(unnamed)))) as pool:
            for d, name in zip(unnamed, pool.map(lambda dev: mdns_name(dev.ip), unnamed)):
                if name:
                    d.name = name
    # SNMP (community "public") for devices still missing a serial — managed gear only; read-only GET
    targets = [d for d in devices if not d.serial]
    if targets and time.monotonic() < deadline:
        with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(targets)))) as pool:
            for d, info in zip(targets, pool.map(lambda dev: snmp_identify(dev.ip), targets)):
                d.name = d.name or info.get("name", "")
                d.model = d.model or info.get("model", "")
                d.serial = d.serial or info.get("serial", "")


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
    gateway = default_gateway()
    for d in devices:
        d.mac = macs.get(d.ip, "")
        d.vendor = manufacturer(d.mac)
    if config.network_scan_identify and not stopped and not timed_out:
        identify(devices, deadline=started + TIME_LIMIT_SECONDS)   # SSDP/mDNS: model + name
    for d in devices:
        d.type = classify(d, gateway)            # after identify(), so name/model words count
    devices.sort(key=lambda d: ipaddress.ip_address(d.ip))
    return ScanResult(str(net), len(hosts), devices, round(time.monotonic() - started, 1), stopped, timed_out)


# ── presentation ─────────────────────────────────────────────────────────
def rows(result: ScanResult) -> list[list[str]]:
    header = ["IP address", "Type", "Name", "Model", "Serial", "Manufacturer", "MAC address",
              "Open ports", "Found by"]
    body = [[d.ip, d.type or "—", d.name or "—", d.model or "—", d.serial or "—", d.vendor or "—", d.mac or "—",
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
         "the manufacturer comes from the MAC address (IEEE list). Model and some names come from the device's "
         "own SSDP/UPnP and mDNS announcements, where it makes them. Model and serial also come from SNMP "
         "(community 'public') on managed gear such as switches and printers; most home/office devices expose no "
         "serial to a credential-free scan, so that column is often empty. Type is a best-effort guess from the name, "
         "model, open ports, manufacturer and whether the device is the gateway — treat it as a hint, not a certainty.")


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
    for col, width in zip("ABCDEFGHI", (16, 14, 20, 24, 18, 26, 20, 44, 10)):
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
    from src.jobs import live
    from src.tools.artifacts import artifact_event, write_bytes

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
