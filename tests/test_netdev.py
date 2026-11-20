"""Router / switch reader (BACKLOG L19 D2): the platform from the SSH banner, only its read-only commands,
RouterOS output parsed — offline, with a fake SSH session and replies shaped like RouterOS v7's."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from src.servers import netdev, server_vault

IDENTITY = "  name: Office-Router\r\n"
RESOURCE = """                   uptime: 3w2d4h12m
                  version: 7.15.3 (stable)
               build-time: 2024-07-24 10:39:04
              free-memory: 211.5MiB
             total-memory: 256.0MiB
                 cpu-load: 3%
               board-name: hAP ac^2
"""
INTERFACES = """ 0  R name=ether1 default-name=ether1 type=ether actual-mtu=1500 mac-address=48:8F:5A:00:00:01
 1  X name=ether2 default-name=ether2 type=ether mac-address=48:8F:5A:00:00:02
 2    name=ether3 default-name=ether3 type=ether mac-address=48:8F:5A:00:00:03
 3  R name=bridge type=bridge mac-address=48:8F:5A:00:00:04
"""
LEASES = """ 0 D address=192.168.88.254 mac-address=AA:BB:CC:00:00:01 host-name="Ali Laptop" server=defconf status=bound
 1   address=192.168.88.20 mac-address=AA:BB:CC:00:00:02 server=defconf status=waiting
"""
ARP = " 0 DC address=192.168.88.254 mac-address=AA:BB:CC:00:00:01 interface=bridge\r\n"
LOG = "10:01:02 system,info,account user admin logged in from 192.168.88.10 via ssh\n10:05:00 dhcp,info lease ```\n"
REPLIES = dict(zip(netdev.MIKROTIK.commands.values(), (IDENTITY, RESOURCE, INTERFACES, LEASES, ARP, LOG)))
LOGIN = server_vault.ServerLogin(id="t", label="router", host="192.168.88.1", port=22, username="multeagent",
                                 password="pw", kind="network")


class _Stream:
    def __init__(self, text: str):
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()


class _Session:
    """A fake SSH session: its banner, and the commands it was asked to run."""

    def __init__(self, banner: str):
        self.banner, self.sent, self.closed = banner, [], False

    def get_transport(self):
        return type("T", (), {"remote_version": self.banner})()

    def exec_command(self, command: str, timeout: int):
        self.sent.append(command)
        return None, _Stream(REPLIES.get(command, "")), None

    def close(self):
        self.closed = True


def _read(banner: str = "SSH-2.0-ROSSSH"):
    session = _Session(banner)
    report, fingerprint = netdev.read_device(LOGIN, open_session=lambda login: (session, "ssh-ed25519 SHA256:abc"))
    return report, fingerprint, session


def test_a_mikrotik_router_is_read_with_its_read_only_commands_only():
    report, fingerprint, session = _read()
    assert session.sent == list(netdev.MIKROTIK.commands.values()) and session.closed
    assert fingerprint == "ssh-ed25519 SHA256:abc"
    assert (report.name, report.platform) == ("Office-Router", "MikroTik RouterOS")
    assert report.info == {"model": "hAP ac^2", "version": "7.15.3 (stable)", "uptime": "3w2d4h12m",
                           "cpu load": "3%", "memory free": "211.5MiB of 256.0MiB"}


def test_interface_flags_become_up_down_disabled():
    report, _, _ = _read()
    assert [(i["name"], i["status"]) for i in report.interfaces] == [
        ("ether1", "up"), ("ether2", "disabled"), ("ether3", "down"), ("bridge", "up")]


def test_connected_devices_come_from_the_dhcp_leases_and_arp():
    report, _, _ = _read()
    assert report.leases[0] == {"ip": "192.168.88.254", "mac": "AA:BB:CC:00:00:01", "host": "Ali Laptop",
                                "status": "bound"}
    assert report.leases[1]["host"] == "" and report.arp[0]["interface"] == "bridge"


def test_an_unknown_device_is_named_and_nothing_is_run_on_it():
    with pytest.raises(netdev.DeviceError, match="SSH-2.0-OpenSSH_9.6"):
        _read("SSH-2.0-OpenSSH_9.6")


def test_the_device_log_cannot_break_out_of_its_block_in_the_chat():
    report, _, _ = _read()
    text = netdev.markdown(report)
    assert text.count("```") == 2                                    # only our own fence
    assert "2 of 4 up" in text and "| ether2 | ether | disabled |" in text


def test_ssh_tools_never_get_a_router_login():
    server_vault.clear(77)
    server_vault.add(77, label="router", host="192.168.88.1", port=22, username="u", password="p", kind="network")
    try:
        assert server_vault.find(77, None) is None
        assert server_vault.find(77, None, kind="network").host == "192.168.88.1"
    finally:
        server_vault.clear(77)


def test_the_chat_job_answers_with_tables_and_an_xlsx(monkeypatch, tmp_path):
    from src.core.config import Config

    server_vault.clear(78)
    server_vault.add(78, label="router", host="192.168.88.1", port=22, username="u", password="p", kind="network")
    session = _Session("SSH-2.0-ROSSSH")
    original = netdev.read_device
    monkeypatch.setattr(netdev, "read_device", lambda login: original(login, open_session=lambda _l: (session, "fp")))
    events: list[dict] = []
    try:
        output, errors, _ = netdev.run_network_device("check the router", job_id="j", on_progress=events.append,
                                                      config=replace(Config.from_env(), artifacts_dir=tmp_path),
                                                      user_id=78)
    finally:
        server_vault.clear(78)
    assert not errors and "**DHCP leases** — 2 devices" in output
    assert any(e["type"] == "artifact_ready" and e["filename"] == "device-192.168.88.1.xlsx" for e in events)


# ── Cisco IOS (BACKLOG L19 D2) ─────────────────────────────────────────

CISCO_VERSION = """Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E2, RELEASE SOFTWARE (fc3)
SW-CORE uptime is 12 weeks, 3 days, 4 hours, 5 minutes
cisco WS-C2960X-48TS-L (APM86XXX) processor (revision B0) with 524288K bytes of memory.
Processor board ID FOC1234X5YZ
Model number                    : WS-C2960X-48TS-L
System serial number            : FOC1234X5YZ
"""
CISCO_PORTS = """
Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1   uplink to router   connected    trunk      a-full a-1000 10/100/1000BaseTX
Gi1/0/2                      notconnect   1            auto   auto 10/100/1000BaseTX
Gi1/0/3   printer            disabled     20           auto   auto 10/100/1000BaseTX
"""
CISCO_MACS = """          Mac Address Table
-------------------------------------------

Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
 All    0100.0ccc.cccc    STATIC      CPU
   1    0011.2233.4455    DYNAMIC     Gi1/0/1
  20    aabb.cc00.0102    DYNAMIC     Gi1/0/3
