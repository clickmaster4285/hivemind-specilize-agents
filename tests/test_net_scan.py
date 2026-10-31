"""network_scan: safe by construction — private networks only, limits, Stop. No real network used."""

from __future__ import annotations

import dataclasses
import time

import pytest

from src.servers import net_scan
from src.core.config import Config
from src.servers.net_scan import Device, ScanError, ScanResult, subnet_from_text, validate_subnet

CFG = Config.from_env()


@pytest.mark.parametrize("subnet, message", [
    ("8.8.8.0/24", "not a private network"),
    ("1.1.1.1", "not a private network"),
    ("10.0.0.0/16", "limit is 1024"),
    ("192.168.0.0/16", "limit is 1024"),
    ("hello", "not a network address"),
    ("fd00::/120", "only IPv4"),
])
def test_refused(subnet, message):
    with pytest.raises(ScanError, match=message):
        validate_subnet(subnet, 1024)


@pytest.mark.parametrize("subnet", ["192.168.1.0/24", "10.20.30.0/22", "172.16.5.0/24", "192.168.88.36"])
def test_allowed(subnet):
    assert validate_subnet(subnet, 1024).num_addresses <= 1024


def test_subnet_in_the_message():
    assert subnet_from_text("ping sweep 10.0.0.0/24 and list who answers") == "10.0.0.0/24"
    assert subnet_from_text("check 192.168.1.20") == "192.168.1.20/24"
    assert subnet_from_text("scan my network") is None


@pytest.fixture
def fake_network(monkeypatch):
    """Hosts .1, .2 answer ping, .9 only on port 445; everything else is silent."""
    monkeypatch.setattr(net_scan, "_ping", lambda ip: ip.endswith((".1", ".2")))
    monkeypatch.setattr(net_scan, "_port_open", lambda ip, port: ip.endswith(".9") and port == 445)
    monkeypatch.setattr(net_scan.socket, "gethostbyaddr", lambda ip: ("router.lan",) if ip.endswith(".1") else (_ for _ in ()).throw(OSError()))
    # .12 is a firewalled PC: no ping, no open port — only in the ARP table
    monkeypatch.setattr(net_scan, "_arp_table", lambda: {"192.168.50.1": "aa:bb:cc:00:00:01",
                                                         "192.168.50.12": "f8:bc:12:70:de:8f",
                                                         "192.168.50.15": "ff:ff:ff:ff:ff:ff",
                                                         "10.9.9.9": "aa:bb:cc:00:00:09"})
    monkeypatch.setattr(net_scan, "_own_macs", lambda: {})
    monkeypatch.setattr(net_scan, "_netbios_name", lambda ip: "PC26" if ip.endswith(".12") else "")
    monkeypatch.setattr(net_scan, "_oui", {"F8BC12": "Dell Inc.", "AABBCC": "Test Vendor"})
    monkeypatch.setattr(net_scan, "default_gateway", lambda: "192.168.50.1")   # hermetic: no real route lookup
    monkeypatch.setattr(net_scan, "identify", lambda devices, *, deadline: None)  # no real SSDP/mDNS in tests


def test_scan_finds_devices_by_ping_and_tcp(fake_network):
    result = net_scan.scan("192.168.50.0/28", CFG)
    assert [(d.ip, d.found_by) for d in result.devices] == [
        ("192.168.50.1", "ping"), ("192.168.50.2", "ping"), ("192.168.50.9", "tcp"), ("192.168.50.12", "arp")]
    assert result.devices[0].name == "router.lan" and result.devices[0].mac == "aa:bb:cc:00:00:01"
    assert result.devices[2].ports == [445]
    assert result.scanned == 14 and not result.stopped
    pc = result.devices[3]                                    # firewalled PC found through ARP, named by NetBIOS
    assert (pc.name, pc.vendor) == ("PC26", "Dell Inc.")
    # device type inferred: .1 is the gateway (router); .9 answers SMB and .12 is a named Dell → pc; .2 unknown
    assert [d.type for d in result.devices] == ["router", "", "pc", "pc"]


def test_switched_off_and_stop(fake_network):
    with pytest.raises(ScanError, match="switched off"):
        net_scan.scan("192.168.50.0/28", dataclasses.replace(CFG, network_scan_enabled=False))
    result = net_scan.scan("192.168.50.0/24", CFG, should_continue=lambda: False)
    assert result.stopped and "Stopped early" in net_scan.summary(result)


def test_table_and_excel():
    result = ScanResult("192.168.1.0/24", 254, [Device("192.168.1.10", "nas-01", "aa:bb:cc:dd:ee:ff", [445, 443],
                                                      "ping", "QNAP Systems, Inc.", type="NAS", model="DS224+",
                                                      serial="Q2A3B4C5")], 3.2)
    table = net_scan.markdown_table(result)
    assert table.splitlines()[0] == ("| IP address | Type | Name | Model | Serial | Manufacturer | MAC address | "
                                     "Open ports | Found by |")
    assert ("| 192.168.1.10 | NAS | nas-01 | DS224+ | Q2A3B4C5 | QNAP Systems, Inc. | aa:bb:cc:dd:ee:ff | "
            "445 SMB, 443 HTTPS | ping |") in table
    from io import BytesIO

    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(net_scan.xlsx_bytes(result)))
    assert wb["Devices"]["A2"].value == "192.168.1.10" and wb["Scan"]["B3"].value == 1


