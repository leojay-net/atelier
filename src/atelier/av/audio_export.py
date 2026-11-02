"""Audio encoding for `export` (docs/03 §3.5): settings model and graph builder.

The same rules as video: a versioned preset decides everything, loudness is normalized in two
passes on the final signal, no metadata is written, and encoders that could vary run
single-threaded or with pinned settings.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from atelier.av.export import Loudness, apply_loudness
from atelier.av.graph import Encode, Graph
from atelier.ops.registry import OpError, Params

KIND = "export.audio"
_FORMATS: dict[str, tuple[str, list[str]]] = {
    # format → (file extension, encoder arguments)
    "mp3": (
        ".mp3",
        ["-c:a", "libmp3lame", "-id3v2_version", "0", "-write_id3v1", "0", "-f", "mp3"],
    ),
    "aac": (".m4a", ["-c:a", "aac", "-f", "ipod"]),  # the ipod muxer writes the M4A brand
    "wav": (".wav", ["-c:a", "pcm_s24le", "-f", "wav"]),
    "flac": (".flac", ["-c:a", "flac", "-compression_level", "8", "-f", "flac"]),
    "opus": (".ogg", ["-c:a", "libopus", "-vbr", "on", "-f", "ogg"]),
}
_LOSSLESS = ("wav", "flac")


class AudioExportSettings(Params):
    format: Literal["mp3", "aac", "wav", "flac", "opus"]
    bitrate: str = Field("128k", pattern=r"^\d+[kK]$", description="ignored for wav and flac")
    sample_rate: int = Field(48000, ge=8000, le=96000)
    channels: Literal[1, 2] = 2
    loudness: Loudness | None = Field(None, description="normalize to this loudness")
    max_duration: float | None = Field(None, gt=0, description="seconds; the duration check")
    checks: list[str] = Field(default_factory=list)


def build_audio_export(g: Graph, settings: dict[str, Any], preset: str, version: int) -> None:
    s = AudioExportSettings.model_validate(settings)
    if not g.has_audio:
        raise OpError("this input has no audio to export")
    g.video = None
    g.af("aresample", {"async": 1, "first_pts": 0})
    loudness_info: dict[str, Any] | None = None
    if s.loudness is not None:
        measured = apply_loudness(g, s.loudness)
        loudness_info = {**s.loudness.model_dump(), "before": measured}
    g.af(
        "aformat",
        {
            "sample_fmts": "s32"
            if s.format == "wav"
            else "fltp"
            if s.format in ("aac", "opus")
            else "s16p|s32",
            "sample_rates": s.sample_rate,
            "channel_layouts": "stereo" if s.channels == 2 else "mono",
        },
    )
    ext, encoder = _FORMATS[s.format]
    args = [*encoder, "-ar", str(s.sample_rate), "-ac", str(s.channels)]
    if s.format not in _LOSSLESS:
        args += ["-b:a", s.bitrate]
    args += [
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        "-vn",
    ]
    g.encode = Encode(
        ext,
        args,
        {
            "preset": preset,
            "version": version,
            "checks": s.checks,
            "max_duration": s.max_duration,
            "format": s.format,
            "loudness": loudness_info,
        },
    )