Total Mac Addresses for this criterion: 3
"""
CISCO_ARP = """Protocol  Address          Age (min)  Hardware Addr   Type   Interface
Internet  192.168.1.1             -   0011.2233.4455  ARPA   Vlan1
Internet  192.168.1.20           12   aabb.cc00.0102  ARPA   Vlan1
"""
CISCO_LOG = """Syslog logging: enabled (0 messages dropped)
Log Buffer (4096 bytes):
*Mar  1 00:01:05.123: %LINK-3-UPDOWN: Interface GigabitEthernet1/0/1, changed state to up
*Mar  1 00:01:06.456: %LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet1/0/1, changed state to up
"""
CISCO_REPLIES = dict(zip(netdev.CISCO.commands.values(),
                         (CISCO_VERSION, CISCO_PORTS, "% Invalid input", CISCO_MACS, CISCO_ARP, CISCO_LOG)))


def test_a_cisco_switch_is_read_with_its_show_commands_only(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "REPLIES", CISCO_REPLIES)
    report, _fp, session = _read("SSH-2.0-Cisco-1.25")
    assert session.sent == list(netdev.CISCO.commands.values()) and all(c.startswith("show ") for c in session.sent)
    assert (report.platform, report.name) == ("Cisco IOS", "SW-CORE")
    assert report.info == {"model": "WS-C2960X-48TS-L", "version": "15.2(7)E2", "serial": "FOC1234X5YZ",
                           "uptime": "12 weeks, 3 days, 4 hours, 5 minutes"}
    assert [(i["name"], i["description"], i["status"], i["vlan"]) for i in report.interfaces] == [
        ("Gi1/0/1", "uplink to router", "up", "trunk"), ("Gi1/0/2", "", "down", "1"),   # a blank name column
        ("Gi1/0/3", "printer", "disabled", "20")]
    assert report.macs[1:] == [{"vlan": "1", "mac": "00:11:22:33:44:55", "type": "dynamic", "port": "Gi1/0/1"},
                               {"vlan": "20", "mac": "aa:bb:cc:00:01:02", "type": "dynamic", "port": "Gi1/0/3"}]
    assert report.arp[1] == {"ip": "192.168.1.20", "mac": "aa:bb:cc:00:01:02", "interface": "Vlan1"}
    assert len(report.log) == 2 and "%LINK-3-UPDOWN" in report.log[0]


def test_a_cisco_router_without_switch_ports_uses_its_ip_interfaces():
    brief = """Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     192.168.1.1     YES NVRAM  up                    up
