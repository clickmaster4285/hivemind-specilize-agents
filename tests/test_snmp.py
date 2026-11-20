"""SNMPv2c reader (BACKLOG L19 D2): GET and GETBULK walks for switches, read-only by construction — offline:
replies are built here with the same BER helpers and handed back by a fake `send`."""

from __future__ import annotations

import socket

import pytest

from src.servers import snmp
from src.servers.net_scan import _ber, _ber_int, _ber_tlv, _encode_oid
from src.servers.snmp import SnmpError, Value

IF_DESCR = "1.3.6.1.2.1.2.2.1.2"


def _rid_of(packet: bytes) -> int:
    """The request-id of a request we built (so the fake device can answer it)."""
    _, msg, _ = _ber_tlv(packet, 0)
    _, _, off = _ber_tlv(msg, 0)
    _, _, off = _ber_tlv(msg, off)
    _, pdu, _ = _ber_tlv(msg, off)
    _, rid, _ = _ber_tlv(pdu, 0)
    return int.from_bytes(rid, "big", signed=True)


def _reply(rid: int, rows: list[tuple[str, int, bytes]], status: int = 0, index: int = 0) -> bytes:
    varbinds = b"".join(_ber(0x30, _encode_oid(oid) + _ber(tag, raw)) for oid, tag, raw in rows)
    pdu = _ber(snmp.PDU_RESPONSE, _ber_int(rid) + _ber_int(status) + _ber_int(index) + _ber(0x30, varbinds))
    return _ber(0x30, _ber_int(1) + _ber(0x04, b"public") + pdu)


class Device:
    """A fake switch: each request gets the next page of rows (or a hand-made answer)."""

    def __init__(self, pages):
        self.pages, self.sent = list(pages), []

    def __call__(self, packet: bytes) -> bytes:
        self.sent.append(packet)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page(_rid_of(packet)) if callable(page) else _reply(_rid_of(packet), page)


def _text(s: str) -> tuple[int, bytes]:
    return 0x04, s.encode()


# ── values ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tag, raw, natural", [
    (0x02, (-5).to_bytes(1, "big", signed=True), "-5"),                   # INTEGER is signed
    (0x04, b"  JetStream\t24-Port  ", "JetStream 24-Port"),               # text, whitespace collapsed
    (0x06, _encode_oid("1.3.6.1.4.1.11863")[2:], "1.3.6.1.4.1.11863"),     # an OID (TP-Link's enterprise number)
    (0x40, bytes([192, 168, 0, 1]), "192.168.0.1"),                       # IpAddress
    (0x41, (4_000_000_000).to_bytes(4, "big"), "4000000000"),             # Counter32 is unsigned
    (0x42, (1000).to_bytes(2, "big"), "1000"),                            # Gauge32 (ifHighSpeed, Mb/s)
    (0x43, (12_345_678).to_bytes(4, "big"), "12345678"),                  # TimeTicks
    (0x46, (2**40).to_bytes(6, "big"), str(2**40)),                       # Counter64
])
def test_every_value_type_reads_naturally(tag, raw, natural):
    assert str(Value(tag, raw)) == natural


def test_a_six_byte_string_is_a_mac_and_exceptions_are_not_values():
    assert Value(0x04, bytes.fromhex("0011223344ff")).mac == "00:11:22:33:44:ff"
    assert Value(0x04, b"abc").mac == ""
    assert all(Value(tag, b"").is_exception for tag in (0x80, 0x81, 0x82))


# ── GET ────────────────────────────────────────────────────────────────

def test_get_leaves_out_what_the_device_does_not_have():
    name, missing = "1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.47.1.1.1.1.13.1"
    device = Device([[(name, *_text("SW-Floor2")), (missing, 0x80, b"")]])
    got = snmp.get("10.0.0.2", [name, missing], community="public", send=device)
    assert {oid: str(v) for oid, v in got.items()} == {name: "SW-Floor2"}


def _pdu_tag(packet: bytes) -> int:
    _, msg, _ = _ber_tlv(packet, 0)
    _, _, off = _ber_tlv(msg, 0)                      # version
    _, _, off = _ber_tlv(msg, off)                    # community
    return msg[off]


