"""Grid layout for collages and contact sheets."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from PIL import Image
from pydantic import Field

from atelier.media import MediaType
from atelier.ops.image._io import fit_aspect, image_op, load, save
from atelier.ops.image.text import TextStyle, render_text
from atelier.ops.registry import Artifact, OpContext, Params
from atelier.spec.types import Aspect, Color, rgba

MAX_CELLS = 64


def build_grid(
    images: Sequence[Image.Image],
    *,
    columns: int | None,
    cell_width: int,
    aspect: float,
    fit: str,
    gap: float,
    background: str,
    labels: Sequence[str] | None,
) -> Image.Image:
    n = len(images)
    cols = columns or math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    cw, ch = cell_width, max(1, round(cell_width / aspect))
    gap_px = round(gap * cw)
    label_h = max(12, round(cw * 0.08)) if labels else 0
    width = cols * cw + (cols + 1) * gap_px
    height = rows * (ch + label_h) + (rows + 1) * gap_px
    bg = rgba(background)
    sheet = Image.new("RGBA", (width, height), bg)
    style = TextStyle(
        weight="medium", color="#333333ff" if sum(bg[:3]) > 384 else "#eeeeeeff", max_size=1.0
    )
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        x = gap_px + c * (cw + gap_px)
        y = gap_px + r * (ch + label_h + gap_px)
        src = im.convert("RGBA")
        if fit == "cover":
            sw, sh = fit_aspect(src.width, src.height, cw / ch)
            left, top = (src.width - sw) // 2, (src.height - sh) // 2
            cell = src.resize(
                (cw, ch), Image.Resampling.LANCZOS, box=(left, top, left + sw, top + sh)
            )
            sheet.alpha_composite(cell, (x, y))
        else:
            ratio = min(cw / src.width, ch / src.height)
            size = (max(1, round(src.width * ratio)), max(1, round(src.height * ratio)))
            cell = src.resize(size, Image.Resampling.LANCZOS)
            sheet.alpha_composite(cell, (x + (cw - size[0]) // 2, y + (ch - size[1]) // 2))
        if labels:
            box = (x / width, (y + ch) / height, cw / width, label_h / height)
            sheet, _ = render_text(sheet, labels[i], style, box)
            sheet = sheet.convert("RGBA")
    return sheet if bg[3] < 255 else sheet.convert("RGB")


class GridParams(Params):
    columns: int | None = Field(None, ge=1, le=16, description="null = square-ish")
    cell_width: int = Field(400, ge=16, le=2048, description="px")
    aspect: Aspect = Field(1.0, description="cell ratio")
    fit: Literal["cover", "contain"] = Field("cover", description="crop to cell or letterbox")
    gap: float = Field(0.03, ge=0, le=0.5, description="x cell width")
    background: Color = Field("#ffffffff", description="sheet and gap color")
    labels: list[str] | None = Field(None, description="one caption per input")


def _inputs_ok(p: GridParams, n: int) -> str | None:
    if n > MAX_CELLS:
        return f"image.grid takes at most {MAX_CELLS} inputs, got {n}"
    if p.labels is not None and len(p.labels) != n:
        return f"labels has {len(p.labels)} entries for {n} inputs"
    return None


@image_op(
    name="image.grid",
    version="1.0.0",
    params=GridParams,
    description="lay images out in a grid (collage/contact sheet), optional captions",
    inputs=(MediaType.IMAGE,),
    variadic=True,
    resources=("pillow", "icc", "fonts"),
    check_inputs=_inputs_ok,
    float_precision=4,
)
def grid(ctx: OpContext, inputs: Sequence[Artifact], p: GridParams) -> Artifact:
    """Arrange the inputs row by row in equal cells. cover crops each image to the cell;
    contain letterboxes it. Layout boxes from the inputs are not carried over.
    """
    sheet = build_grid(
        [load(a) for a in inputs],
        columns=p.columns,
        cell_width=p.cell_width,
        aspect=p.aspect,
        fit=p.fit,
        gap=p.gap,
        background=p.background,
        labels=p.labels,
    )
    return save(ctx, sheet)