def test_chat_job_gives_table_and_file(fake_network, monkeypatch, tmp_path):
    monkeypatch.setattr(net_scan, "local_subnet", lambda: "192.168.50.0/28")
    saved = {}

    def fake_write(config, job_id, filename, data, kind="document"):
        saved["name"] = filename
        return {"filename": filename, "mime": "x", "relpath": f"{job_id}/files/{filename}", "bytes": len(data), "kind": kind}

    monkeypatch.setattr("src.tools.artifacts.write_bytes", fake_write)
    events = []
    output, errors, _ = net_scan.run_network_scan("check all the IPs and give me a table", job_id="j1",
                                                  config=CFG, on_progress=events.append)
    assert errors == [] and "4 devices answered" in output and "| 192.168.50.9 |" in output
    assert saved["name"] == "network-scan-192.168.50.0_28.xlsx"
    types = [e["type"] for e in events]
    assert "artifact_ready" in types and types[-1] == "session_done"


def test_public_target_in_the_message_is_refused_in_chat(monkeypatch):
    output, errors, _ = net_scan.run_network_scan("scan 8.8.8.0/24 for me", job_id="j2", config=CFG)
    assert "not run" in output and "not a private network" in errors[0]


def test_agentic_tool_uses_the_same_guards():
    from src.agentic.tools import ToolError, run_tool

    with pytest.raises(ToolError, match="not a private network"):
        run_tool("network_scan", {"subnet": "8.8.8.0/24"}, job_id="j3", config=CFG)


def test_time_limit(fake_network, monkeypatch):
    monkeypatch.setattr(net_scan, "TIME_LIMIT_SECONDS", 0)
    monkeypatch.setattr(net_scan, "_ping", lambda ip: (time.sleep(0.01), True)[1])
    result = net_scan.scan("192.168.50.0/26", CFG)
    assert result.timed_out and "Time limit" in net_scan.summary(result)


def test_netbios_answer_is_parsed():
    # header(12) + name(34) + type/class/ttl/len(10) + count + entries of 18 bytes
    entry = lambda name, suffix, flags: name.ljust(15).encode() + bytes([suffix]) + flags.to_bytes(2, "big")
    data = b"\0" * 56 + bytes([3]) + entry("WORKGROUP", 0x00, 0x8400) + entry("AMJADKHAN-PC", 0x20, 0x0400)         + entry("AMJADKHAN-PC", 0x00, 0x0400)
    assert net_scan.parse_nbstat(data) == "AMJADKHAN-PC"           # unique name, not the group
    assert net_scan.parse_nbstat(b"too short") == ""


def test_classify_device_type():
    c = net_scan.classify
    assert c(Device("10.0.0.1"), gateway_ip="10.0.0.1") == "router"           # the gateway
    assert c(Device("10.0.0.5", ports=[9100], vendor="Hewlett Packard")) == "printer"
    assert c(Device("10.0.0.6", ports=[554], vendor="Hikvision Digital Technology")) == "ip camera"
    assert c(Device("10.0.0.7", vendor="Ubiquiti Inc.")) == "router"
    assert c(Device("10.0.0.8", ports=[445, 139], vendor="Dell Inc.", name="PC1")) == "pc"
    assert c(Device("10.0.0.9", vendor="Apple, Inc.")) == "phone"
    assert c(Device("10.0.0.10", ports=[445], vendor="Apple, Inc.")) == "pc"  # a Mac that shares files
    assert c(Device("10.0.0.11", vendor="Synology Incorporated")) == "nas"
    assert c(Device("10.0.0.12", ports=[22])) == "server"
    assert c(Device("10.0.0.13", ports=[80, 443])) == ""                      # web UI only: left to SSDP/SNMP
    # phase-2 types: name/model words and the new ports
    assert c(Device("10.0.0.20", name="NVR-Lobby", ports=[554], vendor="Hikvision Digital Technology")) == "nvr"
    assert c(Device("10.0.0.26", name="NVR01")) == "nvr"                       # name wins even without a boundary
    assert c(Device("10.0.0.21", name="SUN2000-12K", model="SUN2000")) == "inverter"
    assert c(Device("10.0.0.22", ports=[502])) == "inverter"                  # Modbus TCP
    assert c(Device("10.0.0.23", name="Catalyst-SW1", vendor="Cisco Systems")) == "switch"
    assert c(Device("10.0.0.24", name="Living Room TV", model="BRAVIA KD-55")) == "media player"
    assert c(Device("10.0.0.25", name="shelly-plug-s")) == "iot device"
    assert c(Device("10.0.0.27", ports=[8000], vendor="Hikvision Digital Technology")) == "ip camera"


