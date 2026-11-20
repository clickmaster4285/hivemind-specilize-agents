"""SNMPv2c reader for switches and routers — GET and GETBULK walks, read-only by construction.

TP-Link / D-Link / Netgear managed switches answer SNMPv2c with a community string; this is how
netdev reads their ports, MAC tables and uptime (BACKLOG L19 D2). Only GET and GETBULK PDUs can
be built here — there is no SET builder, so nothing in this module can change a device.
The BER helpers come from net_scan; this module adds a full value decoder (counters, MACs,
IP addresses, time ticks) and request-id matching.
"""

from __future__ import annotations

import re
import secrets
import socket
from collections.abc import Callable
from dataclasses import dataclass

from src.servers.net_scan import (
    SNMP_PORT,
    _ber,
    _ber_int,
    _ber_tlv,
    _decode_oid,
    _encode_oid,
)

SNMP_VERSION_2C = 1                 # the version field says 1 for SNMPv2c (0 is v1)
MAX_DATAGRAM = 65535                # largest UDP payload; a GETBULK page can be several KB
DEFAULT_TIMEOUT = 2.0               # switches on a busy CPU can take a second to answer
DEFAULT_MAX_ROWS = 5000             # a 48-port switch MAC table fits; stops a runaway walk
BULK_MAX_REPETITIONS = 25           # rows per GETBULK page — small enough to stay under typical MTU-ish replies
BULK_NON_REPEATERS = 0              # every requested OID is walked, none fetched once
TIMEOUT_ATTEMPTS = 2                # the first try plus one retry; UDP can drop a packet
STALE_REPLY_LIMIT = 3               # replies with someone else's request-id we tolerate before giving up
REQUEST_ID_LIMIT = 2**31 - 1        # request-id is a signed 32-bit INTEGER; keep it positive

# PDU and value tags (RFC 3416 / RFC 2578)
PDU_GET = 0xA0
PDU_RESPONSE = 0xA2
PDU_GETBULK = 0xA5
TAG_SEQUENCE = 0x30
TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_IPADDRESS = 0x40
TAG_COUNTER32 = 0x41
TAG_GAUGE32 = 0x42
TAG_TIMETICKS = 0x43
TAG_OPAQUE = 0x44
TAG_COUNTER64 = 0x46
TAG_NO_SUCH_OBJECT = 0x80
TAG_NO_SUCH_INSTANCE = 0x81
TAG_END_OF_MIB_VIEW = 0x82
EXCEPTION_TAGS = (TAG_NO_SUCH_OBJECT, TAG_NO_SUCH_INSTANCE, TAG_END_OF_MIB_VIEW)
UNSIGNED_TAGS = (TAG_COUNTER32, TAG_GAUGE32, TAG_TIMETICKS, TAG_COUNTER64)

MAC_LEN = 6                         # an Ethernet address is 6 bytes / 6 index arcs
TICKS_PER_SECOND = 100              # TimeTicks count hundredths of a second

# error-status values a response can carry (RFC 3416 §3)
ERROR_STATUS_NAMES = {
    1: "tooBig", 2: "noSuchName", 3: "badValue", 4: "readOnly", 5: "genErr", 6: "noAccess",
    7: "wrongType", 8: "wrongLength", 9: "wrongEncoding", 10: "wrongValue", 11: "noCreation",
    12: "inconsistentValue", 13: "resourceUnavailable", 14: "commitFailed", 15: "undoFailed",
    16: "authorizationError", 17: "notWritable", 18: "inconsistentName",
}

Send = Callable[[bytes], bytes]


class SnmpError(Exception):
    """The device did not answer, answered with an error, or sent something we cannot read."""


