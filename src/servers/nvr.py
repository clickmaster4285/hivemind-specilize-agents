"""nvr_cameras — the cameras an NVR records (BACKLOG L17, ISSUES_LOG O17).

Read-only: GET requests to the recorder's own API only — never its web pages, never a setting changed.
Which API a device speaks is found by asking it: the vendor path that answers with a login request is the
one. Each read makes one login attempt (recorders lock an account after a few wrong passwords — no retries).
Replies are untrusted text from the network: XML is parsed with defusedxml.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

import requests
from defusedxml import DefusedXmlException, ElementTree
from requests.auth import HTTPDigestAuth

from src.core.config import Config

logger = logging.getLogger("multeagent.nvr")

# per request: a recorder on the LAN answers in well under a second; a dead or wrong address should fail fast
REQUEST_TIMEOUT = 10

Fetch = Callable[[str], tuple[int, str]]       # path → (HTTP status, body)
HIKVISION_API = "Hikvision ISAPI"              # the one API with a full report too (src/servers/nvr_report.py)


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
    return Recorder(host=host, api=HIKVISION_API, cameras=cameras, **info)


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
    Dialect(HIKVISION_API, "/ISAPI/System/deviceInfo", _read_hikvision),
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


# ── as a chat job ──────────────────────────────────────────────────────

COLUMNS = ("Channel", "Camera", "IP address", "Model", "Protocol", "Status")


def _status(online: bool | None) -> str:
    return {True: "online", False: "offline"}.get(online, "unknown")


def rows(rec: Recorder) -> list[list[str]]:
    return [list(COLUMNS)] + [[str(c.channel), c.name, c.ip, c.model, c.protocol, _status(c.online)]
                              for c in rec.cameras]


def summary(rec: Recorder) -> str:
    about = " · ".join(p for p in (rec.name, rec.model, f"firmware {rec.firmware}" if rec.firmware else "") if p)
    counts = {s: sum(_status(c.online) == s for c in rec.cameras) for s in ("online", "offline", "unknown")}
    found = f"**{len(rec.cameras)} camera{'s' if len(rec.cameras) != 1 else ''}**"
    states = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    return f"Recorder **{rec.host}** ({about or rec.api}, read over the {rec.api}): {found}" + \
        (f" — {states}." if states else ".")


def markdown(rec: Recorder) -> str:
    if not rec.cameras:
        return summary(rec) + " No camera channels are set up on it."
    table = rows(rec)
    lines = ["| " + " | ".join(table[0]) + " |", "|" + "---|" * len(table[0])]
    lines += ["| " + " | ".join(cell.replace("|", "/") for cell in row) + " |" for row in table[1:]]
    return summary(rec) + "\n\n" + "\n".join(lines)


def xlsx_bytes(rec: Recorder) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Cameras"
    for row in rows(rec):
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    info = wb.create_sheet("Recorder")
    for key, value in (("Address", rec.host), ("Name", rec.name), ("Model", rec.model), ("Serial", rec.serial),
                       ("Firmware", rec.firmware), ("Read over", rec.api)):
        info.append([key, value])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_nvr_cameras(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    nvr: str | None = None,
    user_id: int | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'list the cameras on the NVR' → the recorder's login from Settings → Devices → its camera table + .xlsx.
    No model: the table is the recorder's own answer."""
    from src.servers import server_vault
    from src.tools.artifacts import artifact_event, write_bytes

    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "NVR cameras", "stage": "chat",
             "accent": "#3b82c4", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "nvr_cameras"})
    try:
        recorders = server_vault.list_servers(user_id, "nvr") if user_id is not None else []
        named = nvr or next((r.host for r in recorders if r.host in query), None)
        login = server_vault.find(user_id, named, kind="nvr") if user_id is not None else None
        if login is None and not recorders:
            output = ("No recorder login for this session yet. Add the NVR in **Settings → Devices** (type: NVR, "
                      "its address, user and password) — it stays in memory for this session only, never in the chat.")
        elif login is None:
            names = ", ".join(f"{r.label} ({r.host})" for r in recorders)
            output = f"Which recorder? Added this session: {names}."
        else:
            emit({"type": "status", "agent_id": "assistant", "message": f"Reading the cameras on {login.host}…"})
            rec = read_recorder(login.host, login.username, login.password or "", login.port)
            name, sheet = f"nvr-cameras-{rec.host}.xlsx", xlsx_bytes(rec) if rec.cameras else None
            output = markdown(rec)
            if rec.api == HIKVISION_API:                  # the full report: the NVR, each camera's own details, disks
                from src.servers import nvr_report

                emit({"type": "status", "agent_id": "assistant",
                      "message": f"Reading the details of each of the {len(rec.cameras)} cameras…"})
                report = nvr_report.build(login.host, login.username, login.password or "", login.port)
                name, sheet = f"nvr-report-{rec.host}.xlsx", nvr_report.xlsx_bytes(report)
                output = nvr_report.markdown(report)
            if sheet is not None:
                meta = write_bytes(config, job_id, name, sheet, kind="xlsx")
                emit(artifact_event(meta, job_id=job_id))
    except (NvrError, server_vault.VaultError) as exc:
        output = f"The cameras could not be read: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("nvr read failed: %s", type(exc).__name__)
        output = f"The cameras could not be read ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
