"""Image loading, the working color space, intermediates, and layout boxes.

Every image op goes through `load` and `save`:

* **load** decodes the first frame, applies EXIF orientation, converts to one of the
  working modes (L, LA, RGB, RGBA; 8-bit) and to sRGB when an ICC profile says otherwise.
* **save** writes a lossless PNG intermediate with fixed settings and no metadata, so
  intermediates are byte-stable. Final encoding happens only in `export`.

**Boxes** are the layout records (text blocks, overlays, watermarks) that checks use:
normalized `[x, y, w, h]` in the image they belong to. Geometry ops transform them, so
checks can run on the final output's metadata without decoding pixels in the parent.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

import numpy as np
from PIL import Image, ImageCms, ImageFilter, ImageOps

from atelier.media import MediaType
from atelier.ops.registry import (
    Artifact,
    Canonicalizer,
    Determinism,
    InputCheck,
    OpContext,
    OpError,
    Params,
    op,
)
from atelier.paths import SRGB_ICC

MAX_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # decompression-bomb guard (docs/10-security.md)
WORKING_MODES = ("L", "LA", "RGB", "RGBA")
PNG_LEVEL = 1
BOX_PRECISION = 6

Box = dict[str, Any]
PointMap = Callable[[float, float], tuple[float, float]]


# -- registration ------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Artifact])


def image_op(
    *,
    name: str,
    version: str,
    params: type[Params],
    description: str,
    inputs: Sequence[MediaType] = (MediaType.IMAGE,),
    variadic: bool = False,
    determinism: Determinism = Determinism.BIT_EXACT,
    resources: Sequence[str] = ("pillow", "icc"),
    summary: Callable[[Artifact], str] | None = None,
    float_precision: int = 6,
    hidden: bool = False,
    canonicalize: Canonicalizer | None = None,
    check_inputs: InputCheck | None = None,
) -> Callable[[F], F]:
    """`@op` with the image-domain defaults: image → image, Pillow + ICC resources."""
    return op(
        name=name,
        version=version,
        inputs=inputs,
        output=MediaType.IMAGE,
        determinism=determinism,
        params=params,
        summary=summary or image_summary,
        description=description,
        variadic=variadic,
        resources=resources,
        float_precision=float_precision,
        hidden=hidden,
        canonicalize=canonicalize,
        check_inputs=check_inputs,
    )


# -- load / save -------------------------------------------------------------------------


def _is_srgb(icc: bytes) -> bool:
    try:
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
    except (OSError, ImageCms.PyCMSError):
        return True  # unreadable profile: treat as sRGB rather than guess
    return "srgb" in desc.lower()


def _srgb_profile() -> ImageCms.ImageCmsProfile:
    return ImageCms.ImageCmsProfile(str(SRGB_ICC))


def _to_srgb(im: Image.Image, icc: bytes) -> Image.Image:
    src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    alpha = im.getchannel("A") if im.mode == "RGBA" else None
    base = im.convert("RGB") if im.mode == "RGBA" else im
    out_mode = "RGB" if base.mode in ("RGB", "CMYK") else base.mode
    try:
        converted = ImageCms.profileToProfile(
            base,
            src,
            _srgb_profile(),
            renderingIntent=ImageCms.Intent.PERCEPTUAL,
            outputMode=out_mode,
        )
    except ImageCms.PyCMSError:
        return im.convert("RGBA" if alpha else "RGB")
    assert converted is not None
    if alpha is not None:
        converted = converted.convert("RGBA")
        converted.putalpha(alpha)
    return converted


def to_working(im: Image.Image) -> Image.Image:
    icc = im.info.get("icc_profile")
    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        arr = np.asarray(im, dtype=np.float64)
        top = 65535.0 if im.mode.startswith("I;16") or arr.max(initial=0) > 255 else 255.0
        im = Image.fromarray(np.clip(np.rint(arr / top * 255), 0, 255).astype(np.uint8), "L")
    elif im.mode == "1":
        im = im.convert("L")
    elif im.mode in ("P", "PA"):
        im = im.convert("RGBA" if "transparency" in im.info or im.mode == "PA" else "RGB")
    elif im.mode == "CMYK":
        im = _to_srgb(im, icc) if icc else im.convert("RGB")
        icc = None
    elif im.mode not in WORKING_MODES:
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    if icc and im.mode in ("RGB", "RGBA") and not _is_srgb(icc):
        im = _to_srgb(im, icc)
    im.info.clear()
    return im


def load(artifact: Artifact) -> Image.Image:
    try:
        with Image.open(artifact.path) as src:
            src.seek(0)
            im = ImageOps.exif_transpose(src)
            im.load()
            info_icc = src.info.get("icc_profile")
    except Image.DecompressionBombError:
        raise OpError(f"image exceeds the {MAX_PIXELS // 1_000_000} MP limit") from None
    except (OSError, SyntaxError, ValueError) as e:
        raise OpError(f"cannot decode image: {e}") from None
    if info_icc and "icc_profile" not in im.info:
        im.info["icc_profile"] = info_icc
    return to_working(im)


def check_size(width: int, height: int) -> None:
    if width < 1 or height < 1:
        raise OpError(f"result would be {width}x{height}; both sides must be at least 1px")
    if width * height > MAX_PIXELS:
        raise OpError(f"result {width}x{height} exceeds the {MAX_PIXELS // 1_000_000} MP limit")


def image_meta(im: Image.Image, boxes: Iterable[Box]) -> dict[str, Any]:
    return {"width": im.width, "height": im.height, "mode": im.mode, "boxes": list(boxes)}


def save(ctx: OpContext, im: Image.Image, boxes: Iterable[Box] = ()) -> Artifact:
    if im.mode not in WORKING_MODES:
        raise AssertionError(f"op produced mode {im.mode}")
    path = ctx.output_path(".png")
    im.info.clear()
    im.save(path, "PNG", compress_level=PNG_LEVEL, optimize=False)
    return Artifact(path=path, media=MediaType.IMAGE, ext=".png", meta=image_meta(im, boxes))


def image_summary(result: Artifact) -> str:
    m = result.meta
    extra = f" · {len(m['boxes'])} layout box(es)" if m.get("boxes") else ""
    return f"image {m['width']}x{m['height']} {m['mode']}{extra}"


def has_alpha(im: Image.Image) -> bool:
    return im.mode in ("LA", "RGBA")


def rgba_image(im: Image.Image) -> Image.Image:
    return im if im.mode == "RGBA" else im.convert("RGBA")


def match_mode(im: Image.Image, like: Image.Image) -> Image.Image:
    """Convert back to the input's mode family, keeping alpha if either has it."""
    alpha = has_alpha(like) or has_alpha(im)
    gray = like.mode in ("L", "LA") and im.mode in ("L", "LA")
    target = ("LA" if alpha else "L") if gray else ("RGBA" if alpha else "RGB")
    return im if im.mode == target else im.convert(target)


