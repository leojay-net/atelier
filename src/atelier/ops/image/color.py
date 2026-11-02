"""Color: adjust (tone and color) and convert (mode, alpha)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

from PIL import Image, ImageEnhance
from pydantic import Field

from atelier.ops.image._io import boxes_of, has_alpha, image_op, load, save
from atelier.ops.registry import Artifact, OpContext, Params
from atelier.spec.types import Color, rgba


class AdjustParams(Params):
    brightness: float = Field(1.0, ge=0, le=3, description="1 = unchanged")
    contrast: float = Field(1.0, ge=0, le=3, description="1 = unchanged")
    saturation: float = Field(1.0, ge=0, le=3, description="1 = unchanged; 0 = gray")
    gamma: float = Field(1.0, ge=0.1, le=5, description=">1 brightens midtones")
    temperature: float = Field(0.0, ge=-1, le=1, description="-1 cool … +1 warm")
    black: float = Field(0.0, ge=0, lt=1, description="input level mapped to black")
    white: float = Field(1.0, gt=0, le=1, description="input level mapped to white")


def _lut(fn: Callable[[int], float]) -> list[int]:
    return [min(255, max(0, round(fn(i)))) for i in range(256)]


def adjust_image(im: Image.Image, p: AdjustParams) -> Image.Image:
    alpha = im.getchannel("A") if has_alpha(im) else None
    base = im.convert("L" if im.mode in ("L", "LA") else "RGB")

    if (p.black, p.white, p.gamma) != (0.0, 1.0, 1.0):
        lo, hi = p.black * 255, max(p.white * 255, p.black * 255 + 1)

        def level(v: int) -> float:
            t = min(1.0, max(0.0, (v - lo) / (hi - lo)))
            return 255.0 * float(t ** (1 / p.gamma))

        base = base.point(_lut(level) * len(base.getbands()))
    if p.brightness != 1:
        base = ImageEnhance.Brightness(base).enhance(p.brightness)
    if p.contrast != 1:
        base = ImageEnhance.Contrast(base).enhance(p.contrast)
    if p.saturation != 1 and base.mode == "RGB":
        base = ImageEnhance.Color(base).enhance(p.saturation)
    if p.temperature != 0 and base.mode == "RGB":
        shift = 30 * p.temperature
        r = _lut(lambda v: v + shift)
        b = _lut(lambda v: v - shift)
        base = base.point(r + list(range(256)) + b)

    if alpha is not None:
        base = base.convert("LA" if base.mode == "L" else "RGBA")
        base.putalpha(alpha)
    return base


@image_op(
    name="image.adjust",
    version="1.0.0",
    params=AdjustParams,
    description="brightness, contrast, saturation, gamma, temperature, levels",
    float_precision=4,
)
def adjust(ctx: OpContext, inputs: Sequence[Artifact], p: AdjustParams) -> Artifact:
    """Tone and color adjustments, applied in a fixed order: levels (black/white) and
    gamma, then brightness, contrast, saturation, temperature. Alpha is preserved.
    """
    return save(ctx, adjust_image(load(inputs[0]), p), boxes_of(inputs[0]))


class ConvertParams(Params):
    mode: Literal["rgb", "rgba", "grayscale", "keep"] = Field(
        "keep", description="channel layout of the result"
    )
    alpha: Literal["keep", "flatten"] = Field("keep", description="flatten onto background")
    background: Color = Field("#ffffffff", description="used when flattening alpha")


def flatten(im: Image.Image, background: str) -> Image.Image:
    if not has_alpha(im):
        return im
    color = rgba(background)
    rgba_im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, (*color[:3], 255))
    bg.alpha_composite(rgba_im)
    return bg.convert("L" if im.mode == "LA" else "RGB")


@image_op(
    name="image.convert",
    version="1.0.0",
    params=ConvertParams,
    description="change channels (rgb/rgba/grayscale) or flatten alpha",
)
def convert(ctx: OpContext, inputs: Sequence[Artifact], p: ConvertParams) -> Artifact:
    """Channel conversion inside the sRGB working space. ICC handling for output happens
    in `export`; inputs are already converted to sRGB when loaded.
    """
    im = load(inputs[0])
    if p.alpha == "flatten" or (p.mode in ("rgb", "grayscale") and has_alpha(im)):
        im = flatten(im, p.background)
    target = {"rgb": "RGB", "rgba": "RGBA", "grayscale": "LA" if has_alpha(im) else "L"}
    if p.mode != "keep":
        im = im.convert(target[p.mode])
    return save(ctx, im, boxes_of(inputs[0]))
