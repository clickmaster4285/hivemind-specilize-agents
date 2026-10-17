"""Process-wide SDXL Diffusers pipelines (txt2img + img2img, Pascal/8GB-safe).

Memory: with model CPU offload the whole model lives in system RAM and parts move to the GPU
only while an image is generated. So the model is loaded on first use (IMAGE_WARM_ON_START=false
by default) and unloaded again after IMAGE_IDLE_UNLOAD_MINUTES without image jobs.
"""

from __future__ import annotations

import functools
import gc
import logging
import math
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.config import Config
from src.image_models import preset_for

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_txt2img: Any | None = None
_img2img: Any | None = None
_pipeline_path: str | None = None
_warm_state: str = "cold"  # cold | warming | ready | error
_warm_error: str | None = None
_in_use = 0                         # generations currently running
_last_used = 0.0                    # time.monotonic() of the last load / generation
_unloader: threading.Thread | None = None
IDLE_CHECK_SECONDS = 60


class ImageGenNotConfigured(RuntimeError):
    """Raised when MODEL_IMAGE_PATH is missing or unreadable."""


class ImageGenCudaError(RuntimeError):
    """Raised when the GPU build cannot run kernels (e.g. wrong CUDA wheel)."""


def pipeline_status() -> dict[str, Any]:
    loaded = _txt2img is not None and _img2img is not None
    return {
        "state": _warm_state,
        "error": _warm_error,
        "loaded": loaded,
        "path": _pipeline_path,
        "in_use": _in_use,
        "idle_seconds": round(time.monotonic() - _last_used) if loaded and not _in_use else 0,
    }


@contextmanager
def _using() -> Iterator[None]:
    """Mark the model busy for one generation so the idle unloader never frees it mid-image."""
    global _in_use, _last_used
    with _lock:
        _in_use += 1
    try:
        yield
    finally:
        with _lock:
            _in_use -= 1
            _last_used = time.monotonic()
        _release_vram()


def _release_vram() -> None:
    """Give cached GPU memory back after an image: the weights stay in RAM (CPU offload), and the
    chat model (Ollama) needs the room to load fully on the GPU (doc/ISSUES_LOG.md #27)."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — freeing memory must never raise
        pass


def _tracked(fn: Any) -> Any:
    """Decorator: the whole generation (incl. fetching the model) counts as one use."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _using():
            return fn(*args, **kwargs)

    return wrapper


def _free_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — freeing memory must never raise
        pass


def unload_if_idle(idle_seconds: float) -> bool:
    """Drop the loaded model when no image job ran for `idle_seconds`. Returns True if unloaded."""
    global _txt2img, _img2img, _pipeline_path, _warm_state
    with _lock:
        if _txt2img is None or _in_use or time.monotonic() - _last_used < idle_seconds:
            return False
        _txt2img = None
        _img2img = None
        _pipeline_path = None
        _warm_state = "cold"
    _free_memory()
    logger.info("SDXL unloaded after %.0f idle minutes — RAM released", idle_seconds / 60)
    return True


def _start_idle_unloader(config: Config) -> None:
    """One background thread per process, started the first time the model is loaded."""
    global _unloader
    minutes = config.image_idle_unload_minutes
    if minutes <= 0 or (_unloader is not None and _unloader.is_alive()):
        return

    def _loop() -> None:
        while True:
            time.sleep(IDLE_CHECK_SECONDS)
            try:
                unload_if_idle(minutes * 60)
            except Exception:  # noqa: BLE001 — never let the thread die
                logger.exception("SDXL idle unload failed")

    _unloader = threading.Thread(target=_loop, name="sdxl-idle-unload", daemon=True)
    _unloader.start()


def _check_cuda_kernels(config: Config) -> None:
    """Fail fast if torch cannot use the GPU (CPU-only wheel, or no kernels for Pascal)."""
    import torch

    if not torch.cuda.is_available():
        if config.image_allow_cpu:
            logger.warning("CUDA not available; SDXL will run on CPU (very slow, float32).")
            return
        raise ImageGenCudaError(
            f"PyTorch cannot use the GPU (installed: torch {torch.__version__}, CUDA build: "
            f"{torch.version.cuda or 'none — CPU-only'}). Images would take ~4 minutes on the CPU. "
            "Install the GPU build:  pip install -r requirements-image-cu118.txt  "
            "(or set IMAGE_ALLOW_CPU=true to accept CPU mode)."
        )
    try:
        x = torch.zeros(1, device="cuda", dtype=torch.float16)
        _ = x + 1
        del x
        torch.cuda.empty_cache()
    except Exception as exc:  # noqa: BLE001
        raise ImageGenCudaError(
            "CUDA kernels failed on this GPU. Quadro P4000 / Pascal (sm_61) needs "
            "PyTorch cu118, not cu124/cu130. Install with:\n"
            "  pip install -r requirements-image-cu118.txt\n"
            f"Original error: {exc}"
        ) from exc


