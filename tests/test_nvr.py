"""NVR camera list (BACKLOG L17 N1): which API a recorder speaks, and reading its channels — offline, with
replies shaped like the vendors' (Hikvision ISAPI XML, Dahua key=value lines)."""

from __future__ import annotations

import pytest

from src.servers import nvr

HIK_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<DeviceInfo version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <deviceName>Main NVR</deviceName><model>DS-7608NXI-I2/S</model>
  <serialNumber>DS-7608NXI0120230101CCRR</serialNumber><firmwareVersion>V4.62.210</firmwareVersion>
</DeviceInfo>"""

HIK_CHANNELS = """<?xml version="1.0" encoding="UTF-8"?>
<InputProxyChannelList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <InputProxyChannel><id>1</id><name>Gate</name>
    <sourceInputPortDescriptor><proxyProtocol>HIKVISION</proxyProtocol><addressingFormatType>ipaddress</addressingFormatType>
      <ipAddress>192.168.190.64</ipAddress><managePortNo>8000</managePortNo></sourceInputPortDescriptor>
  </InputProxyChannel>
  <InputProxyChannel><id>2</id><name>Parking</name>
    <sourceInputPortDescriptor><proxyProtocol>ONVIF</proxyProtocol><ipAddress>192.168.190.65</ipAddress>
    </sourceInputPortDescriptor>
  </InputProxyChannel>
</InputProxyChannelList>"""

HIK_STATUS = """<?xml version="1.0" encoding="UTF-8"?>
<InputProxyChannelStatusList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <InputProxyChannelStatus><id>1</id><online>true</online></InputProxyChannelStatus>
  <InputProxyChannelStatus><id>2</id><online>false</online></InputProxyChannelStatus>
