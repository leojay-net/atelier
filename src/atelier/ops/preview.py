"""Cheap visual summaries for the escalation ladder (docs/06-verification.md §6.2).

Previews are hidden ops, so they run in the same sandboxed workers and are cached like
any other step. They are small by construction: the model's eyes are expensive.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from PIL import Image
from pydantic import Field

from atelier.media import MediaType
from atelier.ops.image._io import image_op, load
from atelier.ops.image.color import flatten
from atelier.ops.image.grid import MAX_CELLS, build_grid
from atelier.ops.image.text import TextStyle, render_text
from atelier.ops.registry import Artifact, OpContext, Params
from atelier.spec.types import Fraction

IMAGE = MediaType.IMAGE
FEW_COLORS = 256  # text-heavy/flat images stay PNG so glyphs are not smeared by JPEG


def _encode(ctx: OpContext, im: Image.Image) -> Artifact:
    im = flatten(im, "#ffffffff") if im.mode in ("LA", "RGBA") else im
    buf = io.BytesIO()
    if im.getcolors(FEW_COLORS) is not None:
        im.save(buf, "PNG", compress_level=6, optimize=False)
        ext = ".png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=70, subsampling=2, optimize=True)
        ext = ".jpg"
    path = ctx.output_path(ext)
    path.write_bytes(buf.getvalue())
    return Artifact(
        path, IMAGE, ext, {"width": im.width, "height": im.height, "bytes": len(buf.getvalue())}
    )


def preview_summary(result: Artifact) -> str:
    m = result.meta
    tokens = round(m["width"] * m["height"] / 750)
    return f"preview {m['width']}x{m['height']} · ~{tokens} tokens"


def _shrink(im: Image.Image, long_edge: int) -> Image.Image:
    ratio = long_edge / max(im.size)
    if ratio >= 1:
        return im
    size = (max(1, round(im.width * ratio)), max(1, round(im.height * ratio)))
    return im.resize(size, Image.Resampling.LANCZOS)


class ThumbParams(Params):
    long_edge: int = Field(512, ge=64, le=1024)


@image_op(
    name="preview.thumb",
    version="1.0.0",
    params=ThumbParams,
    hidden=True,
    description="small thumbnail (L1)",
    summary=preview_summary,
)
def thumb(ctx: OpContext, inputs: Sequence[Artifact], p: ThumbParams) -> Artifact:
    """Lanczos downscale to `long_edge`; JPEG q70, or PNG for flat/text-heavy images."""
    return _encode(ctx, _shrink(load(inputs[0]), p.long_edge))


class CropPreviewParams(Params):
    region: tuple[Fraction, Fraction, Fraction, Fraction] = Field(
        description="[x, y, w, h] fractions"
    )
    long_edge: int = Field(768, ge=64, le=1024)


@image_op(
    name="preview.crop",
    version="1.0.0",
    params=CropPreviewParams,
    hidden=True,
    description="native-resolution region crop (L3)",
    summary=preview_summary,
)
def crop(ctx: OpContext, inputs: Sequence[Artifact], p: CropPreviewParams) -> Artifact:
    """Crop a region at native resolution (downscaled only if larger than `long_edge`)."""
    im = load(inputs[0])
    x, y, w, h = p.region
    rect = (
        round(x * im.width),
        round(y * im.height),
        max(round(x * im.width) + 1, round((x + w) * im.width)),
        max(round(y * im.height) + 1, round((y + h) * im.height)),
    )
    return _encode(ctx, _shrink(im.crop(rect), p.long_edge))


class CompareParams(Params):
    height: int = Field(384, ge=64, le=768)
    labels: tuple[str, str] = ("before", "after")


@image_op(
    name="preview.compare",
    version="1.0.0",
    params=CompareParams,
    hidden=True,
    description="before/after side by side",
    inputs=(IMAGE, IMAGE),
    resources=("pillow", "icc", "fonts"),
    summary=preview_summary,
)
def compare(ctx: OpContext, inputs: Sequence[Artifact], p: CompareParams) -> Artifact:
    """Both images at the same height, side by side, labelled."""
    ims = [load(a) for a in inputs]
    scaled = [
        im.resize(
            (max(1, round(im.width * p.height / im.height)), p.height), Image.Resampling.LANCZOS
        )
        for im in ims
    ]
    gap, label_h = 8, 28
    sheet = Image.new("RGB", (sum(s.width for s in scaled) + gap, p.height + label_h), "white")
    x = 0
    style = TextStyle(weight="medium", color="#333333ff", max_size=1.0)
    for im, label in zip(scaled, p.labels, strict=True):
        sheet.paste(flatten(im, "#ffffffff") if im.mode in ("LA", "RGBA") else im, (x, label_h))
        box = (x / sheet.width, 0.0, im.width / sheet.width, label_h / sheet.height)
        sheet, _ = render_text(sheet, label, style, box)
        x += im.width + gap
    return _encode(ctx, sheet)


class SheetParams(Params):
    columns: int | None = Field(None, ge=1, le=8)
    cell_width: int = Field(256, ge=64, le=512)
    labels: list[str] | None = None


@image_op(
    name="preview.sheet",
    version="1.0.0",
    params=SheetParams,
    hidden=True,
    description="contact sheet of several images (L2)",
    variadic=True,
    resources=("pillow", "icc", "fonts"),
    summary=preview_summary,
    check_inputs=lambda p, n: None if n <= MAX_CELLS else f"at most {MAX_CELLS} inputs",
)
def sheet(ctx: OpContext, inputs: Sequence[Artifact], p: SheetParams) -> Artifact:
    """One grid image replaces many separate previews; cells are numbered unless labelled."""
    labels = p.labels or [str(i + 1) for i in range(len(inputs))]
    images = [load(a) for a in inputs]
    ratios = sorted(im.width / im.height for im in images)
    im = build_grid(
        images,
        columns=p.columns,
        cell_width=p.cell_width,
        aspect=ratios[len(ratios) // 2],  # median: few letterboxed cells
        fit="contain",
        gap=0.03,
        background="#ffffffff",
        labels=labels[: len(inputs)],
    )
    return _encode(ctx, im)