# -- boxes -------------------------------------------------------------------------------


def boxes_of(artifact: Artifact) -> list[Box]:
    return [dict(b) for b in artifact.meta.get("boxes", [])]


def _clip01(v: float) -> float:
    return min(1.0, max(0.0, v))


def map_boxes(
    boxes: Iterable[Box],
    src: tuple[int, int],
    dst: tuple[int, int],
    point: PointMap,
) -> list[Box]:
    """Map each box's corners through `point` (src px → dst px) and re-normalize.

    Boxes that end up partly outside the new image are clipped and flagged.
    """
    sw, sh = src
    dw, dh = dst
    out = []
    for b in boxes:
        x, y, w, h = b["box"]
        corners = [point(cx * sw, cy * sh) for cx in (x, x + w) for cy in (y, y + h)]
        xs = [c[0] / dw for c in corners]
        ys = [c[1] / dh for c in corners]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        cx0, cx1, cy0, cy1 = _clip01(x0), _clip01(x1), _clip01(y0), _clip01(y1)
        nb = dict(b)
        nb["box"] = [round(v, BOX_PRECISION) for v in (cx0, cy0, cx1 - cx0, cy1 - cy0)]
        if (cx0, cx1, cy0, cy1) != (x0, x1, y0, y1) and (x1 - x0) * (y1 - y0) > 0:
            lost = 1 - ((cx1 - cx0) * (cy1 - cy0)) / ((x1 - x0) * (y1 - y0))
            if lost > 1e-6:
                nb["clipped"] = True
        out.append(nb)
    return out


def scale_boxes(boxes: Iterable[Box]) -> list[Box]:
    """A pure resize keeps normalized coordinates."""
    return [dict(b) for b in boxes]


def crop_boxes(
    boxes: Iterable[Box], src: tuple[int, int], crop: tuple[int, int, int, int]
) -> list[Box]:
    left, top, right, bottom = crop
    return map_boxes(boxes, src, (right - left, bottom - top), lambda x, y: (x - left, y - top))


def offset_boxes(
    boxes: Iterable[Box], src: tuple[int, int], dst: tuple[int, int], offset: tuple[int, int]
) -> list[Box]:
    ox, oy = offset
    return map_boxes(boxes, src, dst, lambda x, y: (x + ox, y + oy))


