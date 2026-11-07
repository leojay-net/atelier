"""Video ops (docs/03 §3.4). Each one contributes filters to the fused ffmpeg graph.

A video op is a *builder*: it receives the `Graph` (with the state of the video so far) and its
validated params, and adds filters. It never runs ffmpeg; the runner fuses adjacent builders and
encodes once (`worker.run_graph_job`). Time-range and size problems are reported from here, in
terms of what the previous steps produced.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Sequence
from typing import Any, Literal, TypeVar

import numpy as np
from PIL import Image
from pydantic import Field, model_validator

from atelier.av.export import av_summary
from atelier.av.graph import Graph, Source, num
from atelier.media import MediaType
from atelier.ops.registry import Artifact, Determinism, OpError, Params, op
from atelier.spec.types import Aspect, Fraction, Timecode

B = TypeVar("B", bound=Callable[[Graph, Any], None])
RESOURCES = ("ffmpeg", "ffprobe")
Anchor = Literal[
    "top_left", "top_center", "top_right", "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
]  # fmt: skip


def video_op(
    *,
    name: str,
    version: str,
    params: type[Params],
    description: str,
    inputs: tuple[MediaType, ...] = (MediaType.VIDEO,),
    variadic: bool = False,
    check_inputs: Callable[[Any, int], str | None] | None = None,
    also: tuple[str, ...] = (),
    summary: Callable[[Artifact], str] = av_summary,
    hidden: bool = False,
    determinism: Determinism = Determinism.PERCEPTUAL,
) -> Callable[[B], B]:
    """Register a graph builder as a video op. The builder is its own worker entry point.
    `also` names further pinned resources it depends on (fonts, for ops that draw text).
    `hidden` keeps it out of the system-prompt catalogue (the token budget, docs/08 Phase 6)
    while leaving it fully callable by name and through `describe_op`. `determinism` defaults
    to PERCEPTUAL (a classical filter); an op whose builder samples a neural model (tracked
    reframing) overrides it to SEEDED_ML."""

    def decorate(builder: B) -> B:
        return op(
            name=name,
            version=version,
            inputs=inputs,
            output=MediaType.VIDEO,
            determinism=determinism,
            params=params,
            summary=summary,
            description=description,
            resources=(*RESOURCES, *also),
            resources_by_media={MediaType.IMAGE: ("pillow", "icc")},  # pictures pass through Pillow
            variadic=variadic,
            check_inputs=check_inputs,
            graph=builder,
            graph_media=inputs[:1],
            hidden=hidden,
        )(builder)

    return decorate


def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


# -- trim --------------------------------------------------------------------------------------


class TrimParams(Params):
    start: Timecode = Field(0.0, description="where the clip starts (seconds or [HH:]MM:SS[.mmm])")
    end: Timecode | None = Field(None, description="where it ends; omit to keep the rest")

    @model_validator(mode="after")
    def _ordered(self) -> TrimParams:
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"end ({self.end:g}s) must be after start ({self.start:g}s)")
        return self


@video_op(
    name="video.trim",
    version="1.0.0",
    params=TrimParams,
    description="cut a time range, frame-accurate (video and audio)",
)
def trim(g: Graph, p: TrimParams) -> None:
    """Keep only the part of the video between `start` and `end`. Cuts are frame-accurate:
    the range is applied to the decoded video, not to keyframes, and the audio is cut at the
    same instants so it stays in sync. The result starts at 0."""
    total = g.duration
    if total is not None and p.start >= total:
        raise OpError(f"trim start {p.start:g}s is past the end: the video is {total:.2f}s long")
    if total is not None and p.end is not None and p.end > total + 0.001:
        raise OpError(
            f"trim end {p.end:g}s is past the end: the video is {total:.2f}s long "
            f"(use end <= {total:.2f})"
        )
    end = p.end if p.end is not None else total
    args: dict[str, Any] = {"start": p.start}
    if p.end is not None:
        args["end"] = p.end
    g.vf("trim", args)
    g.vf("setpts", {"expr": "PTS-STARTPTS"})
    if g.has_audio:
        g.af("atrim", args)
        g.af("asetpts", {"expr": "PTS-STARTPTS"})
    g.duration = (end - p.start) if end is not None else None


# -- reframe -----------------------------------------------------------------------------------


class ReframeParams(Params):
    aspect: Aspect = Field(description="target shape as W:H (9:16, 1:1, 16:9) or a number")
    mode: Literal["fill", "fit", "blur_pad"] = Field(
        "fill",
        description="fill: crop to the shape; fit: pad with black; "
        "blur_pad: pad with a blurred copy",
    )
    focus_x: Fraction = Field(0.5, ge=0, le=1, description="fill: crop position, 0 left … 1 right")
    focus_y: Fraction = Field(0.5, ge=0, le=1, description="fill: crop position, 0 top … 1 bottom")


@video_op(
    name="video.reframe",
    version="1.0.0",
    params=ReframeParams,
    description="change the aspect ratio: crop, pad, or blurred pad",
)
def reframe(g: Graph, p: ReframeParams) -> None:
    """Change the shape of the video to `aspect`. `fill` crops (choose the crop position with
    focus_x/focus_y), `fit` adds black bars, `blur_pad` fills the bars with a blurred, enlarged
    copy of the video (the usual way to turn landscape into vertical without cropping)."""
    w, h, a = g.width, g.height, p.aspect
    if p.mode == "fill":
        nw, nh = (_even(h * a), h) if w / h > a else (w, _even(w / a))
        g.vf(
            "crop",
            {"w": nw, "h": nh, "x": f"(iw-ow)*{num(p.focus_x)}", "y": f"(ih-oh)*{num(p.focus_y)}"},
        )
    else:
        nw, nh = (w, _even(w / a)) if w / h > a else (_even(h * a), h)
        if p.mode == "fit":
            g.vf("pad", {"w": nw, "h": nh, "x": "(ow-iw)/2", "y": "(oh-ih)/2", "color": "black"})
        else:
            assert g.video is not None
            back, front = g.add("split", {"outputs": 2}, ins=[g.video], outs=2, prefix="v")
            (back,) = g.add(
                "scale",
                {"w": nw, "h": nh, "force_original_aspect_ratio": "increase", "flags": "bilinear"},
                ins=[back],
                prefix="v",
            )
            (back,) = g.add("crop", {"w": nw, "h": nh}, ins=[back], prefix="v")
            (back,) = g.add("boxblur", {"luma_radius": 24, "luma_power": 2}, ins=[back], prefix="v")
            (front,) = g.add(
                "scale",
                {"w": nw, "h": nh, "force_original_aspect_ratio": "decrease", "flags": "lanczos"},
                ins=[front],
                prefix="v",
            )
            (g.video,) = g.add(
                "overlay", {"x": "(W-w)/2", "y": "(H-h)/2"}, ins=[back, front], prefix="v"
            )
    g.width, g.height = nw, nh


# -- crop --------------------------------------------------------------------------------------


class VideoCropParams(Params):
    box: tuple[float, float, float, float] = Field(
        description="crop rectangle [left, top, width, height] as fractions 0..1 of the frame"
    )

    @model_validator(mode="after")
    def _valid_box(self) -> VideoCropParams:
        left, top, width, height = self.box
        if not (0 <= left < 1 and 0 <= top < 1 and 0 < width <= 1 and 0 < height <= 1):
            raise ValueError(f"box values must be within (0, 1], got {self.box}")
        if left + width > 1.001 or top + height > 1.001:
            raise ValueError(f"box reaches outside frame: {left + width:.3f}w, {top + height:.3f}h")
        return self


@video_op(
    name="video.crop",
    version="1.0.0",
    params=VideoCropParams,
    description="crop video to a bounding box [left, top, width, height] in fractions",
)
def crop(g: Graph, p: VideoCropParams) -> None:
    """Crop the video to the bounding box [left, top, width, height], where each value is a
    fraction (0..1) of the input frame. Output width and height are rounded to even numbers."""
    left, top, width, height = p.box
    cw = _even(round(g.width * width))
    ch = _even(round(g.height * height))
    cx = _even(round(g.width * left))
    cy = _even(round(g.height * top))
    if cw < 2 or ch < 2:
        raise OpError(f"crop dimensions too small: {cw}x{ch}")
    g.vf("crop", {"w": cw, "h": ch, "x": cx, "y": cy})
    g.width, g.height = cw, ch


# -- speed -------------------------------------------------------------------------------------


class SpeedParams(Params):
    factor: float = Field(
        ge=0.25, le=4.0, description="2 = twice as fast, 0.5 = half, 0.25 = quarter-speed slow-mo"
    )


def _atempo_chain(factor: float) -> list[float]:
    """atempo only accepts 0.5–2.0 per stage, so chain stages for larger changes."""
    stages = []
    while factor > 2.0:
        stages.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        stages.append(0.5)
        factor /= 0.5
    return [*stages, factor]


@video_op(
    name="video.speed",
    version="1.0.0",
    params=SpeedParams,
    description="speed up or slow down; audio pitch is preserved",
)
def speed(g: Graph, p: SpeedParams) -> None:
    """Change playback speed by `factor`. Audio keeps its pitch, and the frame rate stays what
    it was (slowing down repeats frames, speeding up drops them), so slow motion is not
    choppy at a halved rate. The duration becomes duration / factor."""
    fps = g.fps
    g.vf("setpts", {"expr": f"PTS/{num(p.factor)}"})
    g.vf("fps", {"fps": fps})
    if g.has_audio:
        for stage in _atempo_chain(p.factor):
            g.af("atempo", {"tempo": stage})
    if g.duration is not None:
        g.duration /= p.factor


# -- fade --------------------------------------------------------------------------------------


class FadeParams(Params):
    fade_in: Timecode = Field(
        0.0, description="seconds of fade from black (and silence) at the start"
    )
    fade_out: Timecode = Field(0.0, description="seconds of fade to black (and silence) at the end")

    @model_validator(mode="after")
    def _something(self) -> FadeParams:
        if self.fade_in <= 0 and self.fade_out <= 0:
            raise ValueError("set fade_in and/or fade_out to more than 0 seconds")
        return self


@video_op(
    name="video.fade",
    version="1.0.0",
    params=FadeParams,
    description="fade in from and/or out to black, with the audio",
)
def fade(g: Graph, p: FadeParams) -> None:
    """Fade the picture (and the sound) in at the start and/or out at the end."""
    total = g.duration
    if p.fade_out > 0 and total is None:
        raise OpError("fade_out needs a known duration; trim the video first")
    if total is not None and p.fade_in + p.fade_out > total + 0.001:
        raise OpError(
            f"fades ({p.fade_in + p.fade_out:g}s) are longer than the video ({total:.2f}s)"
        )
    if p.fade_in > 0:
        g.vf("fade", {"t": "in", "st": 0, "d": p.fade_in})
        if g.has_audio:
            g.af("afade", {"t": "in", "st": 0, "d": p.fade_in})
    if p.fade_out > 0 and total is not None:
        start = total - p.fade_out
        g.vf("fade", {"t": "out", "st": start, "d": p.fade_out})
        if g.has_audio:
            g.af("afade", {"t": "out", "st": start, "d": p.fade_out})


# -- overlay -----------------------------------------------------------------------------------


class OverlayParams(Params):
    anchor: Anchor = Field("top_right", description="where the image sits")
    margin: Fraction = Field(
        0.04, ge=0, le=0.5, description="gap from the edge, as a fraction of the width"
    )
    scale: Fraction = Field(
        0.15, gt=0, le=1, description="image width as a fraction of the video width"
    )
    opacity: Fraction = Field(1.0, gt=0, le=1, description="1 is opaque")
    start: Timecode = Field(0.0, description="when it appears (seconds)")
    end: Timecode | None = Field(
        None, description="when it disappears; omit for the rest of the video"
    )
    move_to: Anchor | None = Field(
        None,
        description="glide to this anchor between start and end (a keyframe: needs a time range)",
    )
    shape: Literal["rect", "circle", "rounded"] = Field(
        "rect", description="cut the picture to a circle or rounded corners"
    )
    fade: Timecode = Field(
        0.0, ge=0, le=5, description="seconds it fades in at start and out at end (needs an end)"
    )
    blend: Literal["normal", "screen", "multiply", "overlay", "addition"] = Field(
        "normal", description="how it mixes with the picture (screen for light leaks, multiply…)"
    )
    scale_to: Fraction | None = Field(
        None, gt=0, le=1, description="grow or shrink to this width fraction by end (a keyframe)"
    )
    spin: float = Field(
        0.0, ge=-720, le=720, description="degrees it turns between start and end (a keyframe)"
    )

    @model_validator(mode="after")
    def _ordered(self) -> OverlayParams:
        if self.move_to is not None and self.end is None:
            raise ValueError("move_to needs an end time: the glide lasts from start to end")
        if self.fade > 0 and self.end is None:
            raise ValueError("fade needs an end time: it fades out there")
        if (self.scale_to is not None or self.spin) and self.end is None:
            raise ValueError("scale_to and spin need an end time: they last from start to end")
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"end ({self.end:g}s) must be after start ({self.start:g}s)")
        return self


_ANCHOR = {
    "top_left": (0, 0), "top_center": (0.5, 0), "top_right": (1, 0),
    "center_left": (0, 0.5), "center": (0.5, 0.5), "center_right": (1, 0.5),
    "bottom_left": (0, 1), "bottom_center": (0.5, 1), "bottom_right": (1, 1),
}  # fmt: skip


# The platform UI zones of the vertical presets (reels/tiktok/shorts_1080x1920 `safe_area`), as
# fractions of the frame. A logo anchored in a corner of a vertical video would otherwise sit
# under the app's buttons and always fail the safe_margins check.
_SOCIAL_SAFE = {"top": 0.14, "bottom": 0.20, "left": 0.06, "right": 0.06}


def _inside_social_safe_area(x: int, y: int, w: int, h: int, W: int, H: int) -> tuple[int, int]:
    """Move a box the least distance that puts it inside the vertical presets' safe area
    (if it fits; a box bigger than the area keeps its position)."""
    pad = 0.01  # a little inside the line: rounding and a later upscale must not cross it
    lo_x = math.ceil(W * (_SOCIAL_SAFE["left"] + pad))
    hi_x = math.floor(W * (1 - _SOCIAL_SAFE["right"] - pad)) - w
    lo_y = math.ceil(H * (_SOCIAL_SAFE["top"] + pad))
    hi_y = math.floor(H * (1 - _SOCIAL_SAFE["bottom"] - pad)) - h
    if lo_x <= hi_x:
        x = min(max(x, lo_x), hi_x)
    if lo_y <= hi_y:
        y = min(max(y, lo_y), hi_y)
    return x, y


@video_op(
    name="video.overlay",
    version="1.1.0",
    params=OverlayParams,
    inputs=(MediaType.VIDEO, MediaType.IMAGE),
    description="place an image (logo) on the video, optionally for a time range",
)
def overlay(g: Graph, p: OverlayParams) -> None:
    """Place the second input (an image, usually a logo) on the video at an anchor, with a
    margin, size, opacity and an optional time range. The margin is measured from the video
    edges, so the same logo lands in the same relative spot on any frame size."""
    logo = g.extra(0)
    iw, ih = logo.meta["size"]
    ow = _even(g.width * p.scale)
    oh = _even(ow * ih / iw)
    margin = round(g.width * p.margin)
    fx, fy = _ANCHOR[p.anchor]
    x = round(fx * (g.width - ow - 2 * margin)) + margin
    y = round(fy * (g.height - oh - 2 * margin)) + margin
    if g.height > g.width:  # vertical: stay clear of the Reels / TikTok / Shorts UI zones
        x, y = _inside_social_safe_area(x, y, ow, oh, g.width, g.height)
    blended = p.blend != "normal"
    if blended:  # the picture covers the whole frame and mixes with it
        ow, oh, x, y = g.width, g.height, 0, 0
    (img,) = g.add("scale", {"w": ow, "h": oh, "flags": "lanczos"}, ins=[g.extra_label(0)])
    if p.shape != "rect":
        (img,) = g.add("format", {"pix_fmts": "rgba"}, ins=[img])
        radius = "min(W,H)/2" if p.shape == "circle" else "min(W,H)*0.2"
        inside = (
            f"lte(hypot(max(abs(X-W/2)-(W/2-{radius}),0),max(abs(Y-H/2)-(H/2-{radius}),0)),{radius})"
        )
        (img,) = g.add(
            "geq",
            {"r": "r(X,Y)", "g": "g(X,Y)", "b": "b(X,Y)", "a": f"if({inside},alpha(X,Y),0)"},
            ins=[img],
        )
    if p.opacity < 1 and not blended:
        (img,) = g.add("format", {"pix_fmts": "rgba"}, ins=[img])
        (img,) = g.add("colorchannelmixer", {"aa": p.opacity}, ins=[img])
    animated = p.end is not None and (p.scale_to is not None or p.spin != 0)
    if (p.fade > 0 or animated) and p.end is not None and g.duration is not None:
        # a still picture is one frame: repeat it for the length of the video so it can change
        fps = g.fps or 30.0
        (img,) = g.add("format", {"pix_fmts": "rgba"}, ins=[img])
        (img,) = g.add("loop", {"loop": -1, "size": 1, "start": 0}, ins=[img])
        (img,) = g.add("setpts", {"expr": f"N/({num(fps)}*TB)"}, ins=[img])
        (img,) = g.add("trim", {"end": num(g.duration)}, ins=[img])
        progress = f"clip((t-{num(p.start)})/{num(p.end - p.start)},0,1)"
        if p.scale_to is not None:
            k = f"(1+({num(p.scale_to / p.scale)}-1)*{progress})"
            (img,) = g.add(
                "scale", {"w": f"max(2,iw*{k})", "h": f"max(2,ih*{k})", "eval": "frame"}, ins=[img]
            )
        if p.spin:
            (img,) = g.add(
                "rotate",
                {"a": f"{num(p.spin)}*PI/180*{progress}", "ow": "hypot(iw,ih)", "oh": "hypot(iw,ih)",
                 "c": "none"},
                ins=[img],
            )  # fmt: skip
        if p.fade > 0:
            fd = min(p.fade, (p.end - p.start) / 2)
            (img,) = g.add(
                "fade", {"t": "in", "st": num(p.start), "d": num(fd), "alpha": 1}, ins=[img]
            )
            (img,) = g.add(
                "fade", {"t": "out", "st": num(p.end - fd), "d": num(fd), "alpha": 1}, ins=[img]
            )
    args: dict[str, Any] = {"x": x, "y": y}
    if animated and p.end is not None:  # the size changes: place by the middle of the anchor
        fx0, fy0 = _ANCHOR[p.anchor]
        fx1, fy1 = _ANCHOR[p.move_to] if p.move_to else (fx0, fy0)
        k = f"clip((t-{num(p.start)})/{num(p.end - p.start)},0,1)"
        fx, fy = f"({fx0}+({fx1}-{fx0})*{k})", f"({fy0}+({fy1}-{fy0})*{k})"
        m = margin
        args = {"x": f"(W-w)*{fx}+{m}*(1-2*{fx})", "y": f"(H-h)*{fy}+{m}*(1-2*{fy})", "eval": "frame"}
    elif p.move_to is not None and p.end is not None:
        tx, ty = _ANCHOR[p.move_to]
        x2 = round(tx * (g.width - ow - 2 * margin)) + margin
        y2 = round(ty * (g.height - oh - 2 * margin)) + margin
        k = f"clip((t-{num(p.start)})/{num(p.end - p.start)},0,1)"
        args = {"x": f"{x}+({x2}-{x})*{k}", "y": f"{y}+({y2}-{y})*{k}", "eval": "frame"}
    if p.start > 0 or p.end is not None:
        window = (
            f"gte(t,{num(p.start)})" if p.end is None else f"between(t,{num(p.start)},{num(p.end)})"
        )
        args["enable"] = window
    assert g.video is not None
    if blended:
        mode = {"overlay": "overlay", "screen": "screen", "multiply": "multiply", "addition": "addition"}[p.blend]
        bargs: dict[str, Any] = {"all_mode": mode, "all_opacity": p.opacity}
        if "enable" in args:
            bargs["enable"] = args["enable"]
        (img,) = g.add("format", {"pix_fmts": "yuva420p"}, ins=[img])
        (g.video,) = g.add("blend", bargs, ins=[g.video, img], prefix="v")
    else:
        (g.video,) = g.add("overlay", args, ins=[g.video, img], prefix="v")
    boxes = g.notes.setdefault("boxes", [])
    boxes.append(
        {
            "kind": "overlay",
            "label": "logo",
            "box": [x / g.width, y / g.height, ow / g.width, oh / g.height],
            "t": [p.start, p.end],
        }
    )


# -- color -------------------------------------------------------------------------------------


class ColorParams(Params):
    brightness: float = Field(0.0, ge=-1.0, le=1.0, description="-1 darker … 1 brighter")
    contrast: float = Field(1.0, ge=0.0, le=3.0, description="1 unchanged; above 1 stronger")
    saturation: float = Field(1.0, ge=0.0, le=3.0, description="0 grayscale, 1 unchanged")
    gamma: float = Field(1.0, ge=0.1, le=3.0, description="above 1 lifts the midtones")

    @model_validator(mode="after")
    def _something(self) -> ColorParams:
        if (self.brightness, self.contrast, self.saturation, self.gamma) == (0.0, 1.0, 1.0, 1.0):
            raise ValueError("nothing to change: every value is at its neutral setting")
        return self


@video_op(
    name="video.color",
    version="1.0.0",
    params=ColorParams,
    description="adjust brightness, contrast, saturation and gamma of the picture",
)
def color(g: Graph, p: ColorParams) -> None:
    """Adjust the picture: `brightness` shifts it, `contrast` stretches it around the middle,
    `saturation` scales color (0 is black and white) and `gamma` bends the midtones. The sound
    is untouched. Values are neutral at 0 / 1 / 1 / 1."""
    g.vf(
        "eq",
        {
            "brightness": p.brightness,
            "contrast": p.contrast,
            "saturation": p.saturation,
            "gamma": p.gamma,
        },
    )


# -- mute --------------------------------------------------------------------------------------


class MuteParams(Params):
    pass


@video_op(
    name="video.mute",
    version="1.0.0",
    params=MuteParams,
    description="remove the sound from a video",
)
def mute(g: Graph, p: MuteParams) -> None:
    """Drop the audio track. The picture is untouched and the file has no sound at all (which is
    not the same as a silent track); use `video.soundtrack` to give it music."""
    del p
    g.audio = None
    g.sample_rate = g.channels = 0


# -- stabilize -----------------------------------------------------------------------------------


class StabilizeParams(Params):
    shakiness: int = Field(
        5, ge=1, le=10, description="how shaky the source is; higher tries harder and is slower"
    )
    smoothing: float = Field(
        0.5,
        ge=0,
        le=5,
        description="seconds of motion to average out; higher is steadier but drifts",
    )
    zoom: Literal["auto", "keep"] = Field(
        "auto",
        description="auto: zoom in just enough to hide the wobbling border; "
        "keep: do not crop, the border may show",
    )


@video_op(
    name="video.stabilize",
    version="1.0.0",
    params=StabilizeParams,
    description="smooth out shaky handheld footage",
    hidden=True,  # catalogue budget (docs/08 Phase 6): the least common of the new video ops;
    # still fully callable by name, and describe_op("video.stabilize") still works
)
def stabilize(g: Graph, p: StabilizeParams) -> None:
    """Smooth out camera shake. `shakiness` says how much correction to look for (and costs
    time: 10 analyzes much harder than 1), `smoothing` is how many seconds of motion are
    averaged into each frame's correction (more is steadier but can drift on a slow pan).
    `zoom: auto` (the default) zooms in a little, frame by frame, so the corrected edges never
    show; `keep` does not crop at all, so a corrected frame's edge may wobble into view. This
    is a full second pass over the video (an analysis decode before the real encode), so it
    takes about twice as long as an op that only touches one pass."""
    assert g.video is not None
    if g.duration is not None and g.duration < 1.0:
        raise OpError(f"the video is only {g.duration:.2f}s long: too short to analyze for shake")
    trf = f"{g.label('vidstab')}.trf"
    accuracy = min(15, p.shakiness + 5)
    g.stabilize_detect(g.video, shakiness=p.shakiness, accuracy=accuracy, result=trf)
    frames = max(0, round(p.smoothing * (g.fps or 30.0)))
    args: dict[str, Any] = {"input": trf, "smoothing": frames}
    if p.zoom == "auto":
        args["optzoom"] = 2  # dynamic per-frame zoom: always just enough to hide the border
    else:
        args["optzoom"] = 0
        args["crop"] = "keep"  # no cropping; the frame keeps its size, the border may show
    g.vf("vidstabtransform", args)


# -- auto_reframe --------------------------------------------------------------------------------

TRACK_MAX_SECONDS = 600.0  # only a few dozen small frames are sampled, so length is cheap
TRACK_SAMPLE_WIDTH = 160  # px; plenty to find roughly where the subject is
TRACK_MAX_SAMPLES = 40  # caps both the sampling cost and the generated expression's size
TRACK_MIN_INTERVAL = 0.2  # seconds; a floor on the sampling rate for very short clips


class AutoReframeParams(Params):
    aspect: Aspect = Field(description="target shape as W:H (9:16, 1:1, 16:9) or a number")
    smoothing: float = Field(
        1.0, ge=0.2, le=5, description="seconds of motion averaged into the camera's path"
    )


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _smooth_path(points: list[tuple[float, float]], window: int) -> list[tuple[float, float]]:
    """A short centered moving average, so the crop does not jitter with every sample."""
    n = len(points)
    if n <= 2 or window <= 1:
        return points
    half = window // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        xs = [points[j][0] for j in range(lo, hi)]
        ys = [points[j][1] for j in range(lo, hi)]
        out.append((sum(xs) / len(xs), sum(ys) / len(ys)))
    return out


def _piecewise_expr(times: Sequence[float], values: Sequence[float]) -> str:
    """A time expression that linearly interpolates `values` at `times`, holding the first and
    last value outside that range. Commas inside `if(...)` are plain; the graph's own escaper
    (used wherever this string is handed to a filter option) backslash-escapes them."""
    if len(times) == 1:
        return num(values[0])
    expr = num(values[-1])
    for i in range(len(times) - 1, 0, -1):
        t0, t1, v0, v1 = times[i - 1], times[i], values[i - 1], values[i]
        if t1 - t0 < 1e-6:
            seg = num(v1)
        else:
            slope = (v1 - v0) / (t1 - t0)
            seg = f"({num(v0)}+{num(slope)}*(t-{num(t0)}))"
        expr = f"if(lt(t,{num(t1)}),{seg},{expr})"
    return f"if(lt(t,{num(times[0])}),{num(values[0])},{expr})"


@video_op(
    name="video.auto_reframe",
    version="1.0.0",
    params=AutoReframeParams,
    description="crop to a new aspect, tracking the main subject",
    also=("ml_runtime", "model_u2netp"),
    determinism=Determinism.SEEDED_ML,
)
def auto_reframe(g: Graph, p: AutoReframeParams) -> None:
    """Change the shape of the video to `aspect` like `video.reframe mode=fill`, but instead of
    a fixed crop point, a small neural network finds the main subject every fraction of a
    second and the crop follows it, smoothed over `smoothing` seconds so it pans instead of
    jumping. Works best with one clear subject; a busy scene or a subject that already fills
    the frame may not move the crop much. This samples the video once before the real encode,
    at a low resolution, so it costs little beyond the sampling time itself regardless of the
    video's length (up to the limit below)."""
    from atelier.ml.segment import subject_mask

    assert g.video is not None
    if g.duration is None:
        raise OpError("the video's length is not known here, so the subject cannot be tracked")
    if g.duration > TRACK_MAX_SECONDS:
        raise OpError(
            f"video.auto_reframe is limited to {TRACK_MAX_SECONDS:g}s clips; this one is "
            f"{g.duration:.1f}s (trim it first, then reframe)"
        )
    w, h, a = g.width, g.height, p.aspect
    nw, nh = (_even(h * a), h) if w / h > a else (w, _even(w / a))
    interval = max(TRACK_MIN_INTERVAL, g.duration / TRACK_MAX_SAMPLES)
    sample_fps = 1.0 / interval
    frames, fw, fh = g.sample_frames(g.video, fps=sample_fps, width=TRACK_SAMPLE_WIDTH)
    if not frames:
        raise OpError("could not sample any frames to find the subject to track")
    centroids: list[tuple[float, float]] = []
    for raw in frames:
        mask = subject_mask(Image.frombytes("RGB", (fw, fh), raw))
        arr = np.asarray(mask, dtype=np.float64)
        total = float(arr.sum())
        if total < 1.0:  # nothing salient found in this frame: hold the frame's center
            centroids.append((0.5, 0.5))
            continue
        ys, xs = np.indices(arr.shape)
        centroids.append(
            (float((arr * xs).sum() / total) / fw, float((arr * ys).sum() / total) / fh)
        )
    times = [i / sample_fps for i in range(len(frames))]
    window = max(1, round(p.smoothing * sample_fps))
    centroids = _smooth_path(centroids, window)
    xs_px = [_clamp(cx * w - nw / 2, 0, w - nw) for cx, _ in centroids]
    ys_px = [_clamp(cy * h - nh / 2, 0, h - nh) for _, cy in centroids]
    g.vf(
        "crop",
        {"w": nw, "h": nh, "x": _piecewise_expr(times, xs_px), "y": _piecewise_expr(times, ys_px)},
    )
    g.width, g.height = nw, nh