def test_only_read_requests_exist():
    """No SET builder: nothing here can change a device — the only requests are GET (0xA0) and GETBULK (0xA5)."""
    assert not [name for name in dir(snmp) if "set" in name.lower() and callable(getattr(snmp, name))]
    assert _pdu_tag(snmp.build_get(["1.3.6.1.2.1.1.5.0"], "public", 7)) == 0xA0
    assert _pdu_tag(snmp.build_getbulk(IF_DESCR, "public", 7)) == 0xA5


# ── walks ──────────────────────────────────────────────────────────────

def test_a_walk_reads_page_after_page_and_stops_where_the_table_ends():
    device = Device([
        [(f"{IF_DESCR}.{i}", *_text(f"port {i}")) for i in (1, 2)],
        [(f"{IF_DESCR}.3", *_text("port 3")), ("1.3.6.1.2.1.2.2.1.3.1", 0x02, b"\x06")],   # next column: the end
    ])
    rows = snmp.walk("10.0.0.2", IF_DESCR, community="public", send=device)
    assert [(snmp.index_suffix(oid, IF_DESCR), str(v)) for oid, v in rows] == [("1", "port 1"), ("2", "port 2"),
                                                                               ("3", "port 3")]
    assert len(device.sent) == 2


def test_a_walk_stops_at_the_end_of_the_mib():
    device = Device([[(f"{IF_DESCR}.1", *_text("port 1")), (f"{IF_DESCR}.2", 0x82, b"")]])
    assert len(snmp.walk("10.0.0.2", IF_DESCR, community="public", send=device)) == 1


def test_a_walk_stops_when_the_device_goes_backwards_or_hits_the_row_limit():
    backwards = Device([[(f"{IF_DESCR}.5", *_text("a")), (f"{IF_DESCR}.4", *_text("b"))]])
    assert len(snmp.walk("10.0.0.2", IF_DESCR, community="public", send=backwards)) == 1
    many = Device([[(f"{IF_DESCR}.{i}", *_text("p")) for i in range(1, 11)]])
    assert len(snmp.walk("10.0.0.2", IF_DESCR, community="public", max_rows=4, send=many)) == 4


# ── errors ─────────────────────────────────────────────────────────────

def test_a_stale_reply_is_skipped():
    old = lambda rid: _reply(rid + 1, [("1.3.6.1.2.1.1.5.0", *_text("late"))])     # noqa: E731 — someone else's id
    device = Device([old, [("1.3.6.1.2.1.1.5.0", *_text("SW-Floor2"))]])
    got = snmp.get("10.0.0.2", ["1.3.6.1.2.1.1.5.0"], community="public", send=device)
    assert str(got["1.3.6.1.2.1.1.5.0"]) == "SW-Floor2" and len(device.sent) == 2


def test_an_error_status_is_named():
    device = Device([lambda rid: _reply(rid, [], status=16, index=1)])
    with pytest.raises(SnmpError, match="authorizationError"):
        snmp.get("10.0.0.2", ["1.3.6.1.2.1.1.5.0"], community="public", send=device)


def test_no_answer_twice_points_at_the_community_string():
    device = Device([socket.timeout(), socket.timeout()])
    with pytest.raises(SnmpError, match="no answer from 10.0.0.2 on SNMP — check the community string"):
        snmp.get("10.0.0.2", ["1.3.6.1.2.1.1.5.0"], community="wrong", send=device)
    assert len(device.sent) == 2                                                    # one retry, then the error


# ── helpers ────────────────────────────────────────────────────────────

def test_table_indexes_macs_and_uptime():
    assert snmp.index_suffix(f"{IF_DESCR}.17", IF_DESCR) == "17" and snmp.index_suffix("1.3.6.1.9", IF_DESCR) == ""
    assert snmp.mac_from_index("0.17.34.51.68.85") == "00:11:22:33:44:55"
    assert snmp.mac_from_index("0.17.34.51.68") == "" and snmp.mac_from_index("0.17.34.51.68.300") == ""
    assert snmp.vlan_mac_from_index("10.0.17.34.51.68.85") == (10, "00:11:22:33:44:55")
    assert snmp.vlan_mac_from_index("x.0.17.34.51.68.85") is None
    assert snmp.uptime_text(((12 * 24 + 3) * 60 + 4) * 60 * 100) == "12d 3h 4m"
    assert snmp.uptime_text(90 * 60 * 100) == "1h 30m" and snmp.uptime_text(59 * 100) == "0m"
