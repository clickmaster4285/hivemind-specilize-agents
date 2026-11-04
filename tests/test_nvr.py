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
