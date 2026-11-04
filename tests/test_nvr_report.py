"""Full NVR report (BACKLOG L17 N5): NVR status, disks, stream settings, each camera's own details — offline,
with a fake recorder and fake cameras that answer like Hikvision ISAPI."""

from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from src.servers import nvr_report
from tests.test_nvr import HIK_CHANNELS, HIK_INFO, HIK_STATUS

NS = 'xmlns="http://www.hikvision.com/ver20/XMLSchema"'
NVR_STATUS = f"""<DeviceStatus {NS}><deviceUpTime>9360</deviceUpTime>
  <CPUList><CPU><cpuUtilization>7</cpuUtilization></CPU></CPUList>
  <MemoryList><Memory><memoryUsage>41</memoryUsage></Memory></MemoryList></DeviceStatus>"""
HDD = f"""<hddList {NS}>
  <hdd><id>1</id><hddName>hdd1</hddName><status>ok</status><capacity>7630885</capacity><freeSpace>0</freeSpace>
       <hddType>SATA</hddType><property>RW</property></hdd>
  <hdd><id>2</id><hddName>hdd2</hddName><status>sleep</status><capacity>7630885</capacity><freeSpace>1024</freeSpace>
       <hddType>SATA</hddType><property>RW</property></hdd></hddList>"""
STREAM = f"""<StreamingChannel {NS}><id>101</id><enabled>true</enabled><Video><videoCodecType>H.265</videoCodecType>
  <videoResolutionWidth>3840</videoResolutionWidth><videoResolutionHeight>2160</videoResolutionHeight>
  <videoQualityControlType>VBR</videoQualityControlType><fixedQuality>100</fixedQuality>
  <vbrUpperCap>5120</vbrUpperCap><maxFrameRate>1200</maxFrameRate><GovLength>50</GovLength>
  <SmartCodec><enabled>true</enabled></SmartCodec><H265Profile>Main</H265Profile></Video></StreamingChannel>"""
CAM_INFO = f"""<DeviceInfo {NS}><deviceName>IP CAMERA</deviceName><deviceID>bb12-0d3b</deviceID>
  <model>DS-2CD1083G2-LIUF/SL</model><serialNumber>DS-2CD1083G2-LIUF/SL20250517AAWRGA229558</serialNumber>
  <macAddress>50:e5:38:8f:0d:3b</macAddress><firmwareVersion>V5.8.4</firmwareVersion>
  <firmwareReleasedDate>build 241213</firmwareReleasedDate><deviceType>IPCamera</deviceType></DeviceInfo>"""
CAM_NET = f"""<NetworkInterfaceList {NS}><NetworkInterface><IPAddress><ipVersion>dual</ipVersion>
  <addressingType>static</addressingType><ipAddress>192.168.190.64</ipAddress><subnetMask>255.255.255.0</subnetMask>
  <DefaultGateway><ipAddress>192.168.190.1</ipAddress></DefaultGateway>
  <PrimaryDNS><ipAddress>0.0.0.0</ipAddress></PrimaryDNS></IPAddress>
  <Link><autoNegotiation>true</autoNegotiation><speed>10</speed><duplex>half</duplex><MTU>1500</MTU></Link>
  </NetworkInterface></NetworkInterfaceList>"""
CAM_STATUS = f"""<DeviceStatus {NS}><currentDeviceTime>2026-11-04T17:04:20+08:00</currentDeviceTime>
  <deviceUpTime>12660</deviceUpTime><CPUList><CPU><cpuUtilization>34</cpuUtilization></CPU></CPUList></DeviceStatus>"""
CAM_IMAGE = f"""<ImageChannel {NS}><WDR><mode>close</mode></WDR><BLC><enabled>false</enabled></BLC>
  <Color><brightnessLevel>50</brightnessLevel></Color><powerLineFrequency>
  <powerLineFrequencyMode>50hz</powerLineFrequencyMode></powerLineFrequency></ImageChannel>"""