</InputProxyChannelStatusList>"""

DAHUA_TITLES = "table.ChannelTitle[0].Name=Front Door\r\ntable.ChannelTitle[1].Name=Yard\r\n"
DAHUA_CAMERAS = ("camera[0].Channel=0\r\ncamera[0].DeviceInfo.Address=192.168.1.108\r\n"
                 "camera[0].DeviceInfo.DeviceType=IPC-HFW2431S\r\ncamera[0].DeviceInfo.ProtocolType=Private\r\n"
                 "camera[1].Channel=1\r\ncamera[1].DeviceInfo.Address=192.168.1.109\r\n")
DAHUA_STATES = "states[0].channel=0\r\nstates[0].connectionState=Connected\r\n" \
               "states[1].channel=1\r\nstates[1].connectionState=Disconnected\r\n"

CREDENTIALS = ("admin", "right-password")


def _device(replies: dict[str, str]):
    """A fake recorder: without the right login every API path asks for one; unknown paths are 404."""
    calls: list[tuple[str, bool]] = []

    def connect(host, port, username=None, password=None):
        authed = (username, password) == CREDENTIALS

        def fetch(path):
            calls.append((path, authed))
            if path not in replies:
                return 404, ""
            return (200, replies[path]) if authed else (401, "")
        return fetch

    return connect, calls


HIKVISION = {"/ISAPI/System/deviceInfo": HIK_INFO, "/ISAPI/ContentMgmt/InputProxy/channels": HIK_CHANNELS,
             "/ISAPI/ContentMgmt/InputProxy/channels/status": HIK_STATUS}
DAHUA = {"/cgi-bin/magicBox.cgi?action=getDeviceType": "type=NVR4108HS-4KS2\r\n",
         "/cgi-bin/magicBox.cgi?action=getSerialNo": "sn=6G0123PAZ00042\r\n",
         "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle": DAHUA_TITLES,
         "/cgi-bin/LogicDeviceManager.cgi?action=getCameraAll": DAHUA_CAMERAS,
         "/cgi-bin/LogicDeviceManager.cgi?action=getCameraState&uniqueChannels[0]=-1": DAHUA_STATES}


def test_a_hikvision_recorder_lists_its_cameras_with_ip_and_online_state():
    connect, _ = _device(HIKVISION)
    rec = nvr.read_recorder("192.168.190.248", *CREDENTIALS, connect=connect)
    assert (rec.api, rec.name, rec.model, rec.firmware) == ("Hikvision ISAPI", "Main NVR", "DS-7608NXI-I2/S", "V4.62.210")
    assert [(c.channel, c.name, c.ip, c.protocol, c.online) for c in rec.cameras] == [
        (1, "Gate", "192.168.190.64", "HIKVISION", True), (2, "Parking", "192.168.190.65", "ONVIF", False)]


def test_older_hikvision_firmware_without_a_status_list_still_lists_the_cameras():
    replies = {k: v for k, v in HIKVISION.items() if not k.endswith("/status")}
    connect, _ = _device(replies)
    rec = nvr.read_recorder("10.0.0.5", *CREDENTIALS, connect=connect)
    assert [c.online for c in rec.cameras] == [None, None]                # unknown, not "offline"


def test_a_dahua_recorder_lists_its_cameras_numbered_from_one():
    connect, _ = _device(DAHUA)
    rec = nvr.read_recorder("192.168.1.100", *CREDENTIALS, connect=connect)
    assert (rec.api, rec.model, rec.serial) == ("Dahua HTTP API", "NVR4108HS-4KS2", "6G0123PAZ00042")
    assert [(c.channel, c.name, c.ip, c.model, c.online) for c in rec.cameras] == [
        (1, "Front Door", "192.168.1.108", "IPC-HFW2431S", True), (2, "Yard", "192.168.1.109", "", False)]


def test_the_api_is_found_by_asking_the_device_without_a_login():
    connect, calls = _device(DAHUA)
    nvr.read_recorder("192.168.1.100", *CREDENTIALS, connect=connect)
    probes = [path for path, authed in calls if not authed]
    assert probes == ["/ISAPI/System/deviceInfo", "/cgi-bin/magicBox.cgi?action=getDeviceType"]


def test_a_wrong_password_is_tried_once_and_explained():
    connect, calls = _device(HIKVISION)
    with pytest.raises(nvr.NvrError, match="refused the login"):
        nvr.read_recorder("192.168.190.248", "admin", "wrong", connect=connect)
    assert [path for path, _ in calls].count("/ISAPI/System/deviceInfo") == 2   # the probe + one login attempt


def test_a_device_with_no_known_api_is_named_not_guessed():
    connect, _ = _device({})
    with pytest.raises(nvr.NvrError, match="neither the Hikvision nor the Dahua API"):
        nvr.read_recorder("192.168.1.1", *CREDENTIALS, connect=connect)


def test_entity_tricks_in_a_reply_are_refused():
    bomb = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
            "<DeviceInfo><deviceName>&b;</deviceName></DeviceInfo>")
    with pytest.raises(nvr.NvrError, match="not valid XML"):
        nvr.parse_hikvision_info(bomb)


# ── the login store and the chat job (N2, N3) ──────────────────────────

from src.servers import server_vault  # noqa: E402

USER = 4242


@pytest.fixture
def vault():
    server_vault.clear(USER)
    yield
    server_vault.clear(USER)


def test_ssh_tools_never_get_a_recorder_login(vault):
    server_vault.add(USER, label="nvr", host="192.168.190.248", port=80, username="admin", password="s3cret-pw",
                     kind="nvr")
    assert server_vault.find(USER, None) is None                       # the only login is a recorder, not a server
    assert server_vault.find(USER, None, kind="nvr").host == "192.168.190.248"
    assert [s.kind for s in server_vault.list_servers(USER, kind=None)] == ["nvr"]
    assert "s3cret-pw" not in repr(server_vault.find(USER, None, kind="nvr"))     # the password never shows


def test_a_recorder_must_be_on_the_local_network():
    assert server_vault.check_private("192.168.190.248") == "192.168.190.248"
    for host in ("8.8.8.8", "127.0.0.1"):
        with pytest.raises(server_vault.VaultError, match="local network"):
            server_vault.check_private(host)


def _job(monkeypatch, tmp_path, **kwargs):
    from src.core.config import Config
    from dataclasses import replace

    connect, _ = _device(HIKVISION)
    original = nvr.read_recorder
    monkeypatch.setattr(nvr, "read_recorder",
                        lambda host, user, pw, port: original(host, user, pw, port, connect=connect))
    events: list[dict] = []
    config = replace(Config.from_env(), artifacts_dir=tmp_path)
    output, errors, _ = nvr.run_nvr_cameras("list the cameras", job_id="job1", config=config,
                                            on_progress=events.append, user_id=USER, **kwargs)
    return output, errors, events


def test_the_chat_job_answers_with_the_camera_table_and_an_xlsx(vault, monkeypatch, tmp_path):
    server_vault.add(USER, label="nvr", host="192.168.190.248", port=80, username="admin",
                     password="right-password", kind="nvr")
    output, errors, events = _job(monkeypatch, tmp_path)
    assert not errors
    assert "**2 cameras** — 1 online, 1 offline" in output
    assert "| 1 | Gate | 192.168.190.64 |" in output
    assert any(e["type"] == "artifact_ready" and e["filename"].endswith(".xlsx") for e in events)


def test_without_a_recorder_login_the_chat_says_where_to_add_it(vault, monkeypatch, tmp_path):
    output, errors, _ = _job(monkeypatch, tmp_path)
    assert "Settings → Devices" in output and not errors


# ── who may use Settings → Devices (L19 D1b) ───────────────────────────

def _devices_api(role: str):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.core.auth import get_current_user
    from src.core.models_db import User
    from web.routers import servers

    app = FastAPI()
    app.include_router(servers.router)
    app.dependency_overrides[get_current_user] = lambda: User(id=USER, username="ana", password_hash="x", role=role)
    return TestClient(app)


def test_every_user_has_devices_but_only_admins_may_trust_the_agent(vault):
    member, admin = _devices_api("member"), _devices_api("admin")
    assert member.get("/api/servers").status_code == 200
    trusted = {"host": "192.168.1.20", "username": "root", "password": "x", "mode": "trusted"}
    assert member.post("/api/servers", json=trusted).status_code == 403
    assert member.put("/api/servers/abc/mode", json={"mode": "trusted"}).status_code == 403
    assert admin.put("/api/servers/abc/mode", json={"mode": "trusted"}).status_code == 400    # allowed; no such server
