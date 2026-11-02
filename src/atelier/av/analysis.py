"""What is in a finished video or audio file: black stretches, frozen picture, dead air.

One read-only pass over the encoded output with ffmpeg's own detectors. The result is data
(intervals in seconds) that the checks judge; nothing here decides whether it is a problem, so
a slideshow's still pictures or a deliberate fade to black are the check's business, not ours.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from atelier.av.binaries import find, run

MAX_INTERVALS = 32
BLACK = "blackdetect=d=0.3:pix_th=0.10"
FREEZE = "freezedetect=n=-60dB:d=1"
SILENCE = "silencedetect=noise=-50dB:d=1"

_BLACK = re.compile(r"black_start:(?P<a>[\d.]+)\s+black_end:(?P<b>[\d.]+)")
_FREEZE = re.compile(r"lavfi\.freezedetect\.freeze_(?P<what>start|end):\s*(?P<t>[\d.]+)")
_SILENCE = re.compile(r"silence_(?P<what>start|end):\s*(?P<t>-?[\d.]+)")

Interval = list[float]


def _pairs(events: list[tuple[str, float]], end_of_file: float | None) -> list[Interval]:
    """Start/end events into intervals. A start with no end runs to the end of the file."""
    out: list[Interval] = []
    start: float | None = None
    for what, t in events:
        if what == "start":
            start = max(0.0, t)
        elif start is not None:
            out.append([round(start, 3), round(t, 3)])
            start = None
    if start is not None and end_of_file is not None and end_of_file > start:
        out.append([round(start, 3), round(end_of_file, 3)])
    return out[:MAX_INTERVALS]


def parse(stderr: str, duration: float | None) -> dict[str, list[Interval]]:
    black = [[round(float(m["a"]), 3), round(float(m["b"]), 3)] for m in _BLACK.finditer(stderr)][
        :MAX_INTERVALS
    ]
    freeze = _pairs([(m["what"], float(m["t"])) for m in _FREEZE.finditer(stderr)], duration)
    silent = _pairs([(m["what"], float(m["t"])) for m in _SILENCE.finditer(stderr)], duration)
    return {"black": black, "frozen": freeze, "silent": silent}


def analyze(
    path: Path, *, video: bool, audio: bool, duration: float | None, timeout: float = 120
) -> dict[str, list[Interval]] | None:
    """Intervals of black picture, frozen picture and silence in `path`; None if the pass fails
    (the output was already verified to decode, so a failure here is not the file's fault)."""
    argv = [find("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-i", str(path)]
    argv += ["-vf", f"{BLACK},{FREEZE}"] if video else ["-vn"]
    argv += ["-af", SILENCE] if audio else ["-an"]
    try:
        done = run([*argv, "-f", "null", "-"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if done.returncode != 0:
        return None
    found = parse(done.stderr.decode(errors="replace"), duration)
    if not video:
        found.pop("black"), found.pop("frozen")
    if not audio:
        found.pop("silent")
    return found