# -- reverse, freeze, chroma key ---------------------------------------------------------------

MAX_REVERSE_SECONDS = 60.0  # ffmpeg holds the whole clip in memory to play it backwards


class ReverseParams(Params):
    pass


@video_op(
    name="video.reverse",
    version="1.0.0",
    params=ReverseParams,
    description="play the video (and its sound) backwards",
)
def reverse(g: Graph, p: ReverseParams) -> None:
    """Play the clip backwards. Limited to short clips because the whole clip is held in memory."""
    del p
    if g.duration is None or g.duration > MAX_REVERSE_SECONDS:
        raise OpError(f"only clips of up to {MAX_REVERSE_SECONDS:g}s can be reversed: trim it first")
    g.vf("reverse")
    if g.has_audio:
        g.af("areverse")


class FreezeParams(Params):
    at: Timecode = Field(description="the moment to hold (seconds)")
    seconds: Timecode = Field(2.0, gt=0, le=10, description="how long to hold it")


@video_op(
    name="video.freeze",
    version="1.0.0",
    params=FreezeParams,
    description="hold one frame of the video still for a few seconds",
)
def freeze(g: Graph, p: FreezeParams) -> None:
    """Stop the picture on the frame at `at` for `seconds`, then carry on. The video gets that
    much longer; its sound pauses (silence) for the same time."""
    total = g.duration
    if total is None or p.at >= total:
        raise OpError("`at` is past the end of the video")
    fps = g.fps or 30.0
    g.vf("loop", {"loop": max(1, round(p.seconds * fps)), "size": 1, "start": round(p.at * fps)})
    if g.has_audio:
        assert g.audio is not None
        head, tail = g.add("asplit", {"outputs": 2}, ins=[g.audio], outs=2, prefix="a")
        (head,) = g.add("atrim", {"end": num(p.at)}, ins=[head], prefix="a")
        (head,) = g.add("asetpts", {"expr": "PTS-STARTPTS"}, ins=[head], prefix="a")
        (tail,) = g.add("atrim", {"start": num(p.at)}, ins=[tail], prefix="a")
        (tail,) = g.add("asetpts", {"expr": "PTS-STARTPTS"}, ins=[tail], prefix="a")
        (quiet,) = g.add(
            "anullsrc",
            {"r": g.sample_rate or 48000, "cl": "stereo", "d": num(p.seconds)},
            ins=[],
            prefix="a",
        )
        (g.audio,) = g.add("concat", {"n": 3, "v": 0, "a": 1}, ins=[head, quiet, tail], prefix="a")
    g.duration = total + p.seconds


