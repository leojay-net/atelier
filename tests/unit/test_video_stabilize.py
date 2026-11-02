"""video.stabilize: two-pass motion smoothing (libvidstab). Needs a vidstab-enabled ffmpeg
build, which the reference image has and the Homebrew host build does not; skips on the host.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits, StepFailed
from atelier.spec.errors import SpecInvalid

W, H, FPS, SECONDS = 640, 360, 30, 4
FAST = {"encoder_preset": "veryfast"}


def _has_vidstab() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return "vidstabdetect" in out.stdout


pytestmark = pytest.mark.skipif(not _has_vidstab(), reason="needs a vidstab-enabled ffmpeg build")


def shaky_clip(path: Path, tmp: Path) -> Path:
    """A static, richly textured frame shown through a window that jitters sinusoidally: the
    only motion between frames is the simulated camera shake, which makes the effect of
    stabilizing it easy to measure."""
    still = tmp / "still.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=size={W + 80}x{H + 80}:rate=1:duration=1", "-frames:v", "1", str(still)],
        check=True,
    )  # fmt: skip
    jitter = "20+18*sin(2*PI*t*2.7)"
    wobble = "20+15*cos(2*PI*t*2.1)"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(still),
         "-t", str(SECONDS), "-vf", f"fps={FPS},crop={W}:{H}:x='{jitter}':y='{wobble}'",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


def decode_gray(path: Path) -> np.ndarray:
    """Every frame of `path` as a (n, H, W) uint8 grayscale array, one ffmpeg decode."""
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    frames = np.frombuffer(raw, dtype=np.uint8)
    return frames[: frames.size // (W * H) * W * H].reshape(-1, H, W)


def jitter_score(frames: np.ndarray) -> float:
    """Mean frame-to-frame pixel movement: high when the picture is shaking, near 0 when still."""
    diffs = np.abs(frames[1:].astype(np.int16) - frames[:-1].astype(np.int16))
    return float(diffs.mean())


def put(engine: Engine, path: Path) -> str:
    return engine.store.put_file(path).handle


def run(engine: Engine, handle: str, **args: object) -> Path:
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "s", "op": "video.stabilize", "in": "v", "args": args},
            {
                "id": "out",
                "op": "export",
                "in": "s",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }
    result = engine.run(spec)
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    return engine.store.path(result.outputs["out"].hash)


def test_stabilizing_reduces_the_simulated_shake(engine: Engine, tmp_path: Path) -> None:
    src = shaky_clip(tmp_path / "shaky.mp4", tmp_path)
    handle = put(engine, src)
    stabilized = run(engine, handle)
    before = jitter_score(decode_gray(src))
    after = jitter_score(decode_gray(stabilized))
    assert after < before * 0.5, (before, after)  # meaningfully smoother, not just different


def test_auto_zoom_keeps_the_frame_size_and_keep_mode_does_too(
    engine: Engine, tmp_path: Path
) -> None:
    handle = put(engine, shaky_clip(tmp_path / "shaky.mp4", tmp_path))
    for zoom in ("auto", "keep"):
        out = run(engine, handle, zoom=zoom)
        frames = decode_gray(out)
        assert frames.shape[1:] == (H, W)


def test_shakiness_and_smoothing_are_bounded(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, shaky_clip(tmp_path / "shaky.mp4", tmp_path))
    with pytest.raises(SpecInvalid, match="shakiness"):
        engine.validate(
            {
                "in": handle,
                "ops": [["video.stabilize", {"shakiness": 11}], ["export", {"preset": "web_h264"}]],
            }
        )
    with pytest.raises(SpecInvalid, match="zoom"):
        engine.validate(
            {
                "in": handle,
                "ops": [
                    ["video.stabilize", {"zoom": "sideways"}],
                    ["export", {"preset": "web_h264"}],
                ],
            }
        )


def test_a_clip_too_short_to_analyze_is_refused(engine: Engine, tmp_path: Path) -> None:
    short = tmp_path / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=30:duration=0.5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(short)],
        check=True,
    )  # fmt: skip
    handle = put(engine, short)
    with pytest.raises(StepFailed, match=r"only 0\.50s long: too short"):
        engine.run(
            {"in": handle, "ops": [["video.stabilize", {}], ["export", {"preset": "web_h264"}]]}
        )


def test_stabilize_fuses_with_the_rest_of_the_chain(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, shaky_clip(tmp_path / "shaky.mp4", tmp_path))
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "t", "op": "video.trim", "in": "v", "args": {"start": 0.5}},
            {"id": "s", "op": "video.stabilize", "in": "t", "args": {}},
            {
                "id": "out",
                "op": "export",
                "in": "s",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }
    result = engine.run(spec)
    assert result.passed and len(result.steps) == 1 and result.fused_steps == 2


def test_the_result_is_bit_identical_on_a_fresh_engine(engine: Engine, tmp_path: Path) -> None:
    src = shaky_clip(tmp_path / "shaky.mp4", tmp_path)
    first = run(engine, put(engine, src)).read_bytes()
    fresh = Engine.open(tmp_path / "fresh", run_limits=RunLimits(wall_seconds=120))
    second = run(fresh, put(fresh, src)).read_bytes()
    assert first == second
