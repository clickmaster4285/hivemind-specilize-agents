"""Router / switch reader (BACKLOG L19 D2): the platform from the SSH banner, only its read-only commands,
RouterOS output parsed — offline, with a fake SSH session and replies shaped like RouterOS v7's."""

from __future__ import annotations

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
