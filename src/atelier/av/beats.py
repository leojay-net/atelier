"""Finding the tempo (and where the beats fall) in a piece of music or a video's sound.

Plain signal processing, no model: the sound is decoded to mono PCM by ffmpeg, the loudness
jumps (onsets) are measured, and the tempo is the spacing at which those jumps repeat most
(autocorrelation) between 60 and 180 beats per minute.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from atelier.av.binaries import find

RATE = 11025
HOP = 256  # samples between measurements (about 23 ms)
MAX_SECONDS = 120


def decode(path: Path) -> np.ndarray:
    done = subprocess.run(
        [find("ffmpeg"), "-v", "error", "-nostdin", "-t", str(MAX_SECONDS), "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
        capture_output=True, timeout=60, check=False,
    )  # fmt: skip
    return np.frombuffer(done.stdout, dtype=np.float32)


def analyse(pcm: np.ndarray) -> dict[str, float | list[float]]:
    """{bpm, offset, beats}: the tempo, the time of the first beat and every beat (seconds).
    Empty (bpm 0) when there is no clear pulse."""
    none: dict[str, float | list[float]] = {"bpm": 0.0, "offset": 0.0, "beats": []}
    if len(pcm) < RATE * 4:
        return none
    frames = len(pcm) // HOP
    energy = np.sqrt((pcm[: frames * HOP].reshape(frames, HOP) ** 2).mean(axis=1))
    flux = np.maximum(np.diff(energy, prepend=energy[0]), 0.0)
    if flux.max() <= 1e-6:
        return none
    flux = flux - flux.mean()
    per_second = RATE / HOP
    lo, hi = int(per_second * 60 / 180), int(per_second * 60 / 60)
    corr = np.correlate(flux, flux, mode="full")[len(flux) - 1 :]
    if hi >= len(corr) or corr[0] <= 0:
        return none
    lag = lo + int(np.argmax(corr[lo : hi + 1]))
    if corr[lag] < 0.15 * corr[0]:
        return none  # no repeating pulse
    # refine with the parabola through the peak and its neighbours
    if 0 < lag < len(corr) - 1:
        a, b, c = corr[lag - 1], corr[lag], corr[lag + 1]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
    else:
        shift = 0.0
    period = (lag + shift) / per_second
    bpm = 60.0 / period
    while bpm < 70:  # halve or double into the usual range
        bpm *= 2
    while bpm > 160:
        bpm /= 2
    period = 60.0 / bpm
    pf = period * per_second  # the period in (fractional) frames
    n = len(flux)

    def score(k: float) -> float:
        idx = np.rint(k + pf * np.arange(int((n - k) / pf))).astype(int)
        return float(flux[idx[idx < n]].sum())

    phase = max(np.arange(0, pf, 0.25), key=score)
    offset = phase / per_second
    duration = len(pcm) / RATE
    beats = [round(offset + i * period, 3) for i in range(int((duration - offset) / period) + 1)]
    return {"bpm": round(bpm, 1), "offset": round(offset, 3), "beats": beats}
