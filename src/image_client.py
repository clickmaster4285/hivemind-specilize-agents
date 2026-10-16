"""API-side client for the SDXL image service (src/image_service.py).

The API never imports torch/diffusers. It talks to the image service over HTTP — like Ollama —
and starts it on the first image request (IMAGE_SERVICE_AUTOSTART). The service stops itself
after IMAGE_IDLE_UNLOAD_MINUTES without jobs, so its memory is fully returned to Windows.
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import requests

from src.config import ROOT, Config

START_TIMEOUT_SECONDS = 90
GENERATE_TIMEOUT_SECONDS = 1800        # a cold start + slow profile on a Pascal GPU can take minutes
CANCEL_POLL_SECONDS = 1.0
LOG_FILE = ROOT / "logs" / "image_service.log"

_spawn_lock = threading.Lock()


class ImageServiceError(RuntimeError):
    """The image service is unreachable, misconfigured, or reported a failure."""


class ImageCancelled(RuntimeError):
    """The job was cancelled while the image was being generated."""


def _b64_png(image: Any) -> str:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class ImageServiceClient:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self.base_url = f"http://127.0.0.1:{self.config.image_service_port}"
        self.headers = {"X-Image-Token": self.config.image_service_token}

    # ── status ──────────────────────────────────────────────────────
    def health(self) -> dict[str, Any] | None:
        """Service health, or None when it is not running."""
        try:
            resp = requests.get(f"{self.base_url}/health", headers=self.headers, timeout=2)
        except requests.RequestException:
            return None
        if resp.status_code == 401:
            raise ImageServiceError("image service rejected the token — check IMAGE_SERVICE_TOKEN in .env")
        return resp.json() if resp.ok else None

    def status(self) -> dict[str, Any]:
        """Small summary for /api/health: stopped | ready | busy | warming | error."""
        try:
            info = self.health()
        except ImageServiceError as exc:
            return {"state": "error", "error": str(exc)}
        if info is None:
            return {"state": "stopped", "loaded": False}
        pipe = info.get("pipeline") or {}
        state = "busy" if info.get("busy") else (pipe.get("state") or "ready")
        return {"state": state, "loaded": bool(pipe.get("loaded")), "pid": info.get("pid"),
                "idle_seconds": info.get("idle_seconds"), "error": pipe.get("error")}

    # ── lifecycle ───────────────────────────────────────────────────
    def ensure_running(self) -> None:
        if self.health() is not None:
            return
        if not self.config.image_service_autostart:
            raise ImageServiceError(
                "image service is not running — start it with: python -m src.image_service"
            )
        if not self.config.image_service_token:
            raise ImageServiceError("IMAGE_SERVICE_TOKEN is not set in .env")
        with _spawn_lock:
            if self.health() is not None:  # another job started it meanwhile
                return
            self._spawn()
            deadline = time.monotonic() + START_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if self.health() is not None:
                    return
                time.sleep(1)
        raise ImageServiceError(f"image service did not start within {START_TIMEOUT_SECONDS}s — see {LOG_FILE}")

    def _spawn(self) -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "ab")  # noqa: SIM115 — handed to the child process
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        env = {**os.environ, "IMAGE_SERVICE_TOKEN": self.config.image_service_token, "PYTHONUNBUFFERED": "1"}
        subprocess.Popen(
            [sys.executable, "-m", "src.image_service", "--exit-when-idle",
             "--port", str(self.config.image_service_port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
        log.close()  # the child has its own handle

    def warm(self) -> None:
        """Start the service and load the model now (IMAGE_WARM_ON_START)."""
        self.ensure_running()
        requests.post(f"{self.base_url}/warm", headers=self.headers, timeout=10)

    # ── generation ──────────────────────────────────────────────────
    def generate(
        self,
        mode: str,
        prompt: str,
        *,
        init_image: Any = None,
        mask: Any = None,
        negative_prompt: str | None = None,
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        guidance: float | None = None,
        strength: float | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> Any:
        """Generate one image (PIL.Image). Raises ImageCancelled / ImageServiceError."""
        from PIL import Image

        self.ensure_running()
        request_id = uuid.uuid4().hex
        payload = {
            "request_id": request_id, "mode": mode, "prompt": prompt, "negative_prompt": negative_prompt,
            "width": width, "height": height, "steps": steps, "guidance": guidance, "strength": strength,
            "init_image_b64": _b64_png(init_image) if init_image is not None else None,
            "mask_b64": _b64_png(mask) if mask is not None else None,
        }

        done = threading.Event()
        if should_continue is not None:
            def watch_cancel() -> None:
                while not done.wait(CANCEL_POLL_SECONDS):
                    if not should_continue():
                        try:
                            requests.post(f"{self.base_url}/cancel/{request_id}", headers=self.headers, timeout=5)
                        except requests.RequestException:
                            pass
                        return

            threading.Thread(target=watch_cancel, name=f"image-cancel-{request_id[:8]}", daemon=True).start()
        try:
            resp = requests.post(f"{self.base_url}/generate", json=payload, headers=self.headers,
                                 timeout=GENERATE_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise ImageServiceError(f"image service request failed: {exc}") from exc
        finally:
            done.set()

        if not resp.ok:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text[:300]
            raise ImageServiceError(str(detail or f"HTTP {resp.status_code}"))
        data = resp.json()
        if data.get("cancelled"):
            raise ImageCancelled("Image generation cancelled.")
        return Image.open(io.BytesIO(base64.b64decode(data["image_b64"]))).convert("RGB")
