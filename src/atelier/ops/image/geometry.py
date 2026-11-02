"""Geometry: crop, resize, pad, rotate, flip."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
from PIL import Image, ImageFilter
from pydantic import Field, model_validator

from atelier.ops.image._io import (
    ANCHORS,
    Box,
    anchor_position,
    boxes_of,
    check_size,
    crop_boxes,
    fit_aspect,
    flip_boxes,
    focus_origin,
    has_alpha,
    image_op,
    load,
    offset_boxes,
    rotate_boxes,
    save,
    scale_boxes,
)
from atelier.ops.registry import Artifact, OpContext, OpError, Params
from atelier.spec.types import Aspect, Color, Fraction, rgba

Focus = Literal["center", "top", "bottom", "left", "right", "attention"]
Anchor = Literal[
    "top_left",
    "top_center",
    "top_right",
    "center_left",
    "center",
    "center_right",
    "bottom_left",
    "bottom_center",
    "bottom_right",
]
assert set(Anchor.__args__) == set(ANCHORS)  # type: ignore[attr-defined]

FILTERS = {
    "lanczos": Image.Resampling.LANCZOS,
    "bicubic": Image.Resampling.BICUBIC,
    "bilinear": Image.Resampling.BILINEAR,
    "nearest": Image.Resampling.NEAREST,
}

# -- crop --------------------------------------------------------------------------------


class CropParams(Params):
    box: tuple[Fraction, Fraction, Fraction, Fraction] | None = Field(
        None, description="[x, y, w, h] as fractions of the image"
    )
    aspect: Aspect | None = Field(None, description='largest crop with this ratio, e.g. "16:9"')
    focus: Focus = Field(
        "center", description="where the crop sits; attention = busiest area, keeps text/logos"
    )

    @model_validator(mode="after")
    def _one_mode(self) -> CropParams:
        if (self.box is None) == (self.aspect is None):
            raise ValueError("give exactly one of box or aspect")
        if self.box is not None:
            x, y, w, h = self.box
            if min(self.box) < 0 or w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
                raise ValueError("box must lie inside the image: 0 <= x, y and x+w, y+h <= 1")
        return self


def crop_image(
    im: Image.Image, p: CropParams, boxes: list[Box] | None = None
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    if p.box is not None:
        x, y, w, h = p.box
        left, top = round(x * im.width), round(y * im.height)
        right = max(left + 1, round((x + w) * im.width))
        bottom = max(top + 1, round((y + h) * im.height))
        rect = (left, top, min(right, im.width), min(bottom, im.height))
    else:
        assert p.aspect is not None
        cw, ch = fit_aspect(im.width, im.height, p.aspect)
        x0, y0 = focus_origin(im, cw, ch, p.focus, boxes or ())
        rect = (x0, y0, x0 + cw, y0 + ch)
    return im.crop(rect), rect


@image_op(
    name="image.crop",
    version="1.0.0",
    params=CropParams,
    description="crop to a box or to an aspect ratio (center/edge/attention focus)",
)
def crop(ctx: OpContext, inputs: Sequence[Artifact], p: CropParams) -> Artifact:
    """Crop by an explicit fractional box, or to the largest window of `aspect`.

    `focus=attention` places the window over the region with the most detail (edge energy
    and saturation), which keeps subjects in frame for most product and portrait shots.
    """
    im = load(inputs[0])
    boxes = boxes_of(inputs[0])
    out, rect = crop_image(im, p, boxes)
    return save(ctx, out, crop_boxes(boxes, im.size, rect))


# -- resize ------------------------------------------------------------------------------


class ResizeParams(Params):
    width: int | None = Field(None, ge=1, le=16384, description="target width in px")
    height: int | None = Field(None, ge=1, le=16384, description="target height in px")
    scale: float | None = Field(None, gt=0, le=16, description="uniform factor instead of size")
    mode: Literal["fit", "fill", "exact"] = Field(
        "fit", description="fit inside, fill and crop, or stretch"
    )
    focus: Focus = Field("center", description="crop position for fill")
    filter: Literal["lanczos", "bicubic", "bilinear", "nearest"] = Field(
        "lanczos", description="resampling; nearest for pixel art"
    )
    upscale: bool = Field(True, description="allow enlarging")

    @model_validator(mode="after")
    def _sizes(self) -> ResizeParams:
        has_size = self.width is not None or self.height is not None
        if self.scale is not None and has_size:
            raise ValueError("give scale or width/height, not both")
        if self.scale is None and not has_size:
            raise ValueError("give width, height, or scale")
        if self.mode in ("fill", "exact") and (self.width is None or self.height is None):
            raise ValueError(f"mode {self.mode} needs both width and height")
        return self


def target_size(im: Image.Image, p: ResizeParams) -> tuple[int, int]:
    w, h = im.size
    if p.scale is not None:
        tw, th = round(w * p.scale), round(h * p.scale)
    elif p.width is not None and p.height is not None:
        if p.mode == "fit":
            ratio = min(p.width / w, p.height / h)
            tw, th = round(w * ratio), round(h * ratio)
        else:
            tw, th = p.width, p.height
    elif p.width is not None:
        tw, th = p.width, round(h * p.width / w)
    else:
        assert p.height is not None
        tw, th = round(w * p.height / h), p.height
    tw, th = max(1, tw), max(1, th)
    if not p.upscale and (tw > w or th > h) and p.mode != "fill":
        ratio = min(w / tw, h / th, 1.0)
        tw, th = max(1, round(tw * ratio)), max(1, round(th * ratio))
    return tw, th


def resize_image(
    im: Image.Image, p: ResizeParams, boxes: list[Box]
) -> tuple[Image.Image, list[Box]]:
    tw, th = target_size(im, p)
    check_size(tw, th)
    resample = FILTERS[p.filter]
    if p.mode != "fill":
        return im.resize((tw, th), resample), scale_boxes(boxes)
    cw, ch = fit_aspect(im.width, im.height, tw / th)
    x0, y0 = focus_origin(im, cw, ch, p.focus, boxes)
    rect = (x0, y0, x0 + cw, y0 + ch)
    if not p.upscale and (tw > cw or th > ch):
        tw, th = cw, ch
    out = im.resize((tw, th), resample, box=rect)
    return out, crop_boxes(boxes, im.size, rect)


@image_op(
    name="image.resize",
    version="1.0.0",
    params=ResizeParams,
    description="resize by fit/fill/exact or scale; Lanczos by default",
)
def resize(ctx: OpContext, inputs: Sequence[Artifact], p: ResizeParams) -> Artifact:
    """Resize with an explicit resampling filter.

    fit: keep aspect, fit inside width x height. fill: cover width x height, crop the
    overflow at `focus`. exact: stretch. One of width/height alone keeps the aspect ratio.
    """
    im = load(inputs[0])
    out, boxes = resize_image(im, p, boxes_of(inputs[0]))
    return save(ctx, out, boxes)


# -- pad ---------------------------------------------------------------------------------


class PadParams(Params):
    aspect: Aspect | None = Field(None, description="extend the canvas to this ratio")
    width: int | None = Field(None, ge=1, le=16384, description="canvas width in px")
    height: int | None = Field(None, ge=1, le=16384, description="canvas height in px")
    margin: Fraction | None = Field(None, ge=0, le=2, description="each side, x shorter side")
    fill: Literal["color", "blur", "mirror"] = Field(
        "color", description="solid color, blurred copy, or mirrored edges"
    )
    color: Color = Field("#ffffffff", description="canvas color for fill=color")
    anchor: Anchor = Field("center", description="where the image sits on the canvas")

    @model_validator(mode="after")
    def _one_mode(self) -> PadParams:
        modes = [
            self.aspect is not None,
            self.width is not None or self.height is not None,
            self.margin is not None,
        ]
        if sum(modes) != 1:
            raise ValueError("give exactly one of aspect, width+height, or margin")
        if modes[1] and (self.width is None or self.height is None):
            raise ValueError("width and height go together")
        return self


def _canvas_size(im: Image.Image, p: PadParams) -> tuple[int, int]:
    w, h = im.size
    if p.aspect is not None:
        if w / h < p.aspect:
            return max(w, round(h * p.aspect)), h
        return w, max(h, round(w / p.aspect))
    if p.margin is not None:
        m = round(min(w, h) * p.margin)
        return w + 2 * m, h + 2 * m
    assert p.width is not None and p.height is not None
    if p.width < w or p.height < h:
        raise OpError(
            f"canvas {p.width}x{p.height} is smaller than the image {w}x{h}; "
            "use image.resize or image.crop first"
        )
    return p.width, p.height


def _background(
    im: Image.Image, size: tuple[int, int], p: PadParams, offset: tuple[int, int]
) -> Image.Image:
    cw, ch = size
    if p.fill == "blur":
        scale = max(cw / im.width, ch / im.height)
        cover = im.resize(
            (max(cw, round(im.width * scale)), max(ch, round(im.height * scale))),
            Image.Resampling.BILINEAR,
        )
        left, top = (cover.width - cw) // 2, (cover.height - ch) // 2
        bg = cover.crop((left, top, left + cw, top + ch))
        return bg.filter(ImageFilter.GaussianBlur(radius=max(cw, ch) * 0.03))
    if p.fill == "mirror":
        arr = np.asarray(im)
        ox, oy = offset
        pad = ((oy, ch - im.height - oy), (ox, cw - im.width - ox))
        pad_width = pad if arr.ndim == 2 else (*pad, (0, 0))
        return Image.fromarray(np.pad(arr, pad_width, mode="symmetric"), im.mode)
    color = rgba(p.color)
    mode = im.mode if color[3] == 255 else ("LA" if im.mode in ("L", "LA") else "RGBA")
    fill: tuple[int, ...] | int
    if mode in ("L", "LA"):
        luma = round(0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2])
        fill = luma if mode == "L" else (luma, color[3])
    else:
        fill = color[:3] if mode == "RGB" else color
    return Image.new(mode, size, fill)


@image_op(
    name="image.pad",
    version="1.0.0",
    params=PadParams,
    description="extend the canvas to an aspect/size/margin with color, blur or mirror",
)
def pad(ctx: OpContext, inputs: Sequence[Artifact], p: PadParams) -> Artifact:
    """Extend the canvas without scaling the image. Useful before a fixed-aspect export."""
    im = load(inputs[0])
    size = _canvas_size(im, p)
    check_size(*size)
    offset = anchor_position(p.anchor, size, im.size, 0)
    bg = _background(im, size, p, offset)
    if bg.mode != im.mode:
        im = im.convert(bg.mode)
    if has_alpha(im):
        bg.alpha_composite(im, offset) if bg.mode == "RGBA" else bg.paste(im, offset, im)
    else:
        bg.paste(im, offset)
    return save(ctx, bg, offset_boxes(boxes_of(inputs[0]), im.size, size, offset))


# -- rotate / flip -----------------------------------------------------------------------


class RotateParams(Params):
    degrees: float = Field(ge=-360, le=360, description="clockwise")
    expand: bool = Field(True, description="grow the canvas to fit the rotated image")
    fill: Color = Field("#00000000", description="color for uncovered corners")


_QUARTER_TURNS = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


@image_op(
    name="image.rotate",
    version="1.0.0",
    params=RotateParams,
    description="rotate clockwise; exact for multiples of 90°",
    float_precision=4,
)
def rotate(ctx: OpContext, inputs: Sequence[Artifact], p: RotateParams) -> Artifact:
    """Rotate clockwise by `degrees`. Multiples of 90° are lossless transposes; other angles
    use bicubic resampling and fill the corners with `fill` (transparent by default, which
    adds an alpha channel).
    """
    im = load(inputs[0])
    turns = p.degrees % 360
    if turns == 0:
        out = im.copy()
    elif turns in _QUARTER_TURNS:
        out = im.transpose(_QUARTER_TURNS[int(turns)])
    else:
        color = rgba(p.fill)
        if color[3] < 255 and not has_alpha(im):
            im = im.convert("LA" if im.mode == "L" else "RGBA")
        fill = color if im.mode == "RGBA" else color[:3] if im.mode == "RGB" else color[0]
        out = im.rotate(
            -p.degrees, resample=Image.Resampling.BICUBIC, expand=p.expand, fillcolor=fill
        )
    boxes = rotate_boxes(boxes_of(inputs[0]), im.size, out.size, turns)
    return save(ctx, out, boxes)


class FlipParams(Params):
    direction: Literal["horizontal", "vertical"] = Field(
        "horizontal", description="horizontal mirrors left/right"
    )


@image_op(
    name="image.flip",
    version="1.0.0",
    params=FlipParams,
    description="mirror horizontally or vertically",
)
def flip(ctx: OpContext, inputs: Sequence[Artifact], p: FlipParams) -> Artifact:
    """Lossless mirror."""
    im = load(inputs[0])
    horizontal = p.direction == "horizontal"
    method = Image.Transpose.FLIP_LEFT_RIGHT if horizontal else Image.Transpose.FLIP_TOP_BOTTOM
    return save(ctx, im.transpose(method), flip_boxes(boxes_of(inputs[0]), horizontal))
