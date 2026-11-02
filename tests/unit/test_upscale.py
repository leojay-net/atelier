"""image.upscale / video.upscale: FSRCNN super-resolution (OpenCV's `dnn_superres`).

Needs the ML runtime and the fsrcnn model (`atelier models fetch fsrcnn`); skips without them.
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

try:
    import cv2  # noqa: F401

    RUNTIME = True
except ImportError:
    RUNTIME = False

needs_fsrcnn = pytest.mark.skipif(
    not (RUNTIME and models.is_installed("fsrcnn")) or shutil.which("ffmpeg") is None,
    reason="needs the ml extra, ffmpeg, and `atelier models fetch fsrcnn`",
)
pytestmark = needs_fsrcnn

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine.open(tmp_path / "home", run_limits=RunLimits(wall_seconds=90))


def put(engine: Engine, path: Path) -> str:
    return engine.store.put_file(path).handle


# -- image.upscale -------------------------------------------------------------------------


def run_image(engine: Engine, handle: str, **args: Any) -> Path:
    spec = {
        "version": 1,
        "inputs": {"i": handle},
        "steps": [{"id": "up", "op": "image.upscale", "in": "i", "args": args}],
        "outputs": ["up"],
    }
    result = engine.run(spec)
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    return engine.store.path(result.outputs["up"].hash)


@pytest.mark.parametrize("scale", [2, 3, 4])
def test_each_scale_multiplies_both_dimensions(engine: Engine, scale: int) -> None:
    handle = put(engine, FIXTURES / "photo.jpg")
    src = Image.open(FIXTURES / "photo.jpg")
    out = Image.open(run_image(engine, handle, scale=scale))
    assert out.size == (src.width * scale, src.height * scale)


def test_the_model_is_really_used_not_a_plain_resize(engine: Engine) -> None:
    handle = put(engine, FIXTURES / "photo.jpg")
    src = Image.open(FIXTURES / "photo.jpg").convert("RGB")
    out = np.asarray(Image.open(run_image(engine, handle, scale=2)).convert("RGB"))
    plain = np.asarray(src.resize((src.width * 2, src.height * 2), Image.Resampling.LANCZOS))
    assert not np.array_equal(out, plain)


def test_alpha_is_upscaled_and_kept(engine: Engine, tmp_path: Path) -> None:
    rgba = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
    rgba.paste((0, 255, 0, 128), (10, 10, 30, 30))
    path = tmp_path / "rgba.png"
    rgba.save(path)
    handle = put(engine, path)
    out = Image.open(run_image(engine, handle, scale=2))
    assert out.mode == "RGBA" and out.size == (80, 80)
    # the patch is (10,10)-(30,30) in the 40x40 source, so (20,20)-(60,60) once scaled 2x;
    # sampled well inside that box, away from the resize's edge ringing
    assert out.getpixel((40, 40))[3] == 128


def test_grayscale_input_is_handled(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "gray.png"
    Image.new("L", (30, 30), 100).save(path)
    handle = put(engine, path)
    out = Image.open(run_image(engine, handle, scale=2))
    assert out.size == (60, 60)


def test_scale_is_bounded_to_2_3_4(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, FIXTURES / "photo.jpg")
    with pytest.raises(SpecInvalid, match="scale"):
        engine.validate({"in": handle, "ops": [["image.upscale", {"scale": 5}]]})


def test_a_result_too_large_is_refused(tmp_path: Path) -> None:
    from atelier.ml.upscale import _check_pixels
    from atelier.ops.registry import OpError

    with pytest.raises(OpError, match="pixel limit"):
        _check_pixels(5000, 5000, 4)


def test_image_result_is_bit_identical_on_a_fresh_engine(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, FIXTURES / "photo.jpg")
    first = run_image(engine, handle, scale=2).read_bytes()
    fresh = Engine.open(tmp_path / "fresh", run_limits=RunLimits(wall_seconds=90))
    second = run_image(fresh, put(fresh, FIXTURES / "photo.jpg"), scale=2).read_bytes()
    assert first == second


def test_a_missing_image_model_is_an_actionable_error(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path / "no-models"))
    models.model_id.cache_clear()
    handle = put(engine, FIXTURES / "photo.jpg")
    with pytest.raises(StepFailed, match=r"atelier models fetch fsrcnn"):
        engine.run({"in": handle, "ops": [["image.upscale", {}]]})


# -- video.upscale --------------------------------------------------------------------------

W, H, FPS, SECONDS = 320, 240, 25, 2
FAST = {"encoder_preset": "veryfast"}


def small_clip(path: Path, *, sound: bool = True) -> Path:
    argv = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration={SECONDS}",
    ]  # fmt: skip
    if sound:
        argv += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}"]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    argv += (["-c:a", "aac", "-shortest"] if sound else ["-an"]) + [str(path)]
    subprocess.run(argv, check=True)
    return path


def run_video(engine: Engine, handle: str, **args: Any) -> Path:
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [{"id": "up", "op": "video.upscale", "in": "v", "args": args}],
        "outputs": ["up"],
    }
    result = engine.run(spec)
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    return engine.store.path(result.outputs["up"].hash)


def frame_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, check=True, text=True,
    )  # fmt: skip
    w, h = out.stdout.strip().split(",")
    return int(w), int(h)


def test_video_scale_multiplies_frame_size(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, small_clip(tmp_path / "s.mp4"))
    out = run_video(engine, handle, scale=2)
    assert frame_size(out) == (W * 2, H * 2)


def test_video_reports_frame_count_and_keeps_audio(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, small_clip(tmp_path / "s.mp4"))
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [{"id": "up", "op": "video.upscale", "in": "v", "args": {"scale": 2}}],
        "outputs": ["up"],
    }
    result = engine.run(spec)
    assert result.passed
    meta = result.steps[0].meta
    assert meta["upscale"] == {"frames": FPS * SECONDS, "scale": 2}
    assert meta["audio"] is not None


def test_a_source_above_the_resolution_cap_is_refused(tmp_path: Path) -> None:
    from atelier.media import MediaType
    from atelier.ml.upscale import UPSCALE_MAX_SOURCE_DIM, UpscaleParams, video_upscale
    from atelier.ops.registry import Artifact, OpContext, OpError

    fake = Artifact(
        tmp_path / "x.mp4",
        MediaType.VIDEO,
        ".mp4",
        {
            "video": {"display": [1920, 1080], "fps": 30.0, "sar": "1:1"},
            "duration": 5.0,
            "audio": None,
        },
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(OpError, match=rf"limited to {UPSCALE_MAX_SOURCE_DIM}px"):
        video_upscale(OpContext(out_dir, {}), [fake], UpscaleParams())


def test_a_clip_longer_than_the_limit_is_refused(tmp_path: Path) -> None:
    from atelier.media import MediaType
    from atelier.ml.upscale import UPSCALE_MAX_SECONDS, UpscaleParams, video_upscale
    from atelier.ops.registry import Artifact, OpContext, OpError

    fake = Artifact(
        tmp_path / "x.mp4",
        MediaType.VIDEO,
        ".mp4",
        {
            "video": {"display": [320, 240], "fps": 30.0, "sar": "1:1"},
            "duration": UPSCALE_MAX_SECONDS + 1,
            "audio": None,
        },
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(OpError, match=rf"limited to {UPSCALE_MAX_SECONDS:g}s clips"):
        video_upscale(OpContext(out_dir, {}), [fake], UpscaleParams())


def test_video_does_not_fuse_with_neighboring_steps(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, small_clip(tmp_path / "s.mp4"))
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "t", "op": "video.trim", "in": "v", "args": {"start": 0.2}},
            {"id": "up", "op": "video.upscale", "in": "t", "args": {"scale": 2}},
            {
                "id": "out",
                "op": "export",
                "in": "up",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }
    result = engine.run(spec)
    assert result.passed
    assert len(result.steps) == 3
    assert result.fused_steps == 0


def test_video_result_is_bit_identical_on_a_fresh_engine(engine: Engine, tmp_path: Path) -> None:
    src = small_clip(tmp_path / "s.mp4")
    first = run_video(engine, put(engine, src), scale=2).read_bytes()
    fresh = Engine.open(tmp_path / "fresh", run_limits=RunLimits(wall_seconds=90))
    second = run_video(fresh, put(fresh, src), scale=2).read_bytes()
    assert first == second


def test_a_missing_video_model_is_an_actionable_error(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path / "no-models"))
    models.model_id.cache_clear()
    handle = put(engine, small_clip(tmp_path / "s.mp4"))
    with pytest.raises(StepFailed, match=r"atelier models fetch fsrcnn"):
        engine.run({"in": handle, "ops": [["video.upscale", {"scale": 2}]]})