def rotate_boxes(
    boxes: Iterable[Box], src: tuple[int, int], dst: tuple[int, int], degrees_cw: float
) -> list[Box]:
    """Rotation about the center, as Pillow does with expand=True."""
    rad = math.radians(degrees_cw)
    cos, sin = math.cos(rad), math.sin(rad)
    scx, scy = src[0] / 2, src[1] / 2
    dcx, dcy = dst[0] / 2, dst[1] / 2

    def point(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - scx, y - scy
        return (dcx + dx * cos - dy * sin, dcy + dx * sin + dy * cos)

    return map_boxes(boxes, src, dst, point)


def flip_boxes(boxes: Iterable[Box], horizontal: bool) -> list[Box]:
    out = []
    for b in boxes:
        x, y, w, h = b["box"]
        nb = dict(b)
        nb["box"] = (
            [round(1 - x - w, BOX_PRECISION), y, w, h]
            if horizontal
            else [x, round(1 - y - h, BOX_PRECISION), w, h]
        )
        out.append(nb)
    return out


def make_box(
    kind: str,
    label: str,
    px: tuple[float, float, float, float],
    size: tuple[int, int],
    **extra: Any,
) -> Box:
    x, y, w, h = px
    W, H = size
    box = [round(v, BOX_PRECISION) for v in (x / W, y / H, w / W, h / H)]
    return {"kind": kind, "label": label, "box": box, **extra}


# -- anchoring ---------------------------------------------------------------------------

_ANCHOR_AXES = {
    "top_left": ("left", "top"),
    "top_center": ("center", "top"),
    "top_right": ("right", "top"),
    "center_left": ("left", "center"),
    "center": ("center", "center"),
    "center_right": ("right", "center"),
    "bottom_left": ("left", "bottom"),
    "bottom_center": ("center", "bottom"),
    "bottom_right": ("right", "bottom"),
}
ANCHORS = tuple(_ANCHOR_AXES)


def anchor_position(
    anchor: str, outer: tuple[int, int], inner: tuple[int, int], margin: int
) -> tuple[int, int]:
    """Top-left corner that places `inner` at `anchor` inside `outer`, `margin` px in."""
    (ow, oh), (iw, ih) = outer, inner
    horizontal, vertical = _ANCHOR_AXES[anchor]
    xs = {"left": margin, "center": (ow - iw) // 2, "right": ow - iw - margin}
    ys = {"top": margin, "center": (oh - ih) // 2, "bottom": oh - ih - margin}
    return xs[horizontal], ys[vertical]


# -- focus / attention -------------------------------------------------------------------

FOCUS = ("center", "top", "bottom", "left", "right", "attention")
_ATTENTION_EDGE = 256


def _attention_origin(im: Image.Image, crop_w: int, crop_h: int) -> tuple[int, int]:
    """Place a crop window over the region with the most edge energy and saturation.

    Computed on a downscaled copy with integral images; ties resolve toward the center.
    """
    scale = min(1.0, _ATTENTION_EDGE / max(im.size))
    small = im.convert("RGB").resize(
        (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
        Image.Resampling.BILINEAR,
    )
    edges = np.asarray(small.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float64)
    sat = np.asarray(small.convert("HSV").getchannel("S"), dtype=np.float64)
    energy = edges + 0.5 * sat
    sw, sh = small.size
    cw = min(sw, max(1, round(crop_w * scale)))
    ch = min(sh, max(1, round(crop_h * scale)))
    integral = np.pad(energy.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    sums = integral[ch:, cw:] - integral[:-ch, cw:] - integral[ch:, :-cw] + integral[:-ch, :-cw]
    ys, xs = np.mgrid[0 : sums.shape[0], 0 : sums.shape[1]]
    cy, cx = (sums.shape[0] - 1) / 2, (sums.shape[1] - 1) / 2
    dist = np.hypot((xs - cx) / max(1, sums.shape[1]), (ys - cy) / max(1, sums.shape[0]))
    score = sums * (1 - 0.15 * dist)
    iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
    x = min(im.width - crop_w, max(0, round(ix / scale)))
    y = min(im.height - crop_h, max(0, round(iy / scale)))
    return int(x), int(y)


def _keep_boxes(
    origin: tuple[int, int], im: Image.Image, crop_w: int, crop_h: int, boxes: Iterable[Box]
) -> tuple[int, int]:
    """Shift a crop window so it contains every (non-tiled) layout box, when they fit."""
    placed = [b for b in boxes if not b.get("tiled")]
    if not placed:
        return origin
    W, H = im.size
    ux0 = min(b["box"][0] for b in placed) * W
    uy0 = min(b["box"][1] for b in placed) * H
    ux1 = max(b["box"][0] + b["box"][2] for b in placed) * W
    uy1 = max(b["box"][1] + b["box"][3] for b in placed) * H
    x, y = origin
    if ux1 - ux0 <= crop_w:
        x = min(max(x, math.ceil(ux1) - crop_w), math.floor(ux0))
    if uy1 - uy0 <= crop_h:
        y = min(max(y, math.ceil(uy1) - crop_h), math.floor(uy0))
    return min(max(0, x), W - crop_w), min(max(0, y), H - crop_h)


def focus_origin(
    im: Image.Image, crop_w: int, crop_h: int, focus: str, boxes: Iterable[Box] = ()
) -> tuple[int, int]:
    """Top-left of a crop_w x crop_h window. `attention` also keeps layout boxes in frame."""
    free_x, free_y = im.width - crop_w, im.height - crop_h
    if focus == "attention":
        return _keep_boxes(_attention_origin(im, crop_w, crop_h), im, crop_w, crop_h, boxes)
    x = {"left": 0, "right": free_x}.get(focus, free_x // 2)
    y = {"top": 0, "bottom": free_y}.get(focus, free_y // 2)
    return x, y


def fit_aspect(width: int, height: int, aspect: float) -> tuple[int, int]:
    """Largest (w, h) inside width x height with w / h == aspect."""
    if width / height > aspect:
        return max(1, round(height * aspect)), height
    return width, max(1, round(width / aspect))