LOGIN = ("admin", "right-password")
NVR = "192.168.190.248"
DEVICES = {
    NVR: {"/ISAPI/System/deviceInfo": HIK_INFO, "/ISAPI/System/status": NVR_STATUS,
          "/ISAPI/ContentMgmt/Storage/hdd": HDD, "/ISAPI/ContentMgmt/InputProxy/channels": HIK_CHANNELS,
          "/ISAPI/ContentMgmt/InputProxy/channels/status": HIK_STATUS,
          "/ISAPI/ContentMgmt/StreamingProxy/channels/101": STREAM},
    "192.168.190.64": {"/ISAPI/System/deviceInfo": CAM_INFO, "/ISAPI/System/Network/interfaces": CAM_NET,
                       "/ISAPI/System/status": CAM_STATUS, "/ISAPI/Image/channels/1": CAM_IMAGE},
    "192.168.190.65": "refuses",                      # a camera with its own password
}


def _network():
    calls: list[tuple[str, str]] = []

    def connect(host, port, username=None, password=None):
        def fetch(path):
            calls.append((host, path))
            pages = DEVICES.get(host)
            if pages == "refuses" or (username, password) != LOGIN:
                return 401, ""
            return (200, pages[path]) if pages and path in pages else (404, "")
        return fetch

    return connect, calls


def _build():
    connect, calls = _network()
    return nvr_report.build(NVR, *LOGIN, connect=connect), calls


def test_each_camera_row_has_its_own_details_from_the_camera():
    rep, _ = _build()
    gate = dict(zip([c for _g, c, _ in nvr_report.CAMERA_COLUMNS],
                    [get(rep.cameras[0]) for _g, _c, get in nvr_report.CAMERA_COLUMNS]))
    assert (gate["Channel Name"], gate["Status"], gate["Camera IP"]) == ("Gate", "Online", "192.168.190.64")
    assert (gate["MAC Address"], gate["Firmware Version"], gate["Firmware Build"]) == \
        ("50:e5:38:8f:0d:3b", "V5.8.4", "build 241213")
    assert (gate["Subnet Mask"], gate["Default Gateway"], gate["Link Speed (Mbps)"], gate["Auto Negotiation"]) == \
        ("255.255.255.0", "192.168.190.1", "10", "Yes")
    assert (gate["Uptime"], gate["CPU %"], gate["WDR"], gate["BLC"]) == ("3h 31m", "34", "close", "No")
    assert (gate["Main Resolution"], gate["Main FPS"], gate["Main Max Bitrate (kbps)"], gate["Main Smart Codec"]) == \
        ("3840x2160", "12", "5120", "Yes")                         # stream settings through the NVR's proxy
    assert gate["Data Source"] == "NVR + camera"


def test_a_camera_that_refuses_the_login_is_tried_once_and_keeps_its_nvr_columns():
    rep, calls = _build()
    parking = rep.cameras[1]
    assert nvr_report.ctx("source")(parking) == "NVR only (the camera refused the NVR's login)"
    assert nvr_report.text("proxy", "name")(parking) == "Parking"
    assert [path for host, path in calls if host == "192.168.190.65"] == ["/ISAPI/System/deviceInfo"]


def test_the_nvr_and_its_disks_are_summarised():
    rep, _ = _build()
    facts = dict(nvr_report.nvr_row(rep))
    assert (facts["Model"], facts["Uptime"], facts["CPU %"], facts["Memory Usage"]) == \
        ("DS-7608NXI-I2/S", "2h 36m", "7", "41")
    assert (facts["Cameras Online"], facts["Cameras Offline"], facts["HDD Count"], facts["Storage Health"]) == \
        ("1", "1", "2", "1/2 normal")
    assert [(d.name, d.status, d.capacity_gb, d.free_gb) for d in rep.disks] == [
        ("hdd1", "ok", 7452.0, 0.0), ("hdd2", "sleep", 7452.0, 1.0)]


def test_the_workbook_has_the_reference_report_sheets():
    rep, _ = _build()
    wb = load_workbook(BytesIO(nvr_report.xlsx_bytes(rep)))
    assert wb.sheetnames == ["NVRs", "Cameras", "Storage"]
    cams = list(wb["Cameras"].iter_rows(values_only=True))
    assert cams[0][0] == "NVR / Channel" and cams[1][:4] == ("NVR Name", "NVR IP", "Channel", "Channel Name")
    assert len(cams) == 2 + len(rep.cameras) and len(cams[1]) == len(nvr_report.CAMERA_COLUMNS)
    assert list(wb["Storage"].iter_rows(values_only=True))[1][1:3] == ("hdd1", "ok")
