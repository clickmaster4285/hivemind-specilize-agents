"""Full NVR report (BACKLOG L17 N5): the NVRs / Cameras / Storage workbook, for Hikvision recorders.

The NVR (one login) gives its own status, disks and channels, and each channel's stream settings through its
streaming proxy. Each camera's own ISAPI gives the rest (device, network, system, image), read with the NVR's
login — cameras added through an NVR usually share it. One login attempt per camera, so none is locked out;
a camera that refuses or cannot be reached keeps its NVR-side columns and says why. GET requests only.

Every column is one entry in the tables below (sheet group, column, which reply, which field): a field a
firmware names differently is a one-line change, never new code.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

from src.servers.nvr import Fetch, NvrError, _all, _local, _text, _xml, http_fetch

# cameras read at the same time: the whole recorder in a few seconds, without loading the LAN or the
# cameras' small CPUs
CAMERA_WORKERS = 8
# cameras serve ISAPI on their web port; the 8000 in the NVR's list is Hikvision's SDK port, not HTTP
CAMERA_HTTP_PORT = 80
MB_PER_GB = 1024          # Hikvision reports disk sizes in MB
FRAME_RATE_SCALE = 100    # ISAPI frame rates are in hundredths of a frame per second (1200 = 12 fps)
HEALTHY_DISK = {"ok", "normal"}     # what Hikvision firmwares call a working disk

NVR_SOURCES = {"info": "/ISAPI/System/deviceInfo", "status": "/ISAPI/System/status",
               "hdd": "/ISAPI/ContentMgmt/Storage/hdd",
               "channels": "/ISAPI/ContentMgmt/InputProxy/channels",
               "states": "/ISAPI/ContentMgmt/InputProxy/channels/status"}
CAMERA_SOURCES = {"info": "/ISAPI/System/deviceInfo", "net": "/ISAPI/System/Network/interfaces",
                  "status": "/ISAPI/System/status", "time": "/ISAPI/System/time",
                  "ntp": "/ISAPI/System/time/ntpServers", "image": "/ISAPI/Image/channels/1"}
# stream N of channel C is "C0N" (1 = main, 2 = sub): through the NVR's proxy, else the camera's own (channel 1)
STREAMS = {"main": 1, "sub": 2}
NVR_STREAM = "/ISAPI/ContentMgmt/StreamingProxy/channels/{channel}0{stream}"
CAMERA_STREAM = "/ISAPI/Streaming/channels/10{stream}"


@dataclass
class Sources:
    """What one row is read from: parsed replies by name, and plain values the reader knows (ctx)."""
    xml: dict[str, Element] = field(default_factory=dict)
    ctx: dict[str, str] = field(default_factory=dict)


Get = Callable[[Sources], str]


def _el(root: Element | None, path: str) -> Element | None:
    """'DefaultGateway/ipAddress' → that element, matching names without their XML namespace."""
    node = root
    for name in path.split("/"):
        if node is None:
            return None
        node = next((e for e in node.iter() if e is not node and _local(e.tag) == name), None)
    return node


def text(source: str, path: str) -> Get:
    def get(s: Sources) -> str:
        el = _el(s.xml.get(source), path)
        return (el.text or "").strip() if el is not None else ""
    return get


def ctx(key: str) -> Get:
    return lambda s: s.ctx.get(key, "")


def first(*getters: Get) -> Get:
    """The first that has a value (firmwares name some fields differently)."""
    return lambda s: next((v for v in (g(s) for g in getters) if v), "")


def yes_no(get: Get) -> Get:
    return lambda s: {"true": "Yes", "false": "No"}.get(get(s).lower(), get(s))


def joined(a: Get, b: Get, sep: str) -> Get:
    return lambda s: f"{a(s)}{sep}{b(s)}" if a(s) and b(s) else ""


def scaled(get: Get, by: int) -> Get:
    def value(s: Sources) -> str:
        raw = get(s)
        return f"{int(raw) / by:g}" if raw.isdigit() else raw
    return value


def duration(get: Get) -> Get:
    """Seconds → '3h 31m' (days when there are some)."""
    def value(s: Sources) -> str:
        raw = get(s)
        if not raw.isdigit():
            return raw
        minutes = int(raw) // 60
        days, rest = divmod(minutes, 24 * 60)
        return (f"{days}d " if days else "") + f"{rest // 60}h {rest % 60}m"
    return value


def _stream(group: str, key: str) -> list[tuple[str, str, Get]]:
    name = group.split()[0]                     # "Main" / "Sub"
    return [
        (group, f"{name} Enabled", yes_no(text(key, "enabled"))),
        (group, f"{name} Resolution", joined(text(key, "videoResolutionWidth"), text(key, "videoResolutionHeight"), "x")),
        (group, f"{name} Codec", text(key, "videoCodecType")),
        (group, f"{name} Profile", first(text(key, "H265Profile"), text(key, "H264Profile"))),
        (group, f"{name} FPS", scaled(text(key, "maxFrameRate"), FRAME_RATE_SCALE)),
        (group, f"{name} Bitrate Mode", text(key, "videoQualityControlType")),
        (group, f"{name} Max Bitrate (kbps)", first(text(key, "vbrUpperCap"), text(key, "constantBitRate"))),
        (group, f"{name} Avg Bitrate (kbps)", text(key, "vbrAverageCap")),
        (group, f"{name} Quality", text(key, "fixedQuality")),
        (group, f"{name} I-Frame Interval (GOP)", text(key, "GovLength")),
        (group, f"{name} Smart Codec", yes_no(text(key, "SmartCodec/enabled"))),
        (group, f"{name} Scan Type", text(key, "videoScanType")),
        (group, f"{name} Audio", first(text(key, "Audio/audioCompressionType"), yes_no(text(key, "Audio/enabled")))),
    ]


# (sheet group, column, how to read it) — in the reference report's order
CAMERA_COLUMNS: list[tuple[str, str, Get]] = [
    ("NVR / Channel", "NVR Name", ctx("nvr_name")),
    ("NVR / Channel", "NVR IP", ctx("nvr_ip")),
    ("NVR / Channel", "Channel", text("proxy", "id")),
    ("NVR / Channel", "Channel Name", text("proxy", "name")),
    ("NVR / Channel", "Status", ctx("status")),
    ("NVR / Channel", "Connection Detail", text("state", "chanDetectResult")),
    ("NVR / Channel", "Password Strength", first(text("state", "passwordStatus"), text("state", "securityStatus"))),
    ("NVR / Channel", "Protocol", text("proxy", "proxyProtocol")),
    ("NVR / Channel", "Manage Port", text("proxy", "managePortNo")),
    ("Device Information", "Camera IP", ctx("camera_ip")),
    ("Device Information", "MAC Address", text("info", "macAddress")),
    ("Device Information", "Device Name", text("info", "deviceName")),
    ("Device Information", "Device ID", text("info", "deviceID")),
    ("Device Information", "Device Type", text("info", "deviceType")),
    ("Device Information", "Description", text("info", "deviceDescription")),
    ("Device Information", "Location", text("info", "deviceLocation")),
    ("Device Information", "Manufacturer", text("info", "manufacturer")),
    ("Device Information", "System Contact", text("info", "systemContact")),
    ("Device Information", "Model", first(text("info", "model"), text("proxy", "model"))),
    ("Device Information", "Serial Number", text("info", "serialNumber")),
    ("Device Information", "Sub Serial Number", text("info", "subSerialNumber")),
    ("Device Information", "Firmware Version", text("info", "firmwareVersion")),
    ("Device Information", "Firmware Build", text("info", "firmwareReleasedDate")),
    ("Device Information", "Encoder Version", text("info", "encoderVersion")),
    ("Device Information", "Encoder Build", text("info", "encoderReleasedDate")),
    ("Device Information", "Boot Version", text("info", "bootVersion")),
    ("Device Information", "Boot Build", text("info", "bootReleasedDate")),
    ("Device Information", "Hardware Version", text("info", "hardwareVersion")),
    ("Device Information", "Telecontrol ID", text("info", "telecontrolID")),
    ("Network", "Addressing", text("net", "addressingType")),
    ("Network", "IP Version", text("net", "ipVersion")),
    ("Network", "Subnet Mask", text("net", "subnetMask")),
    ("Network", "Default Gateway", text("net", "DefaultGateway/ipAddress")),
    ("Network", "Primary DNS", text("net", "PrimaryDNS/ipAddress")),
    ("Network", "Secondary DNS", text("net", "SecondaryDNS/ipAddress")),
    ("Network", "IPv6 Address", text("net", "ipv6Address")),
    ("Network", "Link Speed (Mbps)", text("net", "Link/speed")),
    ("Network", "Duplex", text("net", "Link/duplex")),
    ("Network", "Auto Negotiation", yes_no(text("net", "Link/autoNegotiation"))),
    ("Network", "MTU", text("net", "Link/MTU")),
    ("System", "Device Time", text("status", "currentDeviceTime")),
    ("System", "Uptime", duration(text("status", "deviceUpTime"))),
    ("System", "CPU %", text("status", "cpuUtilization")),
    ("System", "CPU", text("status", "cpuDescription")),
    ("System", "Memory Usage", text("status", "memoryUsage")),
    ("System", "Memory Available", text("status", "memoryAvailable")),
    ("System", "Time Mode", text("time", "timeMode")),
    ("System", "Time Zone", text("time", "timeZone")),
    ("System", "NTP Server", first(text("ntp", "hostName"), text("ntp", "ipAddress"))),
    *_stream("Main Stream", "main"),
    *_stream("Sub Stream", "sub"),
    ("Image Settings", "Day/Night (IR-Cut)", text("image", "IrcutFilterType")),
    ("Image Settings", "Exposure", text("image", "ExposureType")),
    ("Image Settings", "Shutter", text("image", "ShutterLevel")),
    ("Image Settings", "Gain", text("image", "GainLevel")),
    ("Image Settings", "WDR", text("image", "WDR/mode")),
    ("Image Settings", "BLC", yes_no(text("image", "BLC/enabled"))),
    ("Image Settings", "HLC", yes_no(text("image", "HLC/enabled"))),
    ("Image Settings", "Noise Reduction", text("image", "NoiseReduce/mode")),
    ("Image Settings", "White Balance", text("image", "WhiteBlanceStyle")),
    ("Image Settings", "Brightness", text("image", "brightnessLevel")),
    ("Image Settings", "Contrast", text("image", "contrastLevel")),
    ("Image Settings", "Saturation", text("image", "saturationLevel")),
    ("Image Settings", "Sharpness", text("image", "SharpnessLevel")),
    ("Image Settings", "Image Flip", yes_no(text("image", "ImageFlip/enabled"))),
    ("Image Settings", "Power Line Frequency", text("image", "powerLineFrequencyMode")),
    ("Image Settings", "Supplement Light", text("image", "supplementLightMode")),
    ("Other", "Data Source", ctx("source")),
]


@dataclass
class Disk:
    name: str
    status: str
    capacity_gb: float
    free_gb: float
    type: str
    attribute: str


@dataclass
class NvrReport:
    host: str
    nvr: Sources
    disks: list[Disk]
    cameras: list[Sources]

    def column(self, name: str) -> list[str]:
        get = next(g for _group, col, g in CAMERA_COLUMNS if col == name)
        return [get(c) for c in self.cameras]


def _parse(fetch: Fetch, path: str) -> Element | None:
    """One read: the parsed reply, or None when the device has no such page, dropped off, or sent something
    unusable — one missing reply leaves its columns blank, it never stops the report."""
    try:
        status, body = fetch(path)
        return _xml(body) if status == 200 else None
    except NvrError:
        return None


def _disks(hdd: Element | None) -> list[Disk]:
    def gb(el: Element, name: str) -> float:
        value = _text(el, name)
        return round(int(value) / MB_PER_GB, 1) if value.isdigit() else 0.0
    return [Disk(name=_text(d, "hddName") or f"HDD {_text(d, 'id')}", status=_text(d, "status"),
                 capacity_gb=gb(d, "capacity"), free_gb=gb(d, "freeSpace"), type=_text(d, "hddType"),
                 attribute=_text(d, "property")) for d in (_all(hdd, "hdd") if hdd is not None else [])]


def _camera(nvr_fetch: Fetch, connect: Callable[..., Fetch], login: tuple[str, str, str, str],
            proxy: Element, state: Element | None) -> Sources:
    """One channel: the NVR's view of it, then the camera's own ISAPI (one login attempt)."""
    nvr_name, nvr_ip, username, password = login
    channel = _text(proxy, "id")
    ip = _text(proxy, "ipAddress") or _text(proxy, "hostName")
    online = _text(state, "online").lower() if state is not None else ""
    row = Sources(xml={"proxy": proxy, **({"state": state} if state is not None else {})},
                  ctx={"nvr_name": nvr_name, "nvr_ip": nvr_ip, "camera_ip": ip,
                       "status": {"true": "Online", "false": "Offline"}.get(online, "")})
    for key, stream in STREAMS.items():
        parsed = _parse(nvr_fetch, NVR_STREAM.format(channel=channel, stream=stream))
        if parsed is not None:
            row.xml[key] = parsed
    if not ip:
        row.ctx["source"] = "NVR only (no camera address)"
        return row
    camera = connect(ip, CAMERA_HTTP_PORT, username, password)
    try:
        status, body = camera(CAMERA_SOURCES["info"])           # the one login attempt
    except NvrError:
        row.ctx["source"] = "NVR only (camera not reachable)"
        return row
    if status == 401:
        row.ctx["source"] = "NVR only (the camera refused the NVR's login)"
        return row
    if status == 200:
        try:
            row.xml["info"] = _xml(body)
        except NvrError:
            pass                                                # its columns stay blank; the rest is still read
    for key, path in CAMERA_SOURCES.items():
        if key != "info":
            parsed = _parse(camera, path)
            if parsed is not None:
                row.xml[key] = parsed
    for key, stream in STREAMS.items():
        if key not in row.xml:
            parsed = _parse(camera, CAMERA_STREAM.format(stream=stream))
            if parsed is not None:
                row.xml[key] = parsed
    row.ctx["source"] = "NVR + camera"
    return row


def build(host: str, username: str, password: str, port: int = 80, *,
          connect: Callable[..., Fetch] = http_fetch) -> NvrReport:
    nvr_fetch = connect(host, port, username, password)
    replies = {key: _parse(nvr_fetch, path) for key, path in NVR_SOURCES.items()}
    if replies["info"] is None:
        raise NvrError("the recorder did not answer its device information (check the login)")
    states = {_text(s, "id"): s for s in _all(replies["states"], "InputProxyChannelStatus")} \
        if replies["states"] is not None else {}
    channels = _all(replies["channels"], "InputProxyChannel") if replies["channels"] is not None else []
    name = _text(replies["info"], "deviceName") or host
    with ThreadPoolExecutor(max_workers=CAMERA_WORKERS) as pool:
        cameras = list(pool.map(lambda ch: _camera(nvr_fetch, connect, (name, host, username, password), ch,
                                                   states.get(_text(ch, "id"))), channels))
    nvr = Sources(xml={k: v for k, v in replies.items() if v is not None}, ctx={"name": name})
    return NvrReport(host=host, nvr=nvr, disks=_disks(replies["hdd"]), cameras=cameras)


# ── the NVRs sheet ─────────────────────────────────────────────────────

def nvr_row(rep: NvrReport) -> list[tuple[str, str]]:
    status = rep.column("Status")
    healthy = sum(d.status.lower() in HEALTHY_DISK for d in rep.disks)
    s = rep.nvr
    return [
        ("NVR Name", s.ctx["name"]), ("IP Address", rep.host), ("Manufacturer", "Hikvision"),
        ("Model", text("info", "model")(s)), ("Serial Number", text("info", "serialNumber")(s)),
        ("Firmware", text("info", "firmwareVersion")(s)), ("MAC Address", text("info", "macAddress")(s)),
        ("Status", "Online"), ("Uptime", duration(text("status", "deviceUpTime"))(s)),
        ("CPU %", text("status", "cpuUtilization")(s)), ("Memory Usage", text("status", "memoryUsage")(s)),
        ("Cameras Connected", str(len(rep.cameras))), ("Cameras Online", str(status.count("Online"))),
        ("Cameras Offline", str(status.count("Offline"))), ("HDD Count", str(len(rep.disks))),
        ("Storage Health", f"{healthy}/{len(rep.disks)} normal" if rep.disks else ""),
        ("Total Capacity (GB)", f"{sum(d.capacity_gb for d in rep.disks):g}"),
        ("Free (GB)", f"{sum(d.free_gb for d in rep.disks):g}"),
        ("Camera Data Source", "Live from NVR + cameras"),
    ]


# ── chat + workbook ────────────────────────────────────────────────────

CHAT_COLUMNS = ("Channel", "Channel Name", "Camera IP", "MAC Address", "Model", "Firmware Version", "Status")


def markdown(rep: NvrReport) -> str:
    facts = dict(nvr_row(rep))
    about = " · ".join(f"{k} {facts[k]}" for k in ("Model", "Firmware", "Uptime", "CPU %") if facts.get(k))
    disks = (f" Storage: {facts['HDD Count']} disks, {facts['Storage Health']}, {facts['Total Capacity (GB)']} GB "
             f"({facts['Free (GB)']} GB free).") if rep.disks else ""
    lines = [f"Recorder **{facts['NVR Name']}** ({rep.host}) — {about}. Cameras: **{facts['Cameras Connected']}**, "
             f"{facts['Cameras Online']} online, {facts['Cameras Offline']} offline.{disks}", "",
             "| " + " | ".join(CHAT_COLUMNS) + " |", "|" + "---|" * len(CHAT_COLUMNS)]
    columns = [rep.column(c) for c in CHAT_COLUMNS]
    lines += ["| " + " | ".join(col[i].replace("|", "/") for col in columns) + " |" for i in range(len(rep.cameras))]
    lines += ["", f"_The .xlsx has every detail: {len(CAMERA_COLUMNS)} columns per camera (device, network, system, "
                  "main and sub stream, image settings), the NVR, and each disk._"]
    return "\n".join(lines)


def xlsx_bytes(rep: NvrReport) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    bold = Font(bold=True)
    wb = Workbook()
    nvrs = wb.active
    nvrs.title = "NVRs"
    facts = nvr_row(rep)
    nvrs.append([k for k, _ in facts])
    nvrs.append([v for _, v in facts])
    cams = wb.create_sheet("Cameras")
    groups = [g for g, _c, _ in CAMERA_COLUMNS]
    cams.append([g if i == 0 or groups[i - 1] != g else None for i, g in enumerate(groups)])
    cams.append([c for _g, c, _ in CAMERA_COLUMNS])
    for camera in rep.cameras:
        cams.append([get(camera) for _g, _c, get in CAMERA_COLUMNS])
    storage = wb.create_sheet("Storage")
    storage.append(["NVR Name", "Disk", "Status", "Capacity (GB)", "Free (GB)", "Usage", "Type", "Attribute"])
    for d in rep.disks:
        used = f"{(1 - d.free_gb / d.capacity_gb) * 100:.1f}%" if d.capacity_gb else ""
        storage.append([facts[0][1], d.name, d.status, d.capacity_gb, d.free_gb, used, d.type, d.attribute])
    for ws, header_rows in ((nvrs, 1), (cams, 2), (storage, 1)):
        for row in ws.iter_rows(max_row=header_rows):
            for cell in row:
                cell.font = bold
        ws.freeze_panes = ws.cell(row=header_rows + 1, column=1)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