@dataclass(frozen=True)
class Value:
    """One SNMP value: its BER tag and raw bytes; str() gives the natural text."""

    tag: int
    raw: bytes

    @property
    def is_exception(self) -> bool:
        """noSuchObject / noSuchInstance / endOfMibView — a placeholder, not a value."""
        return self.tag in EXCEPTION_TAGS

    @property
    def number(self) -> int | None:
        """The integer for INTEGER (signed) and counters / gauges / ticks (unsigned); else None."""
        if self.tag == TAG_INTEGER:
            return int.from_bytes(self.raw, "big", signed=True) if self.raw else 0
        if self.tag in UNSIGNED_TAGS:
            return int.from_bytes(self.raw, "big")
        return None

    @property
    def text(self) -> str:
        """Printable text of an OCTET STRING, whitespace collapsed; control bytes dropped."""
        s = self.raw.decode("utf-8", "replace") if self.raw else ""
        s = "".join(ch for ch in s if ch.isprintable() or ch.isspace())
        return re.sub(r"\s+", " ", s).strip()

    @property
    def mac(self) -> str:
        """"aa:bb:cc:dd:ee:ff" when the value is 6 bytes (a MAC address); else ""."""
        return ":".join(f"{b:02x}" for b in self.raw) if len(self.raw) == MAC_LEN else ""

    def __str__(self) -> str:
        if self.tag == TAG_OCTET_STRING:
            return self.text
        if self.tag == TAG_OID:
            return _decode_oid(self.raw) if self.raw else ""
        if self.tag == TAG_IPADDRESS:
            return ".".join(str(b) for b in self.raw)
        num = self.number
        if num is not None:
            return str(num)
        return ""


# ── packets ──

def _new_request_id() -> int:
    return secrets.randbelow(REQUEST_ID_LIMIT) + 1


def _message(community: str, pdu_tag: int, request_id: int, field2: int, field3: int,
             oids: list[str]) -> bytes:
    # field2/field3 are error-status/error-index for GET, non-repeaters/max-repetitions for GETBULK
    varbinds = b"".join(_ber(TAG_SEQUENCE, _encode_oid(o) + _ber(TAG_NULL, b"")) for o in oids)
    pdu = _ber(pdu_tag, _ber_int(request_id) + _ber_int(field2) + _ber_int(field3)
               + _ber(TAG_SEQUENCE, varbinds))
    return _ber(TAG_SEQUENCE, _ber_int(SNMP_VERSION_2C) + _ber(TAG_OCTET_STRING, community.encode()) + pdu)


def build_get(oids: list[str], community: str, request_id: int) -> bytes:
    """A GET request for `oids`."""
    return _message(community, PDU_GET, request_id, 0, 0, oids)


def build_getbulk(oid: str, community: str, request_id: int,
                  max_repetitions: int = BULK_MAX_REPETITIONS) -> bytes:
    """A GETBULK request for the rows after `oid`."""
    return _message(community, PDU_GETBULK, request_id, BULK_NON_REPEATERS, max_repetitions, [oid])


def _int(raw: bytes) -> int:
    return int.from_bytes(raw, "big", signed=True) if raw else 0


def parse_response(data: bytes) -> tuple[int, int, int, list[tuple[str, Value]]]:
    """(request-id, error-status, error-index, [(oid, Value)]) from a Response PDU."""
    try:
        _, msg, _ = _ber_tlv(data, 0)                       # outer SEQUENCE
        _, _, off = _ber_tlv(msg, 0)                        # version
        _, _, off = _ber_tlv(msg, off)                      # community
        tag, pdu, _ = _ber_tlv(msg, off)
        if tag != PDU_RESPONSE:
            raise SnmpError(f"unexpected SNMP PDU 0x{tag:02X}")
        _, rid, off = _ber_tlv(pdu, 0)
        _, status, off = _ber_tlv(pdu, off)
        _, index, off = _ber_tlv(pdu, off)
        _, vbs, _ = _ber_tlv(pdu, off)
        rows: list[tuple[str, Value]] = []
        off = 0
        while off < len(vbs):
            _, vb, off = _ber_tlv(vbs, off)
            _, oid_raw, vo = _ber_tlv(vb, 0)
            vtag, vval, _ = _ber_tlv(vb, vo)
            rows.append((_decode_oid(oid_raw), Value(vtag, vval)))
        return _int(rid), _int(status), _int(index), rows
    except (IndexError, ValueError) as exc:
        raise SnmpError("garbled SNMP reply") from exc


# ── transport ──

def _udp_sender(host: str, port: int, timeout: float) -> Send:
    """One datagram out, one back, on a fresh socket (so a late reply to an old request never lands here)."""
    def send(packet: bytes) -> bytes:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(packet, (host, port))
            data, _ = s.recvfrom(MAX_DATAGRAM)
            return data
    return send


