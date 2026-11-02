"""Layering: composite (image over image) and preset-driven watermarks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from PIL import Image, ImageChops
from pydantic import Field

from atelier import presets
from atelier.media import MediaType
from atelier.ops.image._io import (
    Box,
    anchor_position,
    boxes_of,
    check_size,
    has_alpha,
    image_op,
    load,
    make_box,
    save,
)
from atelier.ops.image.geometry import Anchor
from atelier.ops.image.text import TextStyle, render_text
from atelier.ops.registry import INTERNAL, ArgError, Artifact, OpContext, Params
from atelier.spec.errors import closest

IMAGE = MediaType.IMAGE
Blend = Literal["normal", "multiply", "screen"]


def place(
    base: Image.Image,
    overlay: Image.Image,
    *,
    anchor: str,
    margin: float,
    scale: float | None,
    opacity: float,
    blend: str = "normal",
    kind: str = "overlay",
    label: str = "overlay",
) -> tuple[Image.Image, Box]:
    W, H = base.size
    ov = overlay.convert("RGBA")
    if scale is not None:
        w = max(1, round(W * scale))
        h = max(1, round(ov.height * w / ov.width))
        check_size(w, h)
        ov = ov.resize((w, h), Image.Resampling.LANCZOS)
    if opacity < 1:
        ov.putalpha(ov.getchannel("A").point(lambda a: round(a * opacity)))
    margin_px = round(margin * min(W, H))
    x, y = anchor_position(anchor, (W, H), ov.size, margin_px)
    canvas = base.convert("RGBA")
    if blend == "normal":
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(ov, (x, y))
        canvas.alpha_composite(layer)
    else:
        region = canvas.crop((x, y, x + ov.width, y + ov.height)).convert("RGB")
        op = ImageChops.multiply if blend == "multiply" else ImageChops.screen
        mixed = op(region, ov.convert("RGB"))
        canvas.paste(mixed, (x, y), ov.getchannel("A"))
    out = canvas if has_alpha(base) else canvas.convert("RGB")
    left, top = max(0, x), max(0, y)
    right, bottom = min(W, x + ov.width), min(H, y + ov.height)
    box = make_box(kind, label, (left, top, right - left, bottom - top), (W, H))
    if (left, top, right, bottom) != (x, y, x + ov.width, y + ov.height):
        box["clipped"] = True
    return out, box


class CompositeParams(Params):
    anchor: Anchor = Field("top_right", description="where the overlay sits")
    margin: float = Field(0.04, ge=0, le=0.5, description="x shorter side")
    scale: float | None = Field(0.2, gt=0, le=1, description="overlay width x base width")
    opacity: float = Field(1.0, ge=0, le=1, description="0 transparent … 1 opaque")
    blend: Blend = Field("normal", description="how overlay colors combine with the base")


@image_op(
    name="image.composite",
    version="1.0.0",
    params=CompositeParams,
    description="layer the 2nd image over the 1st at an anchor",
    inputs=(IMAGE, IMAGE),
    float_precision=4,
)
def composite(ctx: OpContext, inputs: Sequence[Artifact], p: CompositeParams) -> Artifact:
    """Place the second input (logo, badge, sticker) over the first. `scale` sizes the
    overlay relative to the base width; `null` keeps its native size.
    """
    base, overlay = load(inputs[0]), load(inputs[1])
    out, box = place(
        base,
        overlay,
        anchor=p.anchor,
        margin=p.margin,
        scale=p.scale,
        opacity=p.opacity,
        blend=p.blend,
    )
    return save(ctx, out, [*boxes_of(inputs[0]), box])


# -- watermark ---------------------------------------------------------------------------

WATERMARK_KIND = "watermark"


class WatermarkSettings(Params):
    anchor: Anchor = "bottom_right"
    margin: float = Field(0.03, ge=0, le=0.5)
    scale: float = Field(0.15, gt=0, le=1, description="mark width x image width")
    opacity: float = Field(0.6, ge=0, le=1)
    tiled: bool = False
    angle: float = Field(0.0, ge=-90, le=90, description="tiled marks only, clockwise")
    spacing: float = Field(0.5, ge=0, le=3, description="gap between tiles x mark width")
    text: TextStyle = TextStyle()


class WatermarkParams(Params):
    preset: str = Field("corner_subtle", description="watermark style preset")
    text: str | None = Field(None, max_length=200, description="text mark when no logo input")
    preset_version: int | None = Field(None, json_schema_extra=INTERNAL)
    settings: WatermarkSettings | None = Field(None, json_schema_extra=INTERNAL)


def _expand(args: dict[str, Any]) -> dict[str, Any]:
    available = presets.of_kind(WATERMARK_KIND)
    preset = available.get(args["preset"])
    if preset is None:
        names = sorted(available)
        raise ArgError(
            "preset",
            "enum_mismatch",
            f"{args['preset']!r} invalid",
            names,
            closest(args["preset"], names),
        )
    settings = WatermarkSettings.model_validate(preset.settings)
    return {**args, "preset_version": preset.version, "settings": settings.model_dump(mode="json")}


def _inputs_ok(p: WatermarkParams, n: int) -> str | None:
    if n == 1 and not p.text:
        return "image.watermark needs a logo as 2nd input, or args.text"
    if n == 2 and p.text:
        return "image.watermark takes a logo input or args.text, not both"
    if n > 2:
        return f"image.watermark takes 1-2 inputs, got {n}"
    return None


def _text_mark(text: str, style: TextStyle, width: int) -> Image.Image:
    """Render text alone on a transparent canvas sized for `width`."""
    height = max(8, round(width * 0.4))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    fitted = style.model_copy(update={"size": None, "max_size": 1.0, "background": "none"})
    rendered, box = render_text(canvas, text, fitted, (0.0, 0.0, 1.0, 1.0), kind="watermark")
    x, y, w, h = box["box"]
    crop = (round(x * width), round(y * height), round((x + w) * width), round((y + h) * height))
    return rendered.crop(crop) if crop[2] > crop[0] and crop[3] > crop[1] else rendered


def _tile(base: Image.Image, mark: Image.Image, s: WatermarkSettings) -> Image.Image:
    mark = mark.convert("RGBA")
    if s.opacity < 1:
        mark.putalpha(mark.getchannel("A").point(lambda a: round(a * s.opacity)))
    if s.angle:
        mark = mark.rotate(-s.angle, resample=Image.Resampling.BICUBIC, expand=True)
    step_x = max(1, round(mark.width * (1 + s.spacing)))
    step_y = max(1, round(mark.height * (1 + s.spacing)))
    # Composite on a layer padded by one mark on each side so edge tiles can start
    # off-canvas, then crop back to the image.
    mw, mh = mark.size
    layer = Image.new("RGBA", (base.width + 2 * mw, base.height + 2 * mh), (0, 0, 0, 0))
    for row, y in enumerate(range(0, base.height + mh, step_y)):
        shift = (step_x // 2) * (row % 2)
        for x in range(shift, base.width + mw, step_x):
            layer.alpha_composite(mark, (x, y))
    layer = layer.crop((mw, mh, mw + base.width, mh + base.height))
    out = base.convert("RGBA")
    out.alpha_composite(layer)
    return out if has_alpha(base) else out.convert("RGB")


@image_op(
    name="image.watermark",
    version="1.0.0",
    params=WatermarkParams,
    description="preset watermark from a logo (2nd input) or text",
    variadic=True,
    resources=("pillow", "icc", "fonts"),
    canonicalize=_expand,
    check_inputs=_inputs_ok,
    float_precision=4,
)
def watermark(ctx: OpContext, inputs: Sequence[Artifact], p: WatermarkParams) -> Artifact:
    """Apply a watermark style preset (corner or tiled) using either a logo image as the
    second input or `text` rendered with the preset's text style.
    """
    assert p.settings is not None
    s = p.settings
    base = load(inputs[0])
    mark_w = max(8, round(base.width * s.scale))
    if len(inputs) == 2:
        mark = load(inputs[1])
        label = "logo"
    else:
        assert p.text
        mark = _text_mark(p.text, s.text, mark_w)
        label = p.text[:32]
    if s.tiled:
        out = _tile(
            base,
            mark.resize(
                (mark_w, max(1, round(mark.height * mark_w / mark.width))), Image.Resampling.LANCZOS
            ),
            s,
        )
        box: Box = {"kind": "watermark", "label": label, "box": [0.0, 0.0, 1.0, 1.0], "tiled": True}
    else:
        out, box = place(
            base,
            mark,
            anchor=s.anchor,
            margin=s.margin,
            scale=s.scale,
            opacity=s.opacity,
            kind="watermark",
            label=label,
        )
    return save(ctx, out, [*boxes_of(inputs[0]), box])
