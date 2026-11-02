"""Image encoding for `export` (docs/03 §3.4): the settings model and the encoder.

The `export` op itself lives in `ops/export.py` (one op for every media type); this module is
what it calls for an image, and owns the image settings that presets are validated against.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Any, Literal

from PIL import Image
from pydantic import Field, model_validator

from atelier.fmt import human_bytes
from atelier.media import MediaType
from atelier.ops.image._io import boxes_of, check_size, has_alpha, load
from atelier.ops.image.color import flatten
from atelier.ops.image.geometry import Focus, ResizeParams, resize_image
from atelier.ops.registry import (
    Artifact,
    OpContext,
    OpError,
    Params,
)
from atelier.paths import SRGB_ICC
from atelier.spec.types import Color, Fraction

KIND = "export.image"
LOSSY = ("jpeg", "webp", "avif")
EXTENSIONS = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "avif": ".avif", "tiff": ".tif"}
_SUBSAMPLING = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}


class ImageSize(Params):
    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)
    mode: Literal["fit", "fill", "exact", "max"] = "fit"
    focus: Focus = "center"


class SafeArea(Params):
    top: Fraction = Field(0.0, ge=0, le=0.5)
    bottom: Fraction = Field(0.0, ge=0, le=0.5)
    left: Fraction = Field(0.0, ge=0, le=0.5)
    right: Fraction = Field(0.0, ge=0, le=0.5)


class ImageExportSettings(Params):
    format: Literal["jpeg", "png", "webp", "avif", "tiff"]
    quality: int = Field(85, ge=1, le=100)
    min_quality: int | None = Field(None, ge=1, le=100, description="floor for budget search")
    subsampling: Literal["4:4:4", "4:2:2", "4:2:0"] = "4:2:0"
    progressive: bool = False
    optimize: bool = True
    lossless: bool = False
    method: int = Field(4, ge=0, le=6, description="webp effort")
    speed: int = Field(6, ge=0, le=10, description="avif encoder speed")
    compress_level: int = Field(9, ge=0, le=9, description="png zlib level")
    compression: Literal["lzw", "deflate", "none"] = "lzw"
    alpha: bool = False
    background: Color = "#ffffffff"
    icc: Literal["srgb", "none"] = "srgb"
    dpi: int | None = Field(None, ge=1, le=2400)
    size: ImageSize | None = None
    max_bytes: int | None = Field(None, ge=1)
    safe_area: SafeArea | None = None
    checks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ImageExportSettings:
        if self.alpha and self.format == "jpeg":
            raise ValueError("jpeg cannot keep alpha; set alpha: false or use png/webp/avif")
        if self.min_quality is not None and self.min_quality > self.quality:
            raise ValueError("min_quality must be <= quality")
        return self


def _srgb_bytes() -> bytes:
    return SRGB_ICC.read_bytes()


def encode(im: Image.Image, s: ImageExportSettings, quality: int) -> bytes:
    buf = io.BytesIO()
    extra: dict[str, Any] = {}
    if s.icc == "srgb" and im.mode in ("RGB", "RGBA"):
        extra["icc_profile"] = _srgb_bytes()
    if s.dpi:
        extra["dpi"] = (s.dpi, s.dpi)
    if s.format == "jpeg":
        im.save(
            buf,
            "JPEG",
            quality=quality,
            subsampling=_SUBSAMPLING[s.subsampling],
            progressive=s.progressive,
            optimize=s.optimize,
            **extra,
        )
    elif s.format == "png":
        im.save(buf, "PNG", compress_level=s.compress_level, optimize=False, **extra)
    elif s.format == "webp":
        im.save(
            buf,
            "WEBP",
            quality=quality,
            lossless=s.lossless,
            method=s.method,
            exact=True,
            **{k: v for k, v in extra.items() if k != "dpi"},
        )
    elif s.format == "avif":
        im.save(
            buf,
            "AVIF",
            quality=quality,
            speed=s.speed,
            subsampling=s.subsampling,
            max_threads=1,
            **{k: v for k, v in extra.items() if k != "dpi"},
        )
    else:
        codec = {"lzw": "tiff_lzw", "deflate": "tiff_adobe_deflate", "none": None}[s.compression]
        im.save(buf, "TIFF", compression=codec, **extra)
    return buf.getvalue()


def _fit_budget(im: Image.Image, s: ImageExportSettings) -> tuple[bytes, int]:
    """Highest quality in [min_quality, quality] whose output fits max_bytes (binary search)."""
    data = encode(im, s, s.quality)
    if (
        s.max_bytes is None
        or len(data) <= s.max_bytes
        or s.format not in LOSSY
        or s.lossless
        or s.min_quality is None
    ):
        return data, s.quality
    lo, hi = s.min_quality, s.quality - 1
    best = encode(im, s, lo), lo
    if len(best[0]) > s.max_bytes:
        return best
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trial = encode(im, s, mid)
        if len(trial) <= s.max_bytes:
            lo, best = mid, (trial, mid)
        else:
            hi = mid - 1
    return best


def image_export_summary(result: Artifact) -> str:
    m = result.meta
    q = f" · q{m['quality']}" if m["format"] in LOSSY and not m.get("lossless") else ""
    icc = " · sRGB" if m.get("icc") == "sRGB" else ""
    return (
        f"{m['format']} {m['width']}x{m['height']}{q} · {human_bytes(m['bytes'])}{icc}"
        f" · {m['export']['preset']} v{m['export']['version']}"
    )


def export_image(
    ctx: OpContext,
    inputs: Sequence[Artifact],
    preset: str,
    preset_version: int,
    s: ImageExportSettings,
) -> Artifact:
    """Encode the final image with an export preset. Metadata (EXIF, XMP, timestamps) is
    never written; the sRGB profile is embedded unless the preset says `icc: none`.
    With `max_bytes` and `min_quality`, lossy formats pick the highest quality that fits.
    """
    im = load(inputs[0])
    boxes = boxes_of(inputs[0])
    if s.size is not None:
        mode = "fit" if s.size.mode == "max" else s.size.mode
        rp = ResizeParams(
            width=s.size.width,
            height=s.size.height,
            mode=mode,
            focus=s.size.focus,
            upscale=s.size.mode != "max",
        )
        im, boxes = resize_image(im, rp, boxes)
    check_size(*im.size)
    if not s.alpha and has_alpha(im):
        im = flatten(im, s.background)
    if s.format == "jpeg" and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    data, quality = _fit_budget(im, s)
    path = ctx.output_path(EXTENSIONS[s.format])
    path.write_bytes(data)
    if len(data) == 0:
        raise OpError("encoder produced no data")
    meta = {
        "width": im.width,
        "height": im.height,
        "mode": im.mode,
        "format": s.format,
        "quality": quality,
        "lossless": s.lossless,
        "bytes": len(data),
        "icc": "sRGB" if s.icc == "srgb" and im.mode in ("RGB", "RGBA") else None,
        "has_alpha": has_alpha(im),
        "dpi": s.dpi,
        "boxes": boxes,
        "export": {
            "preset": preset,
            "version": preset_version,
            "checks": s.checks,
            "max_bytes": s.max_bytes,
            "size": s.size.model_dump() if s.size else None,
            "safe_area": s.safe_area.model_dump() if s.safe_area else None,
            "alpha": s.alpha,
            "icc": s.icc,
        },
    }
    return Artifact(path=path, media=MediaType.IMAGE, ext=EXTENSIONS[s.format], meta=meta)