def _no_answer(host: str) -> SnmpError:
    return SnmpError(f"no answer from {host} on SNMP — check the community string and that SNMP is on")


def _exchange(host: str, build: Callable[[int], bytes], send: Send) -> list[tuple[str, Value]]:
    """Send one request with a fresh request-id; retry once on timeout; skip replies for other ids."""
    timeouts = stale = 0
    while True:
        rid = _new_request_id()
        try:
            reply = send(build(rid))
        except TimeoutError:                                 # socket.timeout is TimeoutError
            timeouts += 1
            if timeouts >= TIMEOUT_ATTEMPTS:
                raise _no_answer(host) from None
            continue
        except OSError as exc:                               # port unreachable, no route…
            raise SnmpError(f"SNMP to {host} failed: {exc}") from exc
        got_rid, status, index, rows = parse_response(reply)
        if got_rid != rid:
            stale += 1
            if stale >= STALE_REPLY_LIMIT:
                raise SnmpError(f"{host} keeps answering an old SNMP request")
            continue
        if status:
            name = ERROR_STATUS_NAMES.get(status, f"error {status}")
            raise SnmpError(f"{host} answered SNMP with {name} (varbind {index})")
        return rows


# ── public API ──

def get(host: str, oids: list[str], *, community: str, port: int = SNMP_PORT,
        timeout: float = DEFAULT_TIMEOUT, send: Send | None = None) -> dict[str, Value]:
    """One GET for `oids` → {oid: Value}; OIDs the device does not have are left out."""
    send = send or _udp_sender(host, port, timeout)
    rows = _exchange(host, lambda rid: build_get(oids, community, rid), send)
    return {oid: v for oid, v in rows if not v.is_exception}


def _arcs(oid: str) -> tuple[int, ...]:
    return tuple(int(p) for p in oid.split("."))


def in_subtree(oid: str, root: str) -> bool:
    """True when `oid` lies under `root` (not `root` itself)."""
    return oid.startswith(root + ".")


def walk(host: str, root_oid: str, *, community: str, port: int = SNMP_PORT,
         timeout: float = DEFAULT_TIMEOUT, max_rows: int = DEFAULT_MAX_ROWS,
         send: Send | None = None) -> list[tuple[str, Value]]:
    """Every (oid, Value) under `root_oid`, read with GETBULK pages until the subtree ends."""
    send = send or _udp_sender(host, port, timeout)
    out: list[tuple[str, Value]] = []
    last = root_oid
    while len(out) < max_rows:
        rows = _exchange(host, lambda rid, start=last: build_getbulk(start, community, rid), send)
        if not rows:
            return out
        for oid, value in rows:
            if value.tag == TAG_END_OF_MIB_VIEW or not in_subtree(oid, root_oid):
                return out
            if _arcs(oid) <= _arcs(last):                    # device went backwards: no progress
                return out
            last = oid
            if not value.is_exception:
                out.append((oid, value))
                if len(out) >= max_rows:
                    return out
    return out


# ── helpers for switch reports ──

def index_suffix(oid: str, root: str) -> str:
    """The table index: the part of `oid` after `root` ("" when outside it)."""
    return oid[len(root) + 1:] if in_subtree(oid, root) else ""


def uptime_text(ticks: int) -> str:
    """TimeTicks → "12d 3h 4m" (days and hours only when non-zero)."""
    minutes_total = ticks // TICKS_PER_SECOND // 60
    days, rest = divmod(minutes_total, 24 * 60)
    hours, minutes = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def mac_from_index(index: str) -> str:
    """BRIDGE-MIB index "0.17.34.51.68.85" → "00:11:22:33:44:55"; "" when it is not 6 byte arcs."""
    try:
        arcs = [int(p) for p in index.split(".")]
    except ValueError:
        return ""
    if len(arcs) != MAC_LEN or any(not 0 <= a <= 0xFF for a in arcs):
        return ""
    return ":".join(f"{a:02x}" for a in arcs)


def vlan_mac_from_index(index: str) -> tuple[int, str] | None:
    """Q-BRIDGE index "10.0.17.34.51.68.85" → (10, "00:11:22:33:44:55"); None when malformed."""
    vlan, _, rest = index.partition(".")
    mac = mac_from_index(rest)
    if not vlan.isdigit() or not mac:
        return None
    return int(vlan), mac
