"""Image checks. They read the metadata written by image ops and `export`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from atelier.checks import CheckResult, Status, check
from atelier.fmt import human_bytes
from atelier.media import MediaType

IMAGE = MediaType.IMAGE
VIDEO = MediaType.VIDEO
TOLERANCE = 1e-4


def _export(meta: Mapping[str, Any]) -> Mapping[str, Any] | None:
    export = meta.get("export")
    return export if isinstance(export, Mapping) else None


def _placed(meta: Mapping[str, Any], kinds: tuple[str, ...]) -> list[Mapping[str, Any]]:
    return [b for b in meta.get("boxes", []) if b.get("kind") in kinds and not b.get("tiled")]


def _ok(name: str) -> CheckResult:
    return CheckResult(name, Status.PASS)


@check("dimensions", IMAGE, "output size matches the export preset")
def dimensions(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    if not export or not export.get("size"):
        return None
    size = export["size"]
    w, h, tw, th = meta["width"], meta["height"], size["width"], size["height"]
    mode = size["mode"]
    if mode in ("fill", "exact"):
        ok = (w, h) == (tw, th)
        want = f"exactly {tw}x{th}"
    elif mode == "fit":
        ok = w <= tw and h <= th and (w == tw or h == th)
        want = f"fit to {tw}x{th}"
    else:
        ok = w <= tw and h <= th
        want = f"at most {tw}x{th}"
    if ok:
        return _ok("dimensions")
    return CheckResult("dimensions", Status.FAIL, f"{w}x{h}, preset wants {want}")


@check("file_size_budget", IMAGE, "encoded size within the preset budget")
def file_size_budget(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    if not export or not export.get("max_bytes"):
        return None
    budget, actual = export["max_bytes"], meta["bytes"]
    if actual <= budget:
        return _ok("file_size_budget")
    return CheckResult(
        "file_size_budget",
        Status.FAIL,
        f"{human_bytes(actual)} > {human_bytes(budget)} even at q{meta.get('quality')}",
        "lower overrides.min_quality, reduce overrides.size, or use web_avif",
    )


@check("color_profile", IMAGE, "sRGB ICC embedded and no unexpected alpha")
def color_profile(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    if not export:
        return None
    if (
        export.get("icc") == "srgb"
        and meta["mode"] in ("RGB", "RGBA")
        and meta.get("icc") != "sRGB"
    ):
        return CheckResult("color_profile", Status.FAIL, "sRGB profile missing")
    if not export.get("alpha") and meta.get("has_alpha"):
        return CheckResult(
            "color_profile",
            Status.FAIL,
            "output has alpha; preset forbids it",
            'insert image.convert {alpha: "flatten"}',
        )
    return _ok("color_profile")


@check("text_fit", IMAGE, "text fits its box and is not cut off", default=True)
def text_fit(meta: Mapping[str, Any]) -> CheckResult | None:
    boxes = _placed(meta, ("text",))
    if not boxes:
        return None
    for b in boxes:
        if not b.get("fit", True):
            return CheckResult(
                "text_fit",
                Status.FAIL,
                f"{b['label']!r} overflows its box at {b.get('font_px')}px",
                "shorten the text, enlarge args.box, or lower args.size",
            )
        if b.get("clipped"):
            return CheckResult(
                "text_fit",
                Status.FAIL,
                f"{b['label']!r} is cut off by a later crop/resize",
                "use focus=attention (keeps text in frame) or place the text after resizing",
            )
    return _ok("text_fit")


@check("text_contrast", IMAGE, "WCAG contrast of text vs its background", default=True)
def text_contrast(meta: Mapping[str, Any]) -> CheckResult | None:
    boxes = _placed(meta, ("text",))
    if not boxes:
        return None
    worst = min(boxes, key=lambda b: b.get("contrast", 21) / b.get("min_contrast", 4.5))
    ratio, minimum = worst.get("contrast", 21), worst.get("min_contrast", 4.5)
    if ratio >= minimum:
        return _ok("text_contrast")
    return CheckResult(
        "text_contrast",
        Status.WARN,
        f"{worst['label']!r} {ratio}:1 (min {minimum})",
        'set background="auto", shadow=true, or stroke=0.06 on that image.text step',
    )


def _frame_size(meta: Mapping[str, Any]) -> tuple[int, int]:
    """The displayed size of an image or a video output."""
    video = meta.get("video")
    if isinstance(video, Mapping) and video.get("display"):
        return int(video["display"][0]), int(video["display"][1])
    return int(meta["width"]), int(meta["height"])


@check(
    "safe_margins",
    (IMAGE, VIDEO),
    "text, captions and overlays inside the preset safe area",
    default=True,
)
def safe_margins(meta: Mapping[str, Any]) -> CheckResult | None:
    export = _export(meta)
    area = export.get("safe_area") if export else None
    boxes = _placed(meta, ("text", "overlay", "watermark", "captions"))
    if not area or not boxes:
        return None
    W, H = _frame_size(meta)
    for b in boxes:
        x, y, w, h = b["box"]
        overs = {
            "left": area["left"] - x,
            "top": area["top"] - y,
            "right": (x + w) - (1 - area["right"]),
            "bottom": (y + h) - (1 - area["bottom"]),
        }
        side, amount = max(overs.items(), key=lambda kv: kv[1])
        if amount > TOLERANCE:
            px = round(amount * (W if side in ("left", "right") else H))
            return CheckResult(
                "safe_margins",
                Status.FAIL,
                f"{b['kind']} {b['label']!r} enters the {side} safe margin by {px}px",
                f"move or shrink it away from the {side} edge (anchor/margin/box)",
            )
    return _ok("safe_margins")
