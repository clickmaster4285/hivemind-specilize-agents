"""Text-to-image and image-to-image jobs, rendered by the separate SDXL image service.

The API process never loads SDXL: src/image_client.py starts the image service on demand and
sends it the job over HTTP (see src/image_service.py).
"""

from __future__ import annotations

import base64
import io
import math
import time
from typing import Any, Callable

from src.attachments import load_mask_image, load_pil_images, save_images
from src.config import FAST_CHECKPOINT_PROFILES, Config
from src.image_client import ImageCancelled, ImageServiceClient, ImageServiceError
from src.image_models import CHECKPOINT_DIR, parse_image_flags, preset_for, size_for_aspect
from src.image_prompt import TEXT_REQUEST_RE, needs_rewrite, polish_prompt, rewrite_prompt, should_polish
from src.llm_lock import image_slot
from src.ollama_mgmt import unload_ollama_models

ProgressCallback = Callable[[dict[str, Any]], None]

NO_CHECKPOINT = """This request needs **local SDXL image generation**.

Set `MODEL_IMAGE_PATH` in `.env` to your SDXL checkpoint (`.safetensors`), for example:

```
MODEL_IMAGE_PATH=D:/multeagent/models/checkpoints/sd_xl_base_1.0.safetensors
```

On Quadro P4000 / Pascal GPUs install the CUDA 11.8 PyTorch stack:

```
pip install -r requirements-image-cu118.txt
```

Then restart the API and try again. Vision models (`llava`) can only **look at** images — they cannot draw new ones."""


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def _png_text(prompt: str, negative: str, seed: Any, model: str) -> Any:
    """Store how the image was made inside the PNG, so it can be repeated later."""
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("prompt", prompt)
    info.add_text("negative_prompt", negative)
    info.add_text("seed", str(seed))
    info.add_text("model", model)
    return info


def looks_like_image_gen_model(name: str) -> bool:
    lower = (name or "").lower()
    return any(
        token in lower
        for token in (
            "z-image",
            "zimage",
            "flux",
            "sdxl",
            "stable-diffusion",
            "stable_diffusion",
            "sd3",
            "imagen",
            "dall-e",
            "dalle",
            "gpt-image",
            "sdxl-local",
        )
    )