def test_ssdp_header_and_upnp_xml_parsing():
    resp = "HTTP/1.1 200 OK\r\nST: upnp:rootdevice\r\nLOCATION: http://192.168.1.1:5000/desc.xml\r\nUSN: uuid:x\r\n\r\n"
    assert net_scan._http_header(resp, "location") == "http://192.168.1.1:5000/desc.xml"
    xml = ("<root><device><deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>"
           "<friendlyName>Home Router</friendlyName><manufacturer>MikroTik</manufacturer>"
           "<modelName>RB750Gr3</modelName><modelNumber>hEX</modelNumber></device></root>")
    assert net_scan.parse_upnp(xml) == {"name": "Home Router", "model": "RB750Gr3 hEX",
                                        "manufacturer": "MikroTik", "type": "router"}


def test_mdns_ptr_answer_is_parsed():
    """A reverse-PTR response whose answer uses a compression pointer back to the question name."""
    def dns_name(n: str) -> bytes:
        return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\x00"

    header = b"\x00\x00\x84\x00" + (1).to_bytes(2, "big") + (1).to_bytes(2, "big") + b"\x00\x00\x00\x00"
    question = dns_name("5.1.168.192.in-addr.arpa") + b"\x00\x0c\x00\x01"
    rdata = dns_name("my-printer.local")
    answer = b"\xc0\x0c" + b"\x00\x0c" + b"\x00\x01" + b"\x00\x00\x00\x3c" + len(rdata).to_bytes(2, "big") + rdata
    assert net_scan.parse_ptr_answer(header + question + answer) == "my-printer"
    assert net_scan.parse_ptr_answer(b"\x00" * 12) == ""        # no answers


def test_snmp_request_round_trips_oid_encoding():
    # the GET we build must carry the four OIDs we ask for, each decodable again
    req = net_scan._snmp_get_request(list(net_scan.SNMP_OIDS), "public")
    assert b"public" in req
    for oid in net_scan.SNMP_OIDS:
        assert net_scan._encode_oid(oid)[2:] in req          # the OID body (after tag+len) is present


def test_parse_snmp_reads_strings_and_skips_errors():
    _ber, _ber_int, _oid = net_scan._ber, net_scan._ber_int, net_scan._encode_oid
    vb = lambda oid, tag, val: _ber(0x30, _oid(oid) + _ber(tag, val))
    varbinds = (vb("1.3.6.1.2.1.1.5.0", 0x04, b"switch-core")
                + vb("1.3.6.1.2.1.47.1.1.1.1.13.1", 0x04, b"WS-C2960X")
                + vb("1.3.6.1.2.1.47.1.1.1.1.11.1", 0x04, b"FOC1234ABCD")
                + vb("1.3.6.1.2.1.43.5.1.1.17.1", 0x81, b""))      # noSuchInstance → must be skipped
    pdu = _ber(0xA2, _ber_int(net_scan._SNMP_REQUEST_ID) + _ber_int(0) + _ber_int(0) + _ber(0x30, varbinds))
    packet = _ber(0x30, _ber_int(1) + _ber(0x04, b"public") + pdu)
    got = net_scan.parse_snmp(packet)
    assert got["1.3.6.1.2.1.1.5.0"] == "switch-core"
    assert got["1.3.6.1.2.1.47.1.1.1.1.11.1"] == "FOC1234ABCD"
    assert "1.3.6.1.2.1.43.5.1.1.17.1" not in got              # the error varbind carried no value
    assert net_scan.parse_snmp(b"\x00\x01") == {}              # junk → {}


def test_snmp_identify_maps_oids_to_fields(monkeypatch):
    monkeypatch.setattr(net_scan, "snmp_get", lambda ip, oids, **kw: {
        "1.3.6.1.2.1.1.5.0": "switch-core",
        "1.3.6.1.2.1.47.1.1.1.1.13.1": "WS-C2960X",
        "1.3.6.1.2.1.47.1.1.1.1.11.1": "FOC1234ABCD"})
    assert net_scan.snmp_identify("10.0.0.1") == {"name": "switch-core", "model": "WS-C2960X",
                                                  "serial": "FOC1234ABCD"}
    # printer: only prtGeneralSerialNumber present → used as the serial
    monkeypatch.setattr(net_scan, "snmp_get", lambda ip, oids, **kw: {"1.3.6.1.2.1.43.5.1.1.17.1": "CNABC123"})
    assert net_scan.snmp_identify("10.0.0.2") == {"serial": "CNABC123"}
    monkeypatch.setattr(net_scan, "snmp_get", lambda ip, oids, **kw: {})
    assert net_scan.snmp_identify("10.0.0.3") == {}            # silent device → {}


def test_random_macs_are_private(monkeypatch):
    monkeypatch.setattr(net_scan, "_oui", {"F8BC12": "Dell Inc."})
    assert net_scan.manufacturer("f8:bc:12:70:de:8f") == "Dell Inc."
    assert net_scan.manufacturer("da:a1:19:00:00:01") == "Private (random MAC)"   # locally administered bit
    assert net_scan.manufacturer("") == ""
