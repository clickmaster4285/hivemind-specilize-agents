"""nvr_cameras — the cameras an NVR records (BACKLOG L17, ISSUES_LOG O17).

Read-only: GET requests to the recorder's own API only — never its web pages, never a setting changed.
Which API a device speaks is found by asking it: the vendor path that answers with a login request is the
one. Each read makes one login attempt (recorders lock an account after a few wrong passwords — no retries).
Replies are untrusted text from the network: XML is parsed with defusedxml.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

import requests
from defusedxml import DefusedXmlException, ElementTree
from requests.auth import HTTPDigestAuth

# per request: a recorder on the LAN answers in well under a second; a dead or wrong address should fail fast
REQUEST_TIMEOUT = 10

Fetch = Callable[[str], tuple[int, str]]       # path → (HTTP status, body)


class NvrError(RuntimeError):
    """The recorder could not be read (no known API, login refused, unreachable)."""


@dataclass
class Camera:
    channel: int                  # as the recorder numbers it for people (1 = first)
    name: str = ""
    ip: str = ""
    model: str = ""
    protocol: str = ""
    online: bool | None = None    # None = the recorder did not say


@dataclass
class Recorder:
    host: str
    api: str
    model: str = ""
    name: str = ""
    serial: str = ""
    firmware: str = ""
    cameras: list[Camera] = field(default_factory=list)


# ── Hikvision ISAPI (XML) ──────────────────────────────────────────────

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml(text: str) -> Element:
    try:
        return ElementTree.fromstring(text)
    except (ElementTree.ParseError, DefusedXmlException) as exc:      # broken, or entity tricks (XXE, "billion laughs")
        raise NvrError("the recorder sent a reply that is not valid XML") from exc


def _all(root: Element, name: str) -> list[Element]:
    return [el for el in root.iter() if _local(el.tag) == name]


def _text(root: Element, name: str) -> str:
    found = _all(root, name)
    return (found[0].text or "").strip() if found else ""


def parse_hikvision_info(text: str) -> dict[str, str]:
    root = _xml(text)
    return {"name": _text(root, "deviceName"), "model": _text(root, "model"),
            "serial": _text(root, "serialNumber"), "firmware": _text(root, "firmwareVersion")}


def parse_hikvision_channels(text: str) -> list[Camera]:
    cameras = []
    for ch in _all(_xml(text), "InputProxyChannel"):
        cameras.append(Camera(channel=int(_text(ch, "id") or 0), name=_text(ch, "name"),
                              ip=_text(ch, "ipAddress") or _text(ch, "hostName"),
                              model=_text(ch, "model"), protocol=_text(ch, "proxyProtocol")))
    return cameras


def parse_hikvision_status(text: str) -> dict[int, bool]:
    return {int(_text(st, "id") or 0): _text(st, "online").lower() == "true"
            for st in _all(_xml(text), "InputProxyChannelStatus")}


def _read_hikvision(fetch: Fetch, host: str) -> Recorder:
    info = parse_hikvision_info(_get(fetch, "/ISAPI/System/deviceInfo"))
    cameras = parse_hikvision_channels(_get(fetch, "/ISAPI/ContentMgmt/InputProxy/channels"))
    status, body = fetch("/ISAPI/ContentMgmt/InputProxy/channels/status")
    online = parse_hikvision_status(body) if status == 200 else {}      # older firmware: no status list
    for cam in cameras:
        cam.online = online.get(cam.channel)
    return Recorder(host=host, api="Hikvision ISAPI", cameras=cameras, **info)


# ── Dahua HTTP API (key=value lines) ───────────────────────────────────

def _pairs(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _indexed(pairs: dict[str, str], prefix: str) -> dict[int, dict[str, str]]:
    """`camera[3].DeviceInfo.Address=…` → {3: {"DeviceInfo.Address": …}} for the given prefix."""
    pattern = re.compile(rf"^{re.escape(prefix)}\[(\d+)\]\.(.+)$")
    out: dict[int, dict[str, str]] = {}
    for key, value in pairs.items():
        m = pattern.match(key)
        if m:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = value
    return out


def parse_dahua_cameras(titles: str, cameras: str, states: str = "") -> list[Camera]:
    names = {i: v.get("Name", "") for i, v in _indexed(_pairs(titles), "table.ChannelTitle").items()}
    connected = {int(v.get("channel", -1)): v.get("connectionState", "").lower() == "connected"
                 for v in _indexed(_pairs(states), "states").values()}
    out = []
    for cam in _indexed(_pairs(cameras), "camera").values():
        index = int(cam.get("Channel", -1))
        if index < 0:
            continue
        out.append(Camera(channel=index + 1, name=names.get(index, cam.get("DeviceInfo.Name", "")),   # Dahua counts from 0
                          ip=cam.get("DeviceInfo.Address", ""), model=cam.get("DeviceInfo.DeviceType", ""),
                          protocol=cam.get("DeviceInfo.ProtocolType", ""), online=connected.get(index)))
    return sorted(out, key=lambda c: c.channel)


def _read_dahua(fetch: Fetch, host: str) -> Recorder:
    model = _pairs(_get(fetch, "/cgi-bin/magicBox.cgi?action=getDeviceType")).get("type", "")
    status, serial = fetch("/cgi-bin/magicBox.cgi?action=getSerialNo")
    titles = _get(fetch, "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle")
    cameras = _get(fetch, "/cgi-bin/LogicDeviceManager.cgi?action=getCameraAll")
    state_status, states = fetch("/cgi-bin/LogicDeviceManager.cgi?action=getCameraState&uniqueChannels[0]=-1")
    return Recorder(host=host, api="Dahua HTTP API", model=model,
                    serial=_pairs(serial).get("sn", "") if status == 200 else "",
                    cameras=parse_dahua_cameras(titles, cameras, states if state_status == 200 else ""))


# ── which API, and reading it ──────────────────────────────────────────

@dataclass(frozen=True)
class Dialect:
    api: str
    probe: str                                  # a path that asks for a login on this API's devices
    read: Callable[[Fetch, str], Recorder]


DIALECTS = (
    Dialect("Hikvision ISAPI", "/ISAPI/System/deviceInfo", _read_hikvision),
    Dialect("Dahua HTTP API", "/cgi-bin/magicBox.cgi?action=getDeviceType", _read_dahua),
)
# a device that has the path answers it (200) or asks for a login (401); others say 404 or serve a page
SPEAKS = {200, 401}


def _get(fetch: Fetch, path: str) -> str:
    status, body = fetch(path)
    if status == 401:
        raise NvrError("the recorder refused the login — check the username and password "
                       "(after a few wrong tries it may lock the account for a while)")
    if status != 200:
        raise NvrError(f"the recorder answered {path} with HTTP {status}")
    return body


def detect(fetch: Fetch) -> Dialect | None:
    """The API the device speaks, asked without a login."""
    for dialect in DIALECTS:
        status, _ = fetch(dialect.probe)
        if status in SPEAKS:
            return dialect
    return None


def http_fetch(host: str, port: int = 80, username: str | None = None, password: str | None = None) -> Fetch:
    session = requests.Session()
    if username:
        session.auth = HTTPDigestAuth(username, password or "")
    base = f"http://{host}:{port}"

    def fetch(path: str) -> tuple[int, str]:
        try:
            response = session.get(base + path, timeout=REQUEST_TIMEOUT, allow_redirects=False)
        except requests.RequestException as exc:
            raise NvrError(f"could not reach {host}:{port} ({type(exc).__name__})") from exc
        return response.status_code, response.text

    return fetch


def read_recorder(host: str, username: str, password: str, port: int = 80, *,
                  connect: Callable[..., Fetch] = http_fetch) -> Recorder:
    dialect = detect(connect(host, port))
    if dialect is None:
        raise NvrError(f"{host} answers neither the Hikvision nor the Dahua API — "
                       "it may not be a recorder, or it speaks only ONVIF (not supported yet)")
    return dialect.read(connect(host, port, username, password), host)
