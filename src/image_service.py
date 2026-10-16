"""SDXL image service — its own process, so the API never holds the ~7 GB model.

Run:  python -m src.image_service                  (stays up; unloads the model when idle)
      python -m src.image_service --exit-when-idle (how the API starts it: the process exits
                                                    after IMAGE_IDLE_UNLOAD_MINUTES without jobs,
                                                    which returns ALL of its memory to Windows)

Listens on 127.0.0.1 only. Every request needs the X-Image-Token header (IMAGE_SERVICE_TOKEN).
One generation at a time; a running generation can be cancelled (stops at the next diffusion step).
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hmac
import io
import logging
import os
import secrets
import threading
import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from src import sdxl_pipeline as sdxl
from src.config import Config
from src.image_models import resolve_checkpoint

log = logging.getLogger("image_service")

MAX_IMAGE_B64_CHARS = 30_000_000   # ~22 MB of image data per request
IDLE_CHECK_SECONDS = 30

_config = Config.from_env()
_gen_lock = threading.Lock()                    # one generation at a time
_cancel: dict[str, threading.Event] = {}
_state = {"busy": False, "last_activity": time.monotonic()}

app = FastAPI(title="MulteAgent image service", docs_url=None, redoc_url=None)


def _check_token(x_image_token: str = Header(default="")) -> None:
    expected = os.environ.get("IMAGE_SERVICE_TOKEN") or _config.image_service_token
    if not expected or not hmac.compare_digest(x_image_token, expected):
        raise HTTPException(status_code=401, detail="missing or wrong X-Image-Token")


class GenerateRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    mode: Literal["txt2img", "img2img", "inpaint"]
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=150)
    guidance: float | None = Field(default=None, ge=0, le=30)
    strength: float | None = Field(default=None, ge=0.05, le=1.0)
    init_image_b64: str | None = Field(default=None, max_length=MAX_IMAGE_B64_CHARS)
    mask_b64: str | None = Field(default=None, max_length=MAX_IMAGE_B64_CHARS)
    seed: int | None = Field(default=None, ge=0, lt=2**32)            # None = random
    checkpoint: str | None = Field(default=None, max_length=200)      # file name in models/checkpoints


def _decode_image(b64: str, what: str) -> Any:
    from PIL import Image

    try:
        return Image.open(io.BytesIO(base64.b64decode(b64, validate=True))).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"{what} is not a valid image") from exc


def _touch() -> None:
    _state["last_activity"] = time.monotonic()


@app.get("/health", dependencies=[Depends(_check_token)])
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "pid": os.getpid(),
        "busy": _state["busy"],
        "idle_seconds": 0 if _state["busy"] else round(time.monotonic() - _state["last_activity"]),
        "pipeline": sdxl.pipeline_status(),
    }


@app.post("/warm", dependencies=[Depends(_check_token)])
def warm() -> dict[str, str]:
    """Load the model in the background (used when IMAGE_WARM_ON_START=true)."""
    _touch()
    sdxl.warm_pipeline_async(dataclasses.replace(_config, image_warm_on_start=True))
    return {"status": "warming"}


@app.post("/cancel/{request_id}", dependencies=[Depends(_check_token)])
def cancel(request_id: str) -> dict[str, bool]:
    event = _cancel.get(request_id)
    if event is not None:
        event.set()
    return {"cancelled": event is not None}


@app.post("/generate", dependencies=[Depends(_check_token)])
def generate(req: GenerateRequest) -> dict[str, Any]:
    stop = threading.Event()
    _cancel[req.request_id] = stop
    started = time.perf_counter()
    try:
        with _gen_lock:
            _state["busy"] = True
            _touch()
            if stop.is_set():
                return {"cancelled": True}
            keep_going = lambda: not stop.is_set()  # noqa: E731
            try:
                checkpoint = resolve_checkpoint(req.checkpoint, _config.model_image_path)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if req.checkpoint and not (checkpoint and checkpoint.is_file()):
                raise HTTPException(status_code=400, detail=f"checkpoint not found: models/checkpoints/{req.checkpoint}")
            seed = req.seed if req.seed is not None else secrets.randbelow(2**32)
            common = dict(
                config=_config, negative_prompt=req.negative_prompt, steps=req.steps,
                guidance=req.guidance, should_continue=keep_going, seed=seed, checkpoint=checkpoint,
            )
            if req.mode == "txt2img":
                image = sdxl.generate(req.prompt, width=req.width, height=req.height, **common)
            else:
                if not req.init_image_b64:
                    raise HTTPException(status_code=400, detail=f"{req.mode} needs init_image_b64")
                init = _decode_image(req.init_image_b64, "init image")
                if req.mode == "img2img":
                    image = sdxl.generate_from_image(req.prompt, init, strength=req.strength, **common)
                else:
                    if not req.mask_b64:
                        raise HTTPException(status_code=400, detail="inpaint needs mask_b64")
                    mask = _decode_image(req.mask_b64, "mask").convert("L")
                    image = sdxl.generate_inpaint(req.prompt, init, mask, strength=req.strength, **common)
        if stop.is_set():
            return {"cancelled": True}
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return {
            "image_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
            "seconds": round(time.perf_counter() - started, 1),
            "seed": seed,
            "checkpoint": checkpoint.name if checkpoint else None,
        }
    except sdxl.ImageGenNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sdxl.ImageGenCudaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — report the failure to the API instead of a bare 500
        log.exception("generation failed")
        raise HTTPException(status_code=500, detail=f"image generation failed: {exc}") from exc
    finally:
        _state["busy"] = False
        _touch()
        _cancel.pop(req.request_id, None)


def idle_too_long(now: float, last_activity: float, busy: bool, idle_minutes: int) -> bool:
    """Should an --exit-when-idle service stop now?"""
    return idle_minutes > 0 and not busy and now - last_activity >= idle_minutes * 60


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=_config.image_service_port)
    parser.add_argument("--exit-when-idle", action="store_true",
                        help="stop the process after IMAGE_IDLE_UNLOAD_MINUTES without jobs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not (os.environ.get("IMAGE_SERVICE_TOKEN") or _config.image_service_token):
        log.error("IMAGE_SERVICE_TOKEN is not set — refusing to start")
        return 1

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    if args.exit_when_idle:
        minutes = _config.image_idle_unload_minutes

        def watch() -> None:
            while not server.should_exit:
                time.sleep(IDLE_CHECK_SECONDS)
                if idle_too_long(time.monotonic(), _state["last_activity"], _state["busy"], minutes):
                    log.info("idle for %s minutes — stopping to free memory", minutes)
                    server.should_exit = True

        threading.Thread(target=watch, name="idle-exit", daemon=True).start()
    log.info("image service on 127.0.0.1:%s (pid %s, exit when idle: %s)", args.port, os.getpid(), args.exit_when_idle)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
