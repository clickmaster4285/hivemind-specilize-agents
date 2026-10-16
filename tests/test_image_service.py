"""SDXL image service (own process) + API-side client.

SDXL itself is faked — no GPU, no 7 GB model. One test starts a real service process.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import os
import signal
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

from src import image_service
from src import sdxl_pipeline as sdxl
from src.config import Config
from src.image_client import ImageCancelled, ImageServiceClient, ImageServiceError

TOKEN = "test-token-123"
AUTH = {"X-Image-Token": TOKEN}


def _png_b64(color=(10, 20, 30)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(autouse=True)
def fake_sdxl(monkeypatch):
    """Replace the real model with tiny fakes; record what each mode received."""
    calls: list[dict] = []

    def fake_generate(prompt, *, width=None, height=None, should_continue=None, **kw):
        calls.append({"mode": "txt2img", "prompt": prompt, "width": width, "steps": kw.get("steps")})
        if prompt == "slow":
            while should_continue():
                time.sleep(0.05)
        if prompt == "not configured":
            raise sdxl.ImageGenNotConfigured("Image generation is not configured. Set MODEL_IMAGE_PATH")
        return Image.new("RGB", (width or 64, height or 64), (200, 100, 50))

    def fake_img2img(prompt, init, *, strength=None, **kw):
        calls.append({"mode": "img2img", "size": init.size, "strength": strength})
        return init

    def fake_inpaint(prompt, init, mask, *, strength=None, **kw):
        calls.append({"mode": "inpaint", "mask_mode": mask.mode, "strength": strength})
        return init

    monkeypatch.setattr(sdxl, "generate", fake_generate)
    monkeypatch.setattr(sdxl, "generate_from_image", fake_img2img)
    monkeypatch.setattr(sdxl, "generate_inpaint", fake_inpaint)
    monkeypatch.setenv("IMAGE_SERVICE_TOKEN", TOKEN)
    return calls


@pytest.fixture
def api():
    return TestClient(image_service.app)


def _req(**overrides):
    return {"request_id": "r1", "mode": "txt2img", "prompt": "a red fox", **overrides}


# ── service ─────────────────────────────────────────────────────────────

def test_token_is_required(api):
    assert api.get("/health").status_code == 401
    assert api.get("/health", headers={"X-Image-Token": "wrong"}).status_code == 401
    assert api.get("/health", headers=AUTH).json()["ok"] is True


def test_txt2img_returns_png(api, fake_sdxl):
    resp = api.post("/generate", json=_req(width=512, height=512, steps=12), headers=AUTH)
    assert resp.status_code == 200
    image = Image.open(io.BytesIO(base64.b64decode(resp.json()["image_b64"])))
    assert image.size == (512, 512)
    assert fake_sdxl[-1] == {"mode": "txt2img", "prompt": "a red fox", "width": 512, "steps": 12}


def test_img2img_and_inpaint(api, fake_sdxl):
    ok = api.post("/generate", json=_req(mode="img2img", init_image_b64=_png_b64(), strength=0.4), headers=AUTH)
    assert ok.status_code == 200 and fake_sdxl[-1] == {"mode": "img2img", "size": (64, 64), "strength": 0.4}
    ok = api.post("/generate", json=_req(mode="inpaint", init_image_b64=_png_b64(), mask_b64=_png_b64((255,) * 3)),
                  headers=AUTH)
    assert ok.status_code == 200 and fake_sdxl[-1]["mask_mode"] == "L"


@pytest.mark.parametrize(
    "body, message",
    [
        (_req(mode="img2img"), "needs init_image_b64"),
        (_req(mode="inpaint", init_image_b64=_png_b64()), "needs mask_b64"),
        (_req(mode="img2img", init_image_b64="bm90IGFuIGltYWdl"), "not a valid image"),
        (_req(prompt="not configured"), "not configured"),
    ],
)
def test_bad_requests_are_explained(api, body, message):
    resp = api.post("/generate", json=body, headers=AUTH)
    assert resp.status_code == 400 and message in resp.json()["detail"]


def test_a_running_generation_can_be_cancelled(api):
    result: dict = {}
    worker = threading.Thread(
        target=lambda: result.update(api.post("/generate", json=_req(prompt="slow", request_id="slow-1"),
                                              headers=AUTH).json())
    )
    worker.start()
    deadline = time.monotonic() + 5
    while not api.get("/health", headers=AUTH).json()["busy"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert api.post("/cancel/slow-1", headers=AUTH).json() == {"cancelled": True}
    worker.join(5)
    assert result == {"cancelled": True}
    assert api.get("/health", headers=AUTH).json()["busy"] is False


@pytest.mark.parametrize(
    "idle, busy, minutes, expected",
    [(601, False, 10, True), (599, False, 10, False), (10_000, True, 10, False), (10_000, False, 0, False)],
)
def test_idle_exit_rule(idle, busy, minutes, expected):
    assert image_service.idle_too_long(1_000_000.0, 1_000_000.0 - idle, busy, minutes) is expected


# ── client against a live server ──────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_service():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(image_service.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield port
    server.should_exit = True
    thread.join(5)


def _client(port: int, **overrides) -> ImageServiceClient:
    settings = {"image_service_port": port, "image_service_token": TOKEN, "image_service_autostart": False}
    settings.update(overrides)
    return ImageServiceClient(dataclasses.replace(Config.from_env(), **settings))


def test_client_generates_and_reports_status(live_service):
    client = _client(live_service)
    assert client.status()["state"] in {"cold", "ready"}
    image = client.generate("txt2img", "a red fox", width=320, height=256)
    assert image.size == (320, 256)
    edited = client.generate("img2img", "bluer", init_image=Image.new("RGB", (100, 80)), strength=0.5)
    assert edited.size == (100, 80)


def test_client_cancel(live_service):
    started = time.monotonic()
    with pytest.raises(ImageCancelled):
        _client(live_service).generate("txt2img", "slow", should_continue=lambda: time.monotonic() - started < 0.5)


def test_client_errors_are_readable(live_service):
    with pytest.raises(ImageServiceError, match="not configured"):
        _client(live_service).generate("txt2img", "not configured")
    with pytest.raises(ImageServiceError, match="token"):
        _client(live_service, image_service_token="wrong").health()


def test_stopped_service_and_no_autostart():
    client = _client(_free_port())
    assert client.status() == {"state": "stopped", "loaded": False}
    with pytest.raises(ImageServiceError, match="not running"):
        client.ensure_running()


# ── real process ──────────────────────────────────────────────────────

def test_client_starts_a_real_service_process():
    port = _free_port()
    client = _client(port, image_service_autostart=True)
    client.ensure_running()
    info = client.health()
    try:
        assert info and info["ok"] and info["pid"] != os.getpid()   # a separate process
        assert info["pipeline"]["loaded"] is False                   # nothing loaded until a job arrives
    finally:
        os.kill(info["pid"], signal.SIGTERM)                         # stop the test service