GigabitEthernet0/1     unassigned      YES unset  administratively down down
"""
    report = netdev._cisco({**dict.fromkeys(netdev.CISCO.commands, ""), "version": CISCO_VERSION,
                            "ports": "% Invalid input detected", "ip": brief}, "10.0.0.1")
    assert [(i["name"], i["type"], i["status"]) for i in report.interfaces] == [
        ("GigabitEthernet0/0", "192.168.1.1", "up"), ("GigabitEthernet0/1", "", "disabled")]


def test_the_chat_shows_the_mac_table_and_only_filled_columns():
    report = netdev._cisco({**CISCO_REPLIES_BY_SECTION, "ip": ""}, "10.0.0.2")
    text = netdev.markdown(report)
    assert "**Who is connected (MAC table)** — 3 addresses on 3 ports" in text
    assert "| Name | Description | Type | Status | Vlan | Speed |" in text            # no MAC column: Cisco has none


CISCO_REPLIES_BY_SECTION = dict(zip(netdev.CISCO.commands, CISCO_REPLIES.values()))


# ── SNMP switches (TP-Link, D-Link, Netgear) ───────────────────────────

def _v(tag: int, raw: bytes):
    from src.servers.snmp import Value
    return Value(tag, raw)


def _s(text: str):
    return _v(0x04, text.encode())


def _i(n: int):
    return _v(0x02, n.to_bytes(2, "big"))


def test_a_switch_is_read_over_snmp_into_the_same_report():
    c = netdev.IF_COLUMNS
    walks = {
        c["descr"]: [(f"{c['descr']}.1", _s("TP-Link port 1")), (f"{c['descr']}.2", _s("TP-Link port 2"))],
        c["name"]: [(f"{c['name']}.1", _s("1/0/1")), (f"{c['name']}.2", _s(""))],      # port 2 has no ifName
        c["iftype"]: [(f"{c['iftype']}.1", _i(6)), (f"{c['iftype']}.2", _i(6))],
        c["admin"]: [(f"{c['admin']}.1", _i(1)), (f"{c['admin']}.2", _i(2))],
        c["oper"]: [(f"{c['oper']}.1", _i(1)), (f"{c['oper']}.2", _i(2))],
        c["speed"]: [(f"{c['speed']}.1", _v(0x42, (1000).to_bytes(2, "big")))],
        c["mac"]: [(f"{c['mac']}.1", _v(0x04, bytes.fromhex("98daa0000001")))],
        netdev.BRIDGE_PORT_IFINDEX: [(f"{netdev.BRIDGE_PORT_IFINDEX}.1", _i(1))],
        netdev.Q_FDB_PORT: [(f"{netdev.Q_FDB_PORT}.10.0.17.34.51.68.85", _i(1))],
        netdev.ARP_MAC: [(f"{netdev.ARP_MAC}.1.192.168.0.50", _v(0x04, bytes.fromhex("001122334455")))],
    }
    system = {netdev.SYS_NAME: _s("SW-Floor2"), netdev.SYS_DESCR: _s("JetStream 24-Port Gigabit L2 Managed Switch"),
              netdev.SYS_UPTIME: _v(0x43, (90 * 60 * 100).to_bytes(4, "big"))}
    report = netdev.snmp_report("10.0.0.2", walk=lambda oid: walks.get(oid, []), get=lambda oids: system)
    assert (report.platform, report.name, report.info["uptime"]) == ("SNMP", "SW-Floor2", "1h 30m")
    assert [(i["name"], i["status"], i["speed"], i["mac"]) for i in report.interfaces] == [
        ("1/0/1", "up", "1000 Mb/s", "98:da:a0:00:00:01"), ("TP-Link port 2", "disabled", "", "")]
    assert report.macs == [{"vlan": "10", "mac": "00:11:22:33:44:55", "type": "dynamic", "port": "1/0/1"}]
    assert report.arp == [{"ip": "192.168.0.50", "mac": "00:11:22:33:44:55", "interface": "1/0/1"}]


def test_a_device_saved_on_the_snmp_port_never_opens_ssh(monkeypatch):
    from src.servers import snmp

    monkeypatch.setattr(netdev, "read_snmp", lambda login: (_ for _ in ()).throw(snmp.SnmpError(
        "no answer from 10.0.0.2 on SNMP — check the community string and that SNMP is on")))
    login = replace(LOGIN, host="10.0.0.2", port=161, username="snmp", password="wrong")
    with pytest.raises(netdev.DeviceError, match="check the community string"):
        netdev.read_device(login, open_session=lambda login: pytest.fail("SSH was opened for an SNMP device"))