def run_image_gen(
    query: str,
    *,
    job_id: str,
    config: Config | None = None,
    model_plan: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
    attachments: list[dict[str, Any]] | None = None,
    image_mode: str | None = None,
    should_continue: Callable[[], bool] | None = None,
    checkpoint: str | None = None,
) -> tuple[str, list[dict[str, str]], list[str], float]:
    """Return (message, saved attachment dicts, errors, seconds).

    `checkpoint` = a file name in models/checkpoints/ for this job (default: MODEL_IMAGE_PATH).
    """
    config = config or Config.from_env()
    model_plan = model_plan or {}
    label = (
        (model_plan.get("image") or config.model_image or "sdxl-local")
    ).strip() or "sdxl-local"
    started = time.perf_counter()
    errors: list[str] = []
    saved: list[dict[str, str]] = []
    attachments = attachments or []

    mode = (image_mode or "").strip().lower()
    init_images = load_pil_images(config, attachments) if attachments else []
    mask = load_mask_image(config, attachments) if attachments else None
    use_inpaint = mode == "inpaint" or (mode == "img2img" and mask is not None)
    use_img2img = (not use_inpaint) and (
        mode == "img2img" or (bool(init_images) and mode not in {"txt2img", "inpaint"})
    )
    if use_inpaint:
        role = "Image edit (inpaint)"
    elif use_img2img:
        role = "Image edit (img2img)"
    else:
        role = "Image generation"

    _emit(on_progress, {"type": "session_start", "mode": "chat", "query": query})
    _emit(
        on_progress,
        {
            "type": "agent_start",
            "agent_id": "assistant",
            "name": "Assistant",
            "role": role,
            "stage": "chat",
            "accent": "#c4a35a",
            "model": label,
            "job_id": job_id,
        },
    )

    flags = parse_image_flags(query)
    raw_query = flags.prompt
    needs_edit_polish = use_img2img or use_inpaint
    if not checkpoint and config.image_profile in FAST_CHECKPOINT_PROFILES and config.image_fast_checkpoint:
        checkpoint = config.image_fast_checkpoint
    path = CHECKPOINT_DIR / checkpoint if checkpoint else config.model_image_path
    preset = preset_for(path)
    ckpt_name = path.name if path is not None and path.parent.resolve() == CHECKPOINT_DIR.resolve() else None
    if path is not None and path.is_file():
        # load the image model (RAM only) while the chat model rewrites the prompt
        ImageServiceClient(config).warm_in_background(ckpt_name)

    aspect = flags.aspect
    rewritten = None
    if not needs_edit_polish and config.image_prompt_rewrite and needs_rewrite(raw_query):
        _emit(on_progress, {"type": "status", "message": "Writing a detailed image prompt…"})
        rewritten = rewrite_prompt(raw_query, config)
    if rewritten is not None:
        prompt = rewritten.prompt
        aspect = aspect or rewritten.aspect
    elif config.image_prompt_polish and should_polish(raw_query, img2img=needs_edit_polish):
        prompt = polish_prompt(raw_query, img2img=needs_edit_polish)
    else:
        prompt = raw_query

    base_w, base_h = (preset.size, preset.size) if preset.size else (config.image_width, config.image_height)
    width, height = size_for_aspect(aspect, base_w, base_h)
    steps = preset.steps or config.image_steps
    if preset.steps and (use_img2img or use_inpaint):
        # an edit runs only steps × strength of them: keep a distilled model at its real step count
        strength = max(config.image_strength, 0.75) if use_inpaint else config.image_strength
        steps = min(50, math.ceil(preset.steps / max(strength, 0.05)))
    guidance = config.image_guidance
    if preset.guidance is not None:
        guidance = preset.guidance
    elif preset.max_guidance is not None:
        guidance = min(guidance, preset.max_guidance)
    negative = preset.negative_prompt or config.image_negative_prompt or ""
    if flags.negative:
        negative = f"{negative}, {flags.negative}" if negative else flags.negative

    output = NO_CHECKPOINT
    if path is not None and path.is_file():
        try:
            if config.image_unload_ollama:
                unloaded = unload_ollama_models(config)
                if unloaded:
                    _emit(
                        on_progress,
                        {
                            "type": "status",
                            "message": f"Freed Ollama VRAM: {', '.join(unloaded)}",
                        },
                    )
            client = ImageServiceClient(config)
            if client.health() is None:
                _emit(on_progress, {"type": "status", "message": "Starting the image service (first image takes longer)…"})
            # This job's settings (profile / UI presets) are sent explicitly: the service has its own defaults.
            settings = dict(
                negative_prompt=negative or None,
                steps=steps,
                guidance=guidance,
                should_continue=should_continue,
                seed=flags.seed,
                checkpoint=ckpt_name,
            )
            with image_slot(config):
                if use_inpaint:
                    if not init_images:
                        raise ValueError("Inpaint needs a source image attachment.")
                    if mask is None:
                        raise ValueError(
                            "Inpaint needs a mask image (filename containing 'mask'; "
                            "white = areas to repaint). Or use Edit (img2img) without a mask."
                        )
                    image = client.generate(
                        "inpaint", prompt, init_image=init_images[0], mask=mask,
                        strength=max(config.image_strength, 0.75), **settings,
                    )
                elif use_img2img:
                    if not init_images:
                        raise ValueError(
                            "img2img requested but no usable attached image was found."
                        )
                    image = client.generate(
                        "img2img", prompt, init_image=init_images[0], strength=config.image_strength, **settings,
                    )
                else:
                    image = client.generate("txt2img", prompt, width=width, height=height,
                                            hires=config.image_hires or None,
                                            hires_checkpoint=config.image_fast_checkpoint or None, **settings)
            if should_continue is not None and not should_continue():
                raise ImageCancelled("Image generation cancelled.")
            seed = image.info.get("seed")
            buf = io.BytesIO()
            image.save(buf, format="PNG", pnginfo=_png_text(prompt, negative, seed, preset.label))
            png = buf.getvalue()
            stored = save_images(
                config,
                job_id,
                [
                    {
                        "filename": "generated.png",
                        "mime": "image/png",
                        "data": base64.b64encode(png).decode("ascii"),
                    }
                ],
            )
            for row in stored:
                row["kind"] = "generated"
            saved = stored
            if use_inpaint:
                output = (
                    f"Inpainted with **{preset.label}** (profile {config.image_profile}): "
                    f"{steps} steps, strength {config.image_strength:.2f}, seed {seed}."
                )
            elif use_img2img:
                output = (
                    f"Edited with **{preset.label}** (profile {config.image_profile}): "
                    f"{steps} steps, strength {config.image_strength:.2f}, seed {seed}."
                )
            else:
                output = (
                    f"Generated with **{preset.label}** (profile {config.image_profile}) at "
                    f"{image.width}×{image.height}, {steps} steps, guidance {guidance:g}, seed {seed}."
                )
                if config.image_hires and image.width > width:
                    output += f" Hires fix: drawn at {width}×{height}, then refined at {config.image_hires:g}×."
            if prompt != raw_query:
                output += f"\n\nPrompt used: `{prompt}`"
            if seed is not None:
                output += (
                    f"\n\nSame image again: add `--seed {seed}` to your request. "
                    "Without it every request gives a new variation."
                )
            if TEXT_REQUEST_RE.search(raw_query):
                output += (
                    "\n\nNote: the image model (RealVisXL, an SDXL model) cannot write readable text or real "
                    "logos, or draw exact sizes and angles — any lettering will be fake. Add text and logos "
                    "afterwards in an editor; for labelled layouts ask for a diagram instead."
                )
            if flags.ignored:
                output += f"\n\nIgnored (Midjourney-only): `{' '.join(flags.ignored)}`"
        except ImageCancelled as exc:
            errors.append(str(exc))
            output = str(exc)
        except ImageServiceError as exc:
            # not configured, CUDA problem, service failed to start, … — message comes from the service
            errors.append(str(exc))
            output = f"Image generation failed: {exc}"
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            output = (
                f"Image generation failed: {exc}\n\n"
                "On 8GB GPUs keep IMAGE_WIDTH/IMAGE_HEIGHT at 768 (not 1024). "
                "Unload large Ollama models before generating. "
                "Pascal GPUs need: pip install -r requirements-image-cu118.txt"
            )
    else:
        errors.append("MODEL_IMAGE_PATH not set or file missing")

    elapsed = time.perf_counter() - started
    _emit(
        on_progress,
        {
            "type": "agent_done",
            "agent_id": "assistant",
            "name": "Assistant",
            "role": role if saved else "Direct reply",
            "stage": "chat",
            "accent": "#c4a35a",
            "elapsed_seconds": elapsed,
            "output": output,
            "job_id": job_id,
            "images": [
                {"filename": a["filename"], "mime": a.get("mime") or "image/png"}
                for a in saved
            ],
        },
    )
    _emit(
        on_progress,
        {
            "type": "session_done",
            "total_seconds": elapsed,
            "errors": errors,
            "mode": "chat",
        },
    )
    return output, saved, errors, elapsed
