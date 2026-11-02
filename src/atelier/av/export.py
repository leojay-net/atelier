"""Video encoding for `export` (docs/03 §3.4, §3.5): the settings model and the graph builder.

`export` is the only place output encoding happens. For video it adds the final scale / pad /
fps / pixel-format filters to the fused graph and sets the encoder arguments from a versioned
preset, so the whole pipeline is decoded once and encoded once.

Determinism: x264 gets an explicit thread count (part of the preset, so part of the cache
key), filter threading is pinned in `Graph.argv`, and no metadata or timestamps are written.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from atelier.av.graph import Encode, Graph, num
from atelier.fmt import human_bytes
from atelier.ops.registry import Artifact, OpError, Params
from atelier.spec.types import Fraction

KIND = "export.video"
EVEN = 2  # H.264 4:2:0 needs even dimensions


class VideoSize(Params):
    width: int = Field(ge=2, le=7680)
    height: int = Field(ge=2, le=7680)
    mode: Literal["max", "fill", "fit", "exact"] = Field(
        "max",
        description="max: shrink to fit inside, never enlarge; fill: cover and center-crop; "
        "fit: fit inside and pad with black; exact: stretch",
    )
    focus_x: Fraction = Field(0.5, ge=0, le=1, description="fill: crop position, 0 left … 1 right")
    focus_y: Fraction = Field(0.5, ge=0, le=1, description="fill: crop position, 0 top … 1 bottom")


class SafeArea(Params):
    top: Fraction = Field(0.0, ge=0, le=0.5)
    bottom: Fraction = Field(0.0, ge=0, le=0.5)
    left: Fraction = Field(0.0, ge=0, le=0.5)
    right: Fraction = Field(0.0, ge=0, le=0.5)


class Loudness(Params):
    target_lufs: float = Field(ge=-40, le=-5, description="integrated loudness target, LUFS")
    true_peak_db: float = Field(-1.0, ge=-9, le=0, description="ceiling for true peak, dBTP")
    lra: float = Field(11.0, ge=1, le=50, description="loudness range target, LU")


def apply_loudness(g: Graph, target: Loudness) -> dict[str, Any] | None:
    """Two-pass EBU R128 on the graph's audio: measure the final signal, then apply exactly the
    gain that reaches the target (linear mode, so dynamics are not touched). Returns what was
    measured, or None if the audio is silent and there is nothing to normalize."""
    assert g.audio is not None
    m = g.measure_loudness(g.audio, target.target_lufs, target.true_peak_db, target.lra)
    if m is None:
        return None
    g.af(
        "loudnorm",
        {
            "I": target.target_lufs, "TP": target.true_peak_db, "LRA": target.lra,
            "measured_I": m["input_i"], "measured_TP": m["input_tp"],
            "measured_LRA": m["input_lra"], "measured_thresh": m["input_thresh"],
            "offset": m["target_offset"], "linear": True,
        },
    )  # fmt: skip
    return m


class VideoExportSettings(Params):
    container: Literal["mp4"] = "mp4"
    encoder_preset: Literal["veryfast", "fast", "medium", "slow"] = "medium"
    profile: Literal["baseline", "main", "high"] = "high"
    level: str | None = "4.1"
    crf: int = Field(21, ge=0, le=51)
    maxrate: str | None = Field(None, pattern=r"^\d+[kKmM]$")
    bufsize: str | None = Field(None, pattern=r"^\d+[kKmM]$")
    gop: int | None = Field(None, ge=1, le=600, description="keyframe interval in frames")
    threads: int = Field(4, ge=1, le=16, description="pinned, so output does not vary by machine")
    size: VideoSize | None = None
    fps: float | None = Field(None, gt=0, le=120, description="output frame rate; None keeps it")
    max_fps: float | None = Field(None, gt=0, le=120, description="cap when fps is None")
    audio: bool = True
    audio_bitrate: str = Field("128k", pattern=r"^\d+[kK]$")
    audio_rate: int = Field(48000, ge=8000, le=96000)
    audio_channels: Literal[1, 2] = 2
    loudness: Loudness | None = Field(None, description="normalize the audio to this loudness")
    max_duration: float | None = Field(None, gt=0, description="seconds; the duration check")
    safe_area: SafeArea | None = None
    checks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> VideoExportSettings:
        if self.fps is not None and self.max_fps is not None:
            raise ValueError("give fps or max_fps, not both")
        return self


def _even(x: float) -> int:
    return max(EVEN, int(x) // EVEN * EVEN)


def _size_filters(g: Graph, size: VideoSize) -> None:
    w, h = size.width, size.height
    if size.mode == "max":
        f = min(w / g.width, h / g.height, 1.0)
        if f < 1.0:
            nw, nh = _even(g.width * f), _even(g.height * f)
            g.vf("scale", {"w": nw, "h": nh, "flags": "lanczos"})
            g.width, g.height = nw, nh
    elif size.mode == "exact":
        g.vf("scale", {"w": w, "h": h, "flags": "lanczos"})
        g.width, g.height = w, h
    elif size.mode == "fit":
        g.vf(
            "scale", {"w": w, "h": h, "force_original_aspect_ratio": "decrease", "flags": "lanczos"}
        )
        g.vf("pad", {"w": w, "h": h, "x": "(ow-iw)/2", "y": "(oh-ih)/2", "color": "black"})
        g.width, g.height = w, h
    else:  # fill
        g.vf(
            "scale", {"w": w, "h": h, "force_original_aspect_ratio": "increase", "flags": "lanczos"}
        )
        g.vf(
            "crop",
            {
                "w": w,
                "h": h,
                "x": f"(iw-ow)*{num(size.focus_x)}",
                "y": f"(ih-oh)*{num(size.focus_y)}",
            },
        )
        g.width, g.height = w, h


def build_video_export(g: Graph, settings: dict[str, Any], preset: str, version: int) -> None:
    s = VideoExportSettings.model_validate(settings)
    if not g.has_video:
        raise OpError("export of a video preset needs a video stream; this input is audio only")
    if s.size is not None:
        _size_filters(g, s.size)
    target = s.fps or (min(g.fps, s.max_fps) if s.max_fps else g.fps)
    if abs(target - g.fps) > 0.001:
        g.vf("fps", {"fps": target})
        g.fps = target
    # Normalize color to BT.709, limited range, 4:2:0, square pixels; nothing depends on the host.
    g.vf("scale", {"flags": "lanczos", "out_color_matrix": "bt709", "out_range": "tv"})
    g.vf("format", {"pix_fmts": "yuv420p"})
    g.vf("setsar", {"sar": 1})

    args = [
        "-c:v", "libx264", "-preset", s.encoder_preset, "-profile:v", s.profile,
        "-crf", str(s.crf), "-pix_fmt", "yuv420p", "-threads", str(s.threads),
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-color_range", "tv",
    ]  # fmt: skip
    if s.level:
        args += ["-level:v", s.level]
    if s.maxrate:
        args += ["-maxrate", s.maxrate, "-bufsize", s.bufsize or s.maxrate]
    if s.gop:
        args += ["-g", str(s.gop)]
    loudness_info: dict[str, Any] | None = None
    if s.audio and g.has_audio:
        g.af("aresample", {"async": 1, "first_pts": 0})
        if s.loudness is not None:
            measured = apply_loudness(g, s.loudness)
            loudness_info = {**s.loudness.model_dump(), "before": measured}
        g.af(
            "aformat",
            {
                "sample_fmts": "fltp",
                "sample_rates": s.audio_rate,
                "channel_layouts": "stereo" if s.audio_channels == 2 else "mono",
            },
        )
        args += ["-c:a", "aac", "-b:a", s.audio_bitrate, "-ar", str(s.audio_rate)]
        args += ["-ac", str(s.audio_channels)]
    else:
        g.audio = None  # the preset drops audio, or the input has none
        args += ["-an"]
    args += _container_args()
    g.encode = Encode(
        ".mp4",
        args,
        {
            "preset": preset,
            "version": version,
            "checks": s.checks,
            "max_duration": s.max_duration,
            "size": s.size.model_dump() if s.size else None,
            "fps": s.fps,
            "max_fps": s.max_fps,
            "audio": s.audio,
            "loudness": loudness_info,
            "safe_area": s.safe_area.model_dump() if s.safe_area else None,
        },
    )


def _container_args() -> list[str]:
    return [
        "-movflags", "+faststart", "-f", "mp4", "-map_metadata", "-1", "-map_chapters", "-1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
    ]  # fmt: skip


def mezzanine(g: Graph) -> None:
    """Settings for a video that is not exported (an intermediate a user asked for): visually
    lossless, source size and frame rate, so a later step does not visibly degrade it."""
    args = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
        "-threads", "4", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", "-color_range", "tv",
    ]  # fmt: skip
    if g.has_video:
        g.vf("scale", {"flags": "lanczos", "out_color_matrix": "bt709", "out_range": "tv"})
        g.vf("format", {"pix_fmts": "yuv420p"})
        g.vf("setsar", {"sar": 1})
    if g.has_audio:
        g.af("aformat", {"sample_fmts": "fltp"})
        args += ["-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-an"]
    g.encode = Encode(".mp4", args + _container_args(), {})


def av_summary(result: Artifact) -> str:
    """One line for the model, e.g. `video 15.00s · 1080x1920 · 30fps · h264 · 4.2MB · reels v1`."""
    m = result.meta
    parts = [result.media.value]
    if m.get("duration") is not None:
        parts.append(f"{m['duration']:.2f}s")
    if v := m.get("video"):
        w, h = v["display"]
        parts.append(f"{w}x{h}")
        if v.get("fps"):
            parts.append(f"{v['fps']:g}fps")
        parts.append(str(v["codec"]))
    if a := m.get("audio"):
        parts.append(f"audio {a['codec']}" if m.get("video") else str(a["codec"]))
    parts.append(human_bytes(m.get("bytes", 0)))
    if m.get("export"):
        parts.append(f"{m['export']['preset']} v{m['export']['version']}")
    return " · ".join(parts)
