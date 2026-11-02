"""ffprobe → compact metadata for video and audio assets.

Runs in the worker sandbox. The demuxer is forced from the sniffed file type, so a crafted
file cannot make ffprobe choose a playlist/concat demuxer that reads other files, and
protocols are limited to `file`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from atelier.av.binaries import PROTOCOLS, find, run
from atelier.ops.registry import OpError

PROBE_TIMEOUT = 60
# The demuxer to force for each sniffed extension (engine/sniff.py).
DEMUXERS = {
    ".mp4": "mov",
    ".mov": "mov",
    ".m4a": "mov",
    ".mkv": "matroska",
    ".webm": "matroska",
    ".wav": "wav",
    ".mp3": "mp3",
    ".ogg": "ogg",
    ".flac": "flac",
}


class ProbeError(OpError):
    """The file could not be read as audio/video; the message is shown to the user as-is."""


def _fraction(text: str | None) -> float | None:
    if not text or "/" not in text:
        return None
    num, _, den = text.partition("/")
    try:
        n, d = float(num), float(den)
    except ValueError:
        return None
    return n / d if d else None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list", []):
        if side.get("side_data_type") == "Display Matrix" and "rotation" in side:
            # ffprobe reports the display matrix counter-clockwise; this is clockwise degrees
            return round(-float(side["rotation"])) % 360
    return 0


def summarize(raw: dict[str, Any]) -> dict[str, Any]:
    """The compact metadata the rest of the engine uses, from ffprobe's JSON."""
    fmt = raw.get("format", {})
    streams = raw.get("streams", [])
    counts = {"video": 0, "audio": 0, "subtitle": 0, "other": 0}
    video: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    for s in streams:
        kind = s.get("codec_type")
        attached = bool(s.get("disposition", {}).get("attached_pic"))
        if kind == "video" and not attached:
            counts["video"] += 1
            if video is None:
                video = _video(s)
        elif kind == "audio":
            counts["audio"] += 1
            if audio is None:
                audio = _audio(s)
        elif kind == "subtitle":
            counts["subtitle"] += 1
        else:
            counts["other"] += 1
    duration = _number(fmt.get("duration"))
    if duration is None:
        durations = [d for d in (_number(s.get("duration")) for s in streams) if d]
        duration = max(durations) if durations else None
    return {
        "container": fmt.get("format_name"),
        "duration": round(duration, 3) if duration is not None else None,
        "size": int(fmt["size"]) if fmt.get("size") else None,
        "bit_rate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        "video": video,
        "audio": audio,
        "streams": counts,
    }


def _frame_rate(s: dict[str, Any]) -> tuple[float | None, bool]:
    """(fps, is_variable). `avg_frame_rate` is frames / stream duration, and muxers differ by a
    frame in the duration they store (7.1 vs 8.x), which is a 1-3% error on a short clip. So a
    constant-rate stream reports its nominal rate, and variable rate is judged by whether the
    frame count is far from duration * rate."""
    avg, real = _fraction(s.get("avg_frame_rate")), _fraction(s.get("r_frame_rate"))
    frames = int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else None
    duration = _number(s.get("duration"))
    if real and frames and duration:
        variable = abs(frames - duration * real) > max(2.0, 0.03 * frames)
    elif avg and real:
        variable = abs(avg - real) / real > 0.05
    else:
        variable = False
    return (avg if variable else (real or avg)), variable


def _video(s: dict[str, Any]) -> dict[str, Any]:
    w, h = int(s.get("width", 0)), int(s.get("height", 0))
    rotation = _rotation(s)
    fps, variable = _frame_rate(s)
    return {
        "codec": s.get("codec_name"),
        "profile": s.get("profile"),
        "width": w,
        "height": h,
        "rotation": rotation,
        "display": [h, w] if rotation in (90, 270) else [w, h],  # what a player shows
        "fps": round(fps, 3) if fps else None,
        "vfr": variable,
        "pix_fmt": s.get("pix_fmt"),
        "sar": s.get("sample_aspect_ratio", "1:1"),
        "duration": _number(s.get("duration")),
        "nb_frames": int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else None,
        "bit_rate": int(s["bit_rate"]) if s.get("bit_rate") else None,
        "color_range": s.get("color_range"),
        "color_space": s.get("color_space"),
    }


def _audio(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "codec": s.get("codec_name"),
        "sample_rate": int(s["sample_rate"]) if s.get("sample_rate") else None,
        "channels": int(s.get("channels", 0)),
        "channel_layout": s.get("channel_layout"),
        "bit_rate": int(s["bit_rate"]) if s.get("bit_rate") else None,
        "duration": _number(s.get("duration")),
    }


def probe_av(path: Path, ext: str = "") -> dict[str, Any]:
    argv = [find("ffprobe"), "-v", "error", "-protocol_whitelist", PROTOCOLS]
    if demuxer := DEMUXERS.get(ext):
        argv += ["-f", demuxer]
    argv += ["-probesize", "20000000", "-analyzeduration", "10000000"]
    argv += ["-print_format", "json", "-show_format", "-show_streams", "-i", str(path)]
    try:
        done = run(argv, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ProbeError("ffprobe timed out; the file may be damaged") from None
    if done.returncode != 0:
        detail = done.stderr.decode(errors="replace").replace(str(path), "the file").splitlines()
        raise ProbeError(detail[-1].strip() if detail else "ffprobe could not read the file")
    try:
        raw = json.loads(done.stdout)
    except ValueError:
        raise ProbeError("ffprobe returned unreadable output") from None
    return summarize(raw)
