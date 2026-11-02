"""Typesetting with the bundled font pack: auto-fit, wrap, stroke, shadow, contrast guard.

The op records a layout box per text block with `fit` (did it fit its box at the chosen
size) and `contrast` (WCAG ratio of the text color against the background under it).
The text_fit and text_contrast checks read those, so nobody has to look at the image.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pydantic import Field, field_validator, model_validator

from atelier import fonts
from atelier.ops.image._io import Box, boxes_of, has_alpha, image_op, load, make_box, save
from atelier.ops.registry import Artifact, Determinism, OpContext, Params
from atelier.spec.types import Color, Fraction, choice, parse_color, rgba

MIN_PX = 6
PLATE_ALPHAS = (140, 170, 200, 230, 255)  # background="auto" tries these in order
LARGE_PX = 24  # WCAG "large text": 18pt ≈ 24px, or 14pt bold ≈ 19px
LARGE_BOLD_PX = 19
Weight = Literal["regular", "medium", "semibold", "bold"]


class TextStyle(Params):
    font: str = Field(fonts.DEFAULT_FAMILY, description="bundled family")
    weight: Weight = Field("bold", description="Noto families: regular|bold only")
    color: Color = Field("#ffffffff", description="text color")
    align: Literal["left", "center", "right"] = Field("center", description="horizontal")
    valign: Literal["top", "middle", "bottom"] = Field("middle", description="vertical")
    size: Fraction | None = Field(None, gt=0, le=1, description="x image height; null = auto-fit")
    max_size: Fraction = Field(0.25, gt=0, le=1, description="auto-fit ceiling, x image height")
    line_spacing: float = Field(1.2, ge=0.8, le=3, description="x font size")
    stroke: Fraction = Field(0.0, ge=0, le=0.3, description="outline width x font size")
    stroke_color: Color = Field("#000000ff", description="outline color")
    shadow: bool = Field(False, description="soft drop shadow")
    background: str = Field("none", description='"none", "auto" (only if contrast is low) or color')

    @field_validator("font")
    @classmethod
    def _font(cls, v: str) -> str:
        return choice(v, fonts.families(), "font")

    @field_validator("background")
    @classmethod
    def _background(cls, v: str) -> str:
        if v in ("none", "auto"):
            return v
        color = parse_color(v)
        assert isinstance(color, str)
        return color

    @model_validator(mode="after")
    def _weight(self) -> TextStyle:
        available = fonts.weights(self.font)
        if self.weight not in available:
            raise ValueError(f"{self.font} has weights {'|'.join(available)}, not {self.weight}")
        return self


class TextParams(TextStyle):
    text: str = Field(min_length=1, max_length=2000, description="\\n for line breaks")
    box: tuple[Fraction, Fraction, Fraction, Fraction] = Field(
        (0.05, 0.05, 0.9, 0.9), description="[x, y, w, h] region for the text block"
    )


# -- color math --------------------------------------------------------------------------


def _linear(c: NDArray[np.float64]) -> NDArray[np.float64]:
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = _linear(np.array(rgb, dtype=np.float64))
    return float(0.2126 * r + 0.7152 * g + 0.0722 * b)


def contrast(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _region_luminance(im: Image.Image, rect: tuple[int, int, int, int]) -> tuple[float, float]:
    """10th and 90th percentile luminance under `rect` (conservative for busy backdrops)."""
    arr = np.asarray(im.convert("RGB").crop(rect), dtype=np.float64)
    if arr.size == 0:
        return 0.0, 0.0
    lin = _linear(arr)
    lum = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    return float(np.percentile(lum, 10)), float(np.percentile(lum, 90))


# -- layout ------------------------------------------------------------------------------


@functools.cache
def _font(family: str, weight: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        str(fonts.font_path(family, weight)), size=px, layout_engine=ImageFont.Layout.BASIC
    )


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            candidate = f"{line} {word}" if line else word
            if font.getlength(candidate) <= max_w:
                line = candidate
                continue
            if line:
                lines.append(line)
            line = word
            while font.getlength(line) > max_w and len(line) > 1:
                cut = len(line) - 1
                while cut > 1 and font.getlength(line[:cut]) > max_w:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return lines


@dataclass(frozen=True)
class Layout:
    px: int
    lines: list[str]
    width: int
    height: int
    line_height: int
    stroke_px: int
    fits: bool


def _measure(text: str, style: TextStyle, px: int, box_w: int, box_h: int) -> Layout:
    font = _font(style.font, style.weight, px)
    stroke_px = round(px * style.stroke)
    lines = _wrap(text, font, max(1, box_w - 2 * stroke_px))
    ascent, descent = font.getmetrics()
    lh = round(px * style.line_spacing)
    height = (len(lines) - 1) * lh + ascent + descent + 2 * stroke_px
    width = round(max(font.getlength(line) for line in lines)) + 2 * stroke_px
    fits = width <= box_w and height <= box_h
    return Layout(px, lines, width, height, lh, stroke_px, fits)


def layout(text: str, style: TextStyle, size: tuple[int, int], box_px: tuple[int, int]) -> Layout:
    box_w, box_h = box_px
    if style.size is not None:
        return _measure(text, style, max(MIN_PX, round(style.size * size[1])), box_w, box_h)
    lo, hi = MIN_PX, max(MIN_PX, min(box_h, round(style.max_size * size[1])))
    best = _measure(text, style, lo, box_w, box_h)
    if not best.fits:
        return best
    while lo < hi:  # largest px that fits
        mid = (lo + hi + 1) // 2
        trial = _measure(text, style, mid, box_w, box_h)
        if trial.fits:
            lo, best = mid, trial
        else:
            hi = mid - 1
    return best


# -- rendering ---------------------------------------------------------------------------


def render_text(
    im: Image.Image,
    text: str,
    style: TextStyle,
    box: tuple[float, float, float, float],
    kind: str = "text",
) -> tuple[Image.Image, Box]:
    """Draw one text block into `box` (fractions) and return the image plus its layout box."""
    W, H = im.size
    bx, by = round(box[0] * W), round(box[1] * H)
    bw, bh = max(1, round(box[2] * W)), max(1, round(box[3] * H))
    lay = layout(text, style, im.size, (bw, bh))
    font = _font(style.font, style.weight, lay.px)

    x = {"left": bx, "center": bx + (bw - lay.width) // 2, "right": bx + bw - lay.width}
    y = {"top": by, "middle": by + (bh - lay.height) // 2, "bottom": by + bh - lay.height}
    left, top = x[style.align], y[style.valign]
    rect = (left, top, left + lay.width, top + lay.height)
    clip = (max(0, rect[0]), max(0, rect[1]), min(W, rect[2]), min(H, rect[3]))

    color = rgba(style.color)
    text_lum = luminance(color[:3])
    base = im.convert("RGBA")

    p10, p90 = _region_luminance(base, clip)
    ratio = min(contrast(text_lum, p10), contrast(text_lum, p90))
    min_ratio = 3.0 if _is_large(lay.px, style.weight) else 4.5

    pad = round(lay.px * 0.3)
    plate_rect = (rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)

    def with_plate(fill: tuple[int, int, int, int]) -> tuple[Image.Image, float]:
        plate = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(plate).rectangle(plate_rect, fill=fill)
        out = base.copy()
        out.alpha_composite(plate)
        lo, hi = _region_luminance(out, clip)
        return out, min(contrast(text_lum, lo), contrast(text_lum, hi))

    if style.background == "auto" and ratio < min_ratio:
        # Darken (light text) or lighten (dark text) in fixed steps until AA is met.
        shade = (0, 0, 0) if text_lum > 0.18 else (255, 255, 255)
        for alpha in PLATE_ALPHAS:
            base, ratio = with_plate((*shade, alpha))
            if ratio >= min_ratio:
                break
    elif style.background not in ("none", "auto"):
        base, ratio = with_plate(rgba(style.background))

    def draw(
        layer: Image.Image,
        fill: tuple[int, int, int, int],
        dx: int = 0,
        dy: int = 0,
        stroke: bool = True,
    ) -> None:
        d = ImageDraw.Draw(layer)
        ascent = font.getmetrics()[0]
        for i, line in enumerate(lay.lines):
            lw = round(font.getlength(line))
            lx = {
                "left": left,
                "center": left + (lay.width - lw) // 2,
                "right": left + lay.width - lw,
            }[style.align]
            d.text(
                (lx + lay.stroke_px + dx, top + lay.stroke_px + i * lay.line_height + ascent + dy),
                line,
                font=font,
                fill=fill,
                anchor="ls",
                stroke_width=lay.stroke_px if stroke else 0,
                stroke_fill=rgba(style.stroke_color) if stroke else None,
            )

    if style.shadow:
        shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        offset = max(1, round(lay.px * 0.05))
        draw(shadow, (0, 0, 0, 160), offset, offset, stroke=False)
        base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(1, lay.px * 0.06))))
        ratio = max(ratio, min(contrast(text_lum, 0.0), min_ratio))
    if lay.stroke_px:
        ratio = max(ratio, contrast(text_lum, luminance(rgba(style.stroke_color)[:3])))

    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw(layer, color)
    base.alpha_composite(layer)

    out = base if has_alpha(im) else base.convert("RGB")
    if im.mode in ("L", "LA") and color[0] == color[1] == color[2]:
        out = out.convert("LA" if has_alpha(im) else "L")
    rendered = make_box(
        kind,
        text[:32],
        (clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1]),
        im.size,
        fit=lay.fits,
        contrast=round(ratio, 2),
        min_contrast=min_ratio,
        font_px=lay.px,
        lines=len(lay.lines),
    )
    if clip != rect:
        rendered["clipped"] = True
    return out, rendered


def _is_large(px: int, weight: str) -> bool:
    return px >= LARGE_PX or (weight in ("semibold", "bold") and px >= LARGE_BOLD_PX)


@image_op(
    name="image.text",
    version="1.0.0",
    params=TextParams,
    description="typeset text in a box; auto-fit, wrap, stroke, shadow, contrast guard",
    determinism=Determinism.PERCEPTUAL,
    resources=("pillow", "icc", "fonts"),
    float_precision=4,
)
def text(ctx: OpContext, inputs: Sequence[Artifact], p: TextParams) -> Artifact:
    """Draw a text block inside `box` using a bundled font.

    With `size: null` the largest size up to `max_size` that fits the box is chosen and
    lines wrap on spaces. `background: auto` adds a translucent plate only when the
    text/background contrast is below WCAG AA (4.5:1, or 3:1 for large text).
    The layout box feeds the text_fit and text_contrast checks.
    """
    im = load(inputs[0])
    out, rendered = render_text(im, p.text, p, p.box)
    return save(ctx, out, [*boxes_of(inputs[0]), rendered])
