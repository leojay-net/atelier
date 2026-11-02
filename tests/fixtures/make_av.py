"""Regenerate the synthetic audio/video fixtures (ffmpeg's lavfi sources; no real footage).

    uv run python tests/fixtures/make_av.py

The committed files are what tests use; rerun only when adding a fixture. Tests assert on
structure (duration, size, fps, codecs) and perceptual similarity, never on these bytes, so it
does not matter which ffmpeg version wrote them.

    clip_landscape.mp4  4 s 640x360 @30, h264 + stereo AAC 440 Hz     (moving pattern with a clock)
    clip_portrait.mp4   3 s 360x640 @30, h264 + stereo AAC 660 Hz
    clip_silent.mp4     3 s 640x360 @25, h264, no audio stream
    clip_rotated.mp4    landscape pixels + a 90 degree rotation flag (displays as 360x640)
    tone.wav            3 s 440 Hz mono PCM at 44.1 kHz
"""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).parent / "av"
COMMON = ["-hide_banner", "-loglevel", "error", "-y"]
OUTPUT = ["-fflags", "+bitexact", "-map_metadata", "-1"]  # output options: go before the file
X264 = [
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "33",
    "-pix_fmt",
    "yuv420p",
    "-threads",
    "1",
]
AAC = ["-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2"]


def ffmpeg(*args: str, output: str, extra: tuple[str, ...] = ()) -> None:
    subprocess.run(["ffmpeg", *COMMON, *args, *OUTPUT, *extra, output], check=True)


def clip(name: str, size: str, fps: int, seconds: int, hz: int | None) -> None:
    args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if hz is not None:
        args += ["-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000:duration={seconds}"]
    args += X264 + (AAC if hz is not None else ["-an"])
    ffmpeg(*args, output=str(HERE / name), extra=("-movflags", "+faststart"))


def main() -> None:
    HERE.mkdir(exist_ok=True)
    clip("clip_landscape.mp4", "640x360", 30, 4, 440)
    clip("clip_portrait.mp4", "360x640", 30, 3, 660)
    clip("clip_silent.mp4", "640x360", 25, 3, None)
    ffmpeg("-display_rotation:v", "90", "-i", str(HERE / "clip_landscape.mp4"),
           output=str(HERE / "clip_rotated.mp4"), extra=("-c", "copy"))  # fmt: skip
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
           output=str(HERE / "tone.wav"), extra=("-ac", "1", "-c:a", "pcm_s16le"))  # fmt: skip


if __name__ == "__main__":
    main()
