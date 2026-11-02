"""video.auto_reframe: crop that follows the main subject (U2-Net centroid), not a fixed point.

Needs the ML runtime and the u2netp model (`atelier models fetch u2netp`); skips without them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from atelier import models
from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits, StepFailed
from atelier.spec.errors import SpecInvalid

# This op samples up to TRACK_MAX_SAMPLES frames through a neural model before the real encode,
# which comfortably outruns the default fixture's 30s budget under any real load.


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine.open(tmp_path / "home", run_limits=RunLimits(wall_seconds=90))


try:
    import onnxruntime  # noqa: F401

    RUNTIME = True
except ImportError:
    RUNTIME = False

needs_u2net = pytest.mark.skipif(
    not (RUNTIME and models.is_installed("u2netp")) or shutil.which("ffmpeg") is None,
    reason="needs the ml extra, ffmpeg, and `atelier models fetch u2netp`",
)
pytestmark = needs_u2net

W, H, FPS, SECONDS = 640, 360, 30, 4
BOX = 100
FAST = {"encoder_preset": "veryfast"}


def moving_clip(path: Path, *, axis: str = "x", sound: bool = False) -> Path:
    """Gray background, a white square sweeping from one edge to the other over the clip."""
    span = (W if axis == "x" else H) - BOX
    expr = f"{span}*(t/{SECONDS})"
    pos = f"x='{expr}':y='{(H - BOX) // 2}'" if axis == "x" else f"x='{(W - BOX) // 2}':y='{expr}'"
    argv = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:r={FPS}:d={SECONDS}",
        "-f", "lavfi", "-i", f"color=c=white:s={BOX}x{BOX}:r={FPS}:d={SECONDS}",
    ]  # fmt: skip
    if sound:
        argv += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}"]
    argv += ["-filter_complex", f"[0][1]overlay={pos}"]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    argv += (["-c:a", "aac", "-shortest"] if sound else ["-an"]) + [str(path)]
    subprocess.run(argv, check=True)
    return path


def put(engine: Engine, path: Path) -> str:
    return engine.store.put_file(path).handle


def run(engine: Engine, handle: str, op: str = "video.auto_reframe", **args: Any) -> Path:
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "s", "op": op, "in": "v", "args": {"aspect": "9:16", **args}},
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


def box_center(path: Path, t: float, tmp: Path, tag: str) -> tuple[float, float, int, int] | None:
    """The bright box's centroid in a frame of `path` at time `t`, plus that frame's size.
    The extracted PNG goes under `tmp` (never beside `path`, which may be a read-only store
    object)."""
    png = tmp / f"frame_{tag}_{t}.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-update", "1", str(png)],
        check=True,
    )  # fmt: skip
    arr = np.asarray(Image.open(png).convert("L"))
    ys, xs = np.where(arr > 200)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean()), arr.shape[1], arr.shape[0]


def test_the_crop_keeps_a_horizontally_sweeping_subject_near_center(
    engine: Engine, tmp_path: Path
) -> None:
    clip = moving_clip(tmp_path / "sweep.mp4")
    handle = put(engine, clip)
    tracked = run(engine, handle)
    offsets = []
    for t in (0.3, 1.2, 2.0, 2.8, 3.6):
        found = box_center(tracked, t, tmp_path, "tracked")
        assert found is not None, f"no subject visible at t={t}"
        cx, _, w, _ = found
        offsets.append(abs(cx - w / 2) / w)
    assert max(offsets) < 0.22, offsets  # stays well within the center band throughout


def test_a_fixed_crop_would_have_lost_the_same_subject(engine: Engine, tmp_path: Path) -> None:
    """The point of the feature: show it actually beats a static center crop on this clip."""
    clip = moving_clip(tmp_path / "sweep.mp4")
    handle = put(engine, clip)
    tracked = run(engine, handle)
    fixed = run(engine, handle, op="video.reframe", mode="fill")
    offsets_tracked, offsets_fixed = [], []
    for t in (0.3, 2.0, 3.6):
        ct = box_center(tracked, t, tmp_path, "tracked")
        cf = box_center(fixed, t, tmp_path, "fixed")
        offsets_tracked.append(abs(ct[0] - ct[2] / 2) / ct[2] if ct else 1.0)
        offsets_fixed.append(abs(cf[0] - cf[2] / 2) / cf[2] if cf else 1.0)
    assert max(offsets_tracked) < max(offsets_fixed) - 0.15, (offsets_tracked, offsets_fixed)


def test_vertical_motion_is_followed_too(engine: Engine, tmp_path: Path) -> None:
    clip = moving_clip(tmp_path / "vsweep.mp4", axis="y")
    handle = put(engine, clip)
    # aspect=3 on a 640x360 (1.78) source crops *height* down to 213px, not width: the only
    # way to actually exercise vertical tracking on a landscape source.
    tracked = run(engine, handle, aspect=3)
    for t in (0.3, 2.0, 3.6):
        found = box_center(tracked, t, tmp_path, "vtracked")
        assert found is not None
        _, cy, _, h = found
        assert abs(cy - h / 2) / h < 0.25, (t, cy, h)


def test_smoothing_is_bounded_and_aspect_is_required(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, moving_clip(tmp_path / "sweep.mp4"))
    with pytest.raises(SpecInvalid, match="smoothing"):
        engine.validate(
            {"in": handle, "ops": [["video.auto_reframe", {"aspect": "9:16", "smoothing": 6}]]}
        )
    with pytest.raises(SpecInvalid, match="aspect"):
        engine.validate({"in": handle, "ops": [["video.auto_reframe", {}]]})


def test_a_clip_longer_than_the_limit_is_refused(tmp_path: Path) -> None:
    """The guard is pure logic (no need to encode a real 600s clip to exercise it): build a
    `Graph` directly, the way `test_video_loudness.py` tests `_feeding` without running ffmpeg."""
    from atelier.av.graph import Graph, Source
    from atelier.av.ops import TRACK_MAX_SECONDS, AutoReframeParams, auto_reframe
    from atelier.media import MediaType
    from atelier.ops.registry import OpError

    g = Graph([Source(tmp_path / "x.mp4", MediaType.VIDEO, ".mp4", {})], tmp_path)
    g.video = "0:v:0"
    g.width, g.height, g.fps = 640, 360, 30.0
    g.duration = TRACK_MAX_SECONDS + 1
    with pytest.raises(OpError, match=rf"limited to {TRACK_MAX_SECONDS:g}s clips"):
        auto_reframe(g, AutoReframeParams(aspect="9:16"))


def test_fuses_with_the_rest_of_the_chain(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, moving_clip(tmp_path / "sweep.mp4"))
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "t", "op": "video.trim", "in": "v", "args": {"start": 0.5}},
            {"id": "s", "op": "video.auto_reframe", "in": "t", "args": {"aspect": "9:16"}},
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


def test_result_is_bit_identical_on_a_fresh_engine(engine: Engine, tmp_path: Path) -> None:
    src = moving_clip(tmp_path / "sweep.mp4")
    first = run(engine, put(engine, src)).read_bytes()
    fresh = Engine.open(tmp_path / "fresh", run_limits=RunLimits(wall_seconds=120))
    second = run(fresh, put(fresh, src)).read_bytes()
    assert first == second


def test_a_missing_model_is_an_actionable_error(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path / "no-models"))
    models.model_id.cache_clear()
    handle = put(engine, moving_clip(tmp_path / "sweep.mp4"))
    with pytest.raises(StepFailed, match=r"atelier models fetch u2netp"):
        engine.run({"in": handle, "ops": [["video.auto_reframe", {"aspect": "9:16"}]]})
