"""Validate and persist chat image attachments for vision models.

An upload is trusted for nothing it says about itself (BACKLOG L16 S3): its type comes from its own bytes, it must
be a picture Pillow can decode within MAX_PIXELS (a small file can unpack to gigabytes: a decompression bomb), and
what is stored is Pillow's re-encoding of the pixels — whatever else the file carried (a script after the image
data, EXIF with the phone's GPS position) is gone. A refused upload is written to the security log (S6).
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

from src.core.config import Config

ALLOWED_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGES = 4
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000          # 8,000 × 5,000: any real photo; far below what exhausts memory when decoded
JPEG_QUALITY = 92
# what Pillow finds in the bytes → what is stored; a GIF keeps its first frame as a PNG (vision models read one)
STORED_AS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/png", "MPO": "image/jpeg"}


class UploadRefused(ValueError):
    """The upload is not a picture we keep: the message says why (shown to the user, written to the security log)."""
SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def strip_data_url(raw: str) -> tuple[str | None, str]:
    text = (raw or "").strip()
    mime = None
    if text.startswith("data:"):
        header, _, rest = text.partition(",")
        match = re.match(r"data:([^;]+)", header)
        if match:
            mime = match.group(1).lower()
        text = rest
    return mime, re.sub(r"\s+", "", text)


def decode_image(item: dict[str, Any]) -> tuple[str, str, bytes]:
    mime_hint = str(item.get("mime") or "").lower().strip()
    filename = str(item.get("filename") or "image.png")
    data_raw = str(item.get("data") or "")
    parsed_mime, b64 = strip_data_url(data_raw)
    mime = (parsed_mime or mime_hint or "image/png").split(";")[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_MIME:
        raise UploadRefused(f"Unsupported image type: {mime}")
    try:
        blob = base64.b64decode(b64, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise UploadRefused("Invalid image data") from exc
    if not blob:
        raise UploadRefused("Empty image")
    if len(blob) > MAX_BYTES:
        raise UploadRefused(f"Image too large (max {MAX_BYTES // (1024 * 1024)}MB)")
    mime, blob = clean_image(blob)
    return filename, mime, blob


def clean_image(blob: bytes) -> tuple[str, bytes]:
    """The picture in `blob`, decoded and saved again by Pillow → (its real type, the new bytes)."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(blob)) as probe:          # reads the header only
            found, (width, height) = probe.format or "", probe.size
            if found not in STORED_AS:
                raise UploadRefused(f"Not a JPEG, PNG, WebP or GIF picture (the file is {found or 'unknown'})")
            if width * height > MAX_PIXELS:
                raise UploadRefused(f"Picture too large: {width}×{height} pixels "
                                    f"(max {MAX_PIXELS // 1_000_000} million)")
            probe.verify()                                   # a damaged or crafted file fails here
        with Image.open(io.BytesIO(blob)) as image:
            image.seek(0)                                    # an animation: its first frame
            image.load()
            mime = STORED_AS[found]
            if mime == "image/jpeg" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            elif mime == "image/png" and image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                image = image.convert("RGBA")
            out = io.BytesIO()
            if mime == "image/jpeg":
                image.save(out, "JPEG", quality=JPEG_QUALITY)
            elif mime == "image/webp":
                image.save(out, "WEBP", quality=JPEG_QUALITY)
            else:
                image.save(out, "PNG")
    except UploadRefused:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise UploadRefused("Not a picture that can be read (damaged, or not an image at all)") from exc
    return mime, out.getvalue()


def save_images(config: Config, job_id: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not items:
        return []
    if len(items) > MAX_IMAGES:
        raise ValueError(f"At most {MAX_IMAGES} images per message")
    decoded = [decode_image(item) for item in items]        # all checked before any is written
    folder = Path(config.artifacts_dir) / job_id / "images"
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    for i, (filename, mime, blob) in enumerate(decoded, start=1):
        ext = ALLOWED_MIME[mime]
        stem = SAFE_NAME.sub("_", Path(filename).stem)[:40] or f"image{i}"
        stored = f"{i:02d}_{stem}{ext}"
        path = folder / stored
        path.write_bytes(blob)
        saved.append(
            {
                "filename": stored,
                "original": filename,
                "mime": mime,
                "relpath": f"{job_id}/images/{stored}",
            }
        )
    return saved


def image_path(config: Config, relpath: str) -> Path:
    root = Path(config.artifacts_dir).resolve()
    path = (root / relpath).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid attachment path")
    if not path.is_file():
        raise FileNotFoundError(relpath)
    return path


def load_b64_images(config: Config, attachments: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for item in attachments:
        rel = str(item.get("relpath") or "")
        if not rel:
            continue
        blob = image_path(config, rel).read_bytes()
        out.append(base64.b64encode(blob).decode("ascii"))
    return out


def _is_mask_name(item: dict[str, Any]) -> bool:
    name = f"{item.get('filename') or ''} {item.get('original') or ''} {item.get('relpath') or ''}"
    return "mask" in name.lower()


def load_pil_images(config: Config, attachments: list[dict[str, Any]]) -> list[Any]:
    """Load attachment files as RGB PIL images (for img2img). Skips mask_* files."""
    from PIL import Image

    out: list[Any] = []
    for item in attachments:
        kind = str(item.get("kind") or "").lower()
        if kind == "generated" or _is_mask_name(item):
            continue
        rel = str(item.get("relpath") or "")
        if not rel:
            continue
        with Image.open(image_path(config, rel)) as im:
            out.append(im.convert("RGB"))
    if out:
        return out
    for item in attachments:
        if _is_mask_name(item):
            continue
        rel = str(item.get("relpath") or "")
        if not rel:
            continue
        with Image.open(image_path(config, rel)) as im:
            out.append(im.convert("RGB"))
    return out


def load_mask_image(config: Config, attachments: list[dict[str, Any]]) -> Any | None:
    """Load first attachment whose name contains 'mask' (white=repaint)."""
    from PIL import Image

    for item in attachments:
        if not _is_mask_name(item):
            continue
        rel = str(item.get("relpath") or "")
        if not rel:
            continue
        with Image.open(image_path(config, rel)) as im:
            return im.convert("L")
    return None