class ChromaKeyParams(Params):
    color: str = Field(
        "#00ff00", pattern=r"^#[0-9a-fA-F]{6}$", description="the colour to remove (green screen)"
    )
    similarity: Fraction = Field(0.3, gt=0, le=1, description="how close a colour must be to go")
    blend: Fraction = Field(0.1, ge=0, le=1, description="softens the edge of what is kept")


@video_op(
    name="video.chromakey",
    version="1.0.0",
    params=ChromaKeyParams,
    inputs=(MediaType.VIDEO, MediaType.IMAGE),
    description="remove a green screen and put the second input (a picture) behind the subject",
)
def chromakey(g: Graph, p: ChromaKeyParams) -> None:
    """Key out `color` from the video and show the second input, scaled to fill the frame,
    behind what is left. The video's sound is kept."""
    assert g.video is not None
    width, height = g.width, g.height
    (bg,) = g.add(
        "scale",
        {"w": width, "h": height, "force_original_aspect_ratio": "increase"},
        ins=[g.extra_label(0)],
    )
    (bg,) = g.add("crop", {"w": width, "h": height}, ins=[bg])
    (fg,) = g.add(
        "chromakey",
        {"color": "0x" + p.color[1:], "similarity": p.similarity, "blend": p.blend},
        ins=[g.video],
        prefix="v",
    )
    (g.video,) = g.add("overlay", {"shortest": 1}, ins=[bg, fg], prefix="v")
    g.origin = None