def _enable_memory_savers(pipe: Any) -> None:
    import torch

    if torch.cuda.is_available():
        pipe.enable_model_cpu_offload()
    if hasattr(pipe, "enable_vae_slicing"):
        pipe.enable_vae_slicing()
    elif getattr(pipe, "vae", None) is not None and hasattr(pipe.vae, "enable_slicing"):
        pipe.vae.enable_slicing()


def _maybe_tiny_vae(pipe: Any, config: Config, dtype: Any) -> None:
    if not config.image_tiny_vae:
        return
    try:
        from diffusers import AutoencoderTiny

        logger.info("Swapping in TinyVAE (taesdxl) for faster decode")
        pipe.vae = AutoencoderTiny.from_pretrained(
            "madebyollin/taesdxl",
            torch_dtype=dtype,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("TinyVAE unavailable, keeping default VAE: %s", exc)


def _maybe_fixed_vae(pipe: Any, config: Config, dtype: Any) -> None:
    """Swap in a VAE that is safe in float16 (no float32 upcast at decode time)."""
    import torch

    if not config.image_vae or dtype != torch.float16:
        return
    try:
        from diffusers import AutoencoderKL

        pipe.vae = AutoencoderKL.from_pretrained(config.image_vae, torch_dtype=dtype)
        logger.info("Using VAE %s", config.image_vae)
    except Exception as exc:  # noqa: BLE001
        logger.warning("VAE %s unavailable, keeping the checkpoint's VAE: %s", config.image_vae, exc)


def _load_pipelines(resolved: str, config: Config) -> tuple[Any, Any]:
    import torch
    from diffusers import (
        DPMSolverMultistepScheduler,
        StableDiffusionXLImg2ImgPipeline,
        StableDiffusionXLPipeline,
    )

    _check_cuda_kernels(config)
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    logger.info("Loading SDXL from %s (dtype=%s)", resolved, dtype)
    txt = StableDiffusionXLPipeline.from_single_file(
        resolved,
        torch_dtype=dtype,
        use_safetensors=True,
    )
    # The sampler the model page recommends: DPM++ 2M Karras (RealVisXL, Juggernaut) or
    # DPM++ SDE Karras (Lightning). Both sharper than UniPC at the same steps.
    sde = preset_for(resolved).sampler == "dpmpp_sde_karras"
    txt.scheduler = DPMSolverMultistepScheduler.from_config(
        txt.scheduler.config, use_karras_sigmas=True,
        algorithm_type="sde-dpmsolver++" if sde else "dpmsolver++",
    )
    if getattr(txt, "watermark", None) is not None:
        txt.watermark = None
    if config.image_tiny_vae:
        _maybe_tiny_vae(txt, config, dtype)
    else:
        _maybe_fixed_vae(txt, config, dtype)
    # Share weights — do not load the 7GB checkpoint twice.
    img = StableDiffusionXLImg2ImgPipeline(**txt.components)
    img.scheduler = txt.scheduler
    if getattr(img, "watermark", None) is not None:
        img.watermark = None
    # Offload owns placement — never .to("cuda") before this.
    _enable_memory_savers(txt)
    logger.info("SDXL txt2img + img2img ready (profile=%s)", config.image_profile)
    return txt, img


def get_pipelines(config: Config | None = None, checkpoint: Path | None = None) -> tuple[Any, Any]:
    """Return (txt2img, img2img) singletons for this process (loads on first use).

    `checkpoint` overrides MODEL_IMAGE_PATH; asking for another checkpoint replaces the loaded one.
    """
    global _txt2img, _img2img, _pipeline_path, _warm_state, _warm_error, _last_used
    config = config or Config.from_env()
    path = checkpoint or config.model_image_path
    if path is None or not Path(path).is_file():
        raise ImageGenNotConfigured(
            "Image generation is not configured. Set MODEL_IMAGE_PATH to your "
            "SDXL `.safetensors` file (e.g. models/checkpoints/sd_xl_base_1.0.safetensors), "
            "then restart the API."
        )
    resolved = str(Path(path).resolve())
    with _lock:
        if _txt2img is not None and _img2img is not None and _pipeline_path == resolved:
            _warm_state = "ready"
            return _txt2img, _img2img
        if _txt2img is not None:
            logger.info("Reloading SDXL pipelines from %s", resolved)
            _txt2img = None
            _img2img = None
            _pipeline_path = None
            _free_memory()
        try:
            _txt2img, _img2img = _load_pipelines(resolved, config)
            _pipeline_path = resolved
            _warm_state = "ready"
            _warm_error = None
            _last_used = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            _warm_state = "error"
            _warm_error = str(exc)
            raise
    _start_idle_unloader(config)
    return _txt2img, _img2img


def get_pipeline(config: Config | None = None) -> Any:
    """Backward-compatible: return txt2img pipeline."""
    return get_pipelines(config)[0]


def warm_pipeline_async(config: Config | None = None, checkpoint: Path | None = None) -> None:
    """Background warm-load so the first chat image is not cold."""
    global _warm_state, _warm_error
    config = config or Config.from_env()
    if not config.image_warm_on_start:
        return
    target = checkpoint or config.model_image_path
    if target is None or not Path(target).is_file():
        logger.info("SDXL warm skipped: no checkpoint configured")
        return

    def _run() -> None:
        global _warm_state, _warm_error
        _warm_state = "warming"
        try:
            get_pipelines(config, checkpoint)
            logger.info("SDXL warm-load complete — image model ready")
        except Exception as exc:  # noqa: BLE001
            _warm_state = "error"
            _warm_error = str(exc)
            logger.warning("SDXL warm-load failed: %s", exc)

    threading.Thread(target=_run, name="sdxl-warm", daemon=True).start()


def _dims(config: Config, width: int | None, height: int | None) -> tuple[int, int]:
    w = width if width is not None else config.image_width
    h = height if height is not None else config.image_height
    return max(256, (int(w) // 8) * 8), max(256, (int(h) // 8) * 8)


def _generator(seed: int | None) -> Any:
    """Seeded noise so an image can be repeated exactly (CPU generator: same result on any device)."""
    import torch

    return None if seed is None else torch.Generator("cpu").manual_seed(int(seed))


def _step_callback(should_continue: Any | None):
    if should_continue is None:
        return None

    def _cb(pipe: Any, step_index: int, timestep: Any, callback_kwargs: dict) -> dict:
        if not should_continue():
            pipe._interrupt = True
        return callback_kwargs

    return _cb


@_tracked
def generate(
    prompt: str,
    *,
    config: Config | None = None,
    negative_prompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    should_continue: Any | None = None,
    seed: int | None = None,
    checkpoint: Path | None = None,
    hires: float | None = None,
    hires_checkpoint: Path | None = None,
) -> Any:
    """Run text-to-image; returns a PIL.Image.Image.

    hires > 1: "hires fix" — enlarge the image and redraw it lightly, which sharpens faces and
    small details (doc/ISSUES_LOG.md #28). `hires_checkpoint` does the redraw (a fast Lightning
    model: ~1 min instead of ~2.5 min); default = the same model.
    """
    config = config or Config.from_env()
    pipe, _ = get_pipelines(config, checkpoint)
    w, h = _dims(config, width, height)
    n_steps = steps if steps is not None else config.image_steps
    guide = guidance if guidance is not None else config.image_guidance
    neg = (
        negative_prompt
        if negative_prompt is not None
        else config.image_negative_prompt
    )
    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": neg or None,
        "width": w,
        "height": h,
        "num_inference_steps": n_steps,
        "guidance_scale": guide,
        "generator": _generator(seed),
    }
    cb = _step_callback(should_continue)
    if cb is not None:
        kwargs["callback_on_step_end"] = cb
    started = time.perf_counter()
    image = pipe(**kwargs).images[0]
    logger.info("txt2img %dx%d, %d steps: %.1fs", w, h, n_steps, time.perf_counter() - started)
    if hires and hires > 1 and not getattr(pipe, "_interrupt", False):
        started = time.perf_counter()
        image = _hires_fix(image, kwargs, scale=hires, steps=n_steps, guidance=guide,
                           checkpoint=hires_checkpoint or checkpoint, config=config)
        logger.info("hires fix to %dx%d: %.1fs", image.width, image.height, time.perf_counter() - started)
    return image


HIRES_STRENGTH = 0.35      # enough to add detail, too little to change the picture
HIRES_MAX_STEPS = 12       # real denoising steps at the large size (~8 s each on a P4000)


def _hires_fix(image: Any, kwargs: dict[str, Any], *, scale: float, steps: int, guidance: float,
               checkpoint: Path | None, config: Config) -> Any:
    from PIL import Image

    _, img2img = get_pipelines(config, checkpoint)
    preset = preset_for(checkpoint or config.model_image_path)
    if preset.steps:                       # distilled refiner: its own steps and guidance
        steps, guidance = preset.steps, preset.guidance if preset.guidance is not None else guidance
    w = max(64, round(image.width * scale / 64) * 64)
    h = max(64, round(image.height * scale / 64) * 64)
    big = image.resize((w, h), Image.Resampling.LANCZOS)
    real_steps = min(steps, HIRES_MAX_STEPS)
    args = {k: v for k, v in kwargs.items() if k not in {"width", "height", "num_inference_steps"}}
    args.update(
        image=big, strength=HIRES_STRENGTH, guidance_scale=guidance,
        num_inference_steps=math.ceil(real_steps / HIRES_STRENGTH),
        # same seed again, so the redraw is as repeatable as the first image
        generator=_generator(kwargs["generator"].initial_seed()) if kwargs.get("generator") else None,
    )
    vae = img2img.vae
    vae.enable_tiling()            # decoding ~2.3 MP at once would not fit in 8 GB
    try:
        return img2img(**args).images[0]
    finally:
        vae.disable_tiling()


@_tracked
def generate_from_image(
    prompt: str,
    init_image: Any,
    *,
    config: Config | None = None,
    negative_prompt: str | None = None,
    strength: float | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    should_continue: Any | None = None,
    seed: int | None = None,
    checkpoint: Path | None = None,
) -> Any:
    """Run image-to-image; returns a PIL.Image.Image."""
    from PIL import Image

    config = config or Config.from_env()
    _, pipe = get_pipelines(config, checkpoint)
    n_steps = steps if steps is not None else config.image_steps
    guide = guidance if guidance is not None else config.image_guidance
    strength_v = strength if strength is not None else config.image_strength
    strength_v = max(0.05, min(1.0, float(strength_v)))
    neg = (
        negative_prompt
        if negative_prompt is not None
        else config.image_negative_prompt
    )

    image = init_image
    if not isinstance(image, Image.Image):
        image = Image.open(image).convert("RGB")
    else:
        image = image.convert("RGB")

    target_w, target_h = _dims(config, None, None)
    image = _fit_image(image, target_w, target_h)

    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "image": image,
        "negative_prompt": neg or None,
        "strength": strength_v,
        "num_inference_steps": n_steps,
        "guidance_scale": guide,
        "generator": _generator(seed),
    }
    cb = _step_callback(should_continue)
    if cb is not None:
        kwargs["callback_on_step_end"] = cb
    result = pipe(**kwargs)
    return result.images[0]


@_tracked
def generate_inpaint(
    prompt: str,
    init_image: Any,
    mask_image: Any,
    *,
    config: Config | None = None,
    negative_prompt: str | None = None,
    strength: float | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    should_continue: Any | None = None,
    seed: int | None = None,
    checkpoint: Path | None = None,
) -> Any:
    """Inpaint masked regions; mask white = repaint, black = keep."""
    from PIL import Image
    from diffusers import StableDiffusionXLInpaintPipeline

    config = config or Config.from_env()
    txt, _ = get_pipelines(config, checkpoint)
    # Build inpaint pipe from shared components (no second 7GB load).
    pipe = StableDiffusionXLInpaintPipeline(**txt.components)
    pipe.scheduler = txt.scheduler

    n_steps = steps if steps is not None else config.image_steps
    guide = guidance if guidance is not None else config.image_guidance
    strength_v = strength if strength is not None else max(config.image_strength, 0.75)
    strength_v = max(0.05, min(1.0, float(strength_v)))
    neg = (
        negative_prompt
        if negative_prompt is not None
        else config.image_negative_prompt
    )

    image = init_image if isinstance(init_image, Image.Image) else Image.open(init_image)
    image = image.convert("RGB")
    mask = mask_image if isinstance(mask_image, Image.Image) else Image.open(mask_image)
    mask = mask.convert("L")
    target_w, target_h = _dims(config, None, None)
    image = _fit_image(image, target_w, target_h)
    mask = _fit_image(mask.convert("RGB"), target_w, target_h).convert("L")

    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "image": image,
        "mask_image": mask,
        "negative_prompt": neg or None,
        "strength": strength_v,
        "num_inference_steps": n_steps,
        "guidance_scale": guide,
        "width": target_w,
        "height": target_h,
        "generator": _generator(seed),
    }
    cb = _step_callback(should_continue)
    if cb is not None:
        kwargs["callback_on_step_end"] = cb
    result = pipe(**kwargs)
    return result.images[0]


def _fit_image(image: Any, width: int, height: int) -> Any:
    from PIL import Image

    src_w, src_h = image.size
    if src_w == width and src_h == height:
        return image
    scale = max(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - width) // 2)
    top = max(0, (new_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def reset_pipeline() -> None:
    """Drop cached pipelines (tests / model path change)."""
    global _txt2img, _img2img, _pipeline_path, _warm_state, _warm_error
    with _lock:
        _txt2img = None
        _img2img = None
        _pipeline_path = None
        _warm_state = "cold"
        _warm_error = None
    _free_memory()