# -- mask: keep a shape of the picture --------------------------------------------------------


class MaskParams(Params):
    shape: Literal["circle", "rounded", "diamond"] = Field(
        "circle", description="the part of the picture that stays: a circle, rounded box or diamond"
    )
    inset: Fraction = Field(0.0, ge=0, le=0.6, description="shrink the shape from the edges")
    feather: Fraction = Field(0.03, ge=0, le=0.5, description="softness of the shape's edge")
    background: Literal["color", "blur"] = Field(
        "color", description="what shows outside the shape: a colour, or the picture blurred"
    )
    color: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$", description="the plain colour")


@video_op(
    name="video.mask",
    version="1.0.0",
    params=MaskParams,
    description="show only a circle, rounded box or diamond of the picture",
)
def mask(g: Graph, p: MaskParams) -> None:
    """Keep a shape of the picture and fill the rest with a colour or a blurred copy. Pure
    filters (no model): a per-pixel alpha from the distance to the middle."""
    assert g.video is not None
    total = g.duration
    if total is None:
        raise OpError("the video's length is not known here, so the mask cannot be applied")
    feather = max(p.feather, 0.004)
    # the shape is drawn once, as a grey picture (white = kept), not worked out on every frame
    yy, xx = np.mgrid[0 : g.height, 0 : g.width].astype(np.float32)
    half_x, half_y = g.width / 2, g.height / 2
    if p.shape == "circle":  # a true circle: as wide as the short side
        half_x = half_y = min(half_x, half_y)
    u = (xx + 0.5 - g.width / 2) / (half_x * (1 - p.inset))
    v = (yy + 0.5 - g.height / 2) / (half_y * (1 - p.inset))
    if p.shape == "circle":
        edge = 1 - np.hypot(u, v)
    elif p.shape == "diamond":
        edge = 1 - (np.abs(u) + np.abs(v))
    else:  # rounded box: distance from the inner box, in units of the corner radius
        r = 0.3
        edge = 1 - np.hypot(np.maximum(np.abs(u) - (1 - r), 0), np.maximum(np.abs(v) - (1 - r), 0)) / r
    alpha = np.clip(edge / feather, 0, 1)
    shape_png = io.BytesIO()
    Image.fromarray(np.rint(alpha * 255).astype(np.uint8), "L").save(shape_png, "PNG")
    name = g.write_file(f"mask{len(g.sources)}.png", shape_png.getvalue())
    index = g.add_source(Source(g.work / name, MediaType.IMAGE, ".png", {"size": [g.width, g.height]}))
    if p.background == "blur":
        front, back = g.add("split", {"outputs": 2}, ins=[g.video], outs=2, prefix="v")
        (back,) = g.add("gblur", {"sigma": 24}, ins=[back], prefix="v")
    else:
        front = g.video
        (back,) = g.add(
            "color",
            {"c": "0x" + p.color[1:], "s": f"{g.width}x{g.height}", "r": num(g.fps or 30.0),
             "d": num(total)},
            ins=[], prefix="v",
        )  # fmt: skip
    (front,) = g.add("format", {"pix_fmts": "rgba"}, ins=[front], prefix="v")
    (shape,) = g.add("format", {"pix_fmts": "gray"}, ins=[g.source_label(index)], prefix="v")
    (front,) = g.add("alphamerge", {}, ins=[front, shape], prefix="v")
    (g.video,) = g.add("overlay", {"shortest": 1, "format": "auto"}, ins=[back, front], prefix="v")
    g.origin = None
