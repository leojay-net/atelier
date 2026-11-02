"""video.blur_faces: per-frame face detection (YuNet) and blur/pixelate/box treatment.

Needs the ML runtime and the yunet model (`atelier models fetch yunet`); skips without them.

A plain ffmpeg-drawn shape (a box, a gradient) does not trigger a real CNN face detector the
way it triggers U2-Net's salience (auto_reframe's test clips): YuNet was trained on photos and
reacts to skin-like shading, texture and the arrangement of eyes/nose/mouth. `face_clip` builds
a synthetic face with numpy/cv2 (soft radial shading, photographic noise, eyes, brows, a nose
shadow, a mouth) that reliably scores above 0.3 - well below real photos (0.8-0.99+), which is
why these tests pass `min_score=0.3` explicitly instead of the op's real-world default of 0.6.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from atelier import models
from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits, StepFailed
from atelier.spec.errors import SpecInvalid


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine.open(tmp_path / "home", run_limits=RunLimits(wall_seconds=90))


try:
    import cv2

    RUNTIME = True
except ImportError:
    RUNTIME = False

needs_yunet = pytest.mark.skipif(
    not (RUNTIME and models.is_installed("yunet")) or shutil.which("ffmpeg") is None,
    reason="needs the ml extra, ffmpeg, and `atelier models fetch yunet`",
)
pytestmark = needs_yunet

W, H, FPS, SECONDS = 640, 480, 25, 2
FACE_W, FACE_H = 240, 300
DRIFT = 20  # total px the face drifts right over the clip
FAST = {"encoder_preset": "veryfast"}


def _draw_face(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    """A soft-shaded, noisy, eyes/brows/nose/mouth oval: photographic enough that YuNet's CNN
    (trained on real photos, not geometry) reacts to it, unlike a flat-colored drawbox/crop."""
    yy, xx = np.mgrid[0:h, 0:w]
    rx, ry = w / 2, h / 2
    dist = np.sqrt(((xx - rx) / rx) ** 2 + ((yy - ry) / ry) ** 2)
    shade = np.clip(1.0 - 0.35 * dist, 0.4, 1.0)
    base = np.array([190, 170, 225], dtype=np.float32)  # BGR
    face = base * shade[..., None] + rng.normal(0, 9, size=(h, w, 3))
    face = np.clip(face, 0, 255).astype(np.uint8)
    ex1, ex2, ey = int(w * 0.32), int(w * 0.68), int(h * 0.40)
    for ex in (ex1, ex2):
        eye = (int(w * 0.09), int(h * 0.045))
        cv2.ellipse(face, (ex, ey), eye, 0, 0, 360, (250, 250, 250), -1)
        cv2.circle(face, (ex, ey), max(2, int(w * 0.035)), (40, 30, 20), -1)
        brow = (int(w * 0.1), int(h * 0.02))
        cv2.ellipse(face, (ex, ey - int(h * 0.07)), brow, 0, 200, 340, (60, 50, 40), 3)
    nose_top, nose_bot = (int(w * 0.5), int(h * 0.42)), (int(w * 0.47), int(h * 0.62))
    cv2.line(face, nose_top, nose_bot, (142, 127, 168), 3)
    mouth = (int(w * 0.16), int(h * 0.05))
    cv2.ellipse(face, (int(w * 0.5), int(h * 0.78)), mouth, 0, 0, 180, (120, 90, 140), -1)
    return cv2.GaussianBlur(face, (3, 3), 0)


def face_box(i: int, n: int) -> tuple[int, int, int, int]:
    """The synthetic face's (x0, y0, x1, y1) in frame `i` of `n` (same drift formula as
    `face_clip`): used by tests to sample inside/outside the blurred region precisely."""
    x0 = (W - FACE_W) // 2 + round(DRIFT * (i / max(1, n - 1)))
    y0 = (H - FACE_H) // 2
    return x0, y0, x0 + FACE_W, y0 + FACE_H


def face_clip(path: Path, *, sound: bool = True) -> Path:
    """Gray background, a synthetic photographic face drifting slowly to the right."""
    rng = np.random.default_rng(0)
    face = _draw_face(rng, FACE_W, FACE_H)
    n = FPS * SECONDS
    argv = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
    ]  # fmt: skip
    if sound:
        argv += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}"]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    argv += (["-c:a", "aac", "-shortest"] if sound else ["-an"]) + [str(path)]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(n):
        canvas = np.full((H, W, 3), (210, 210, 210), dtype=np.uint8)
        x0, y0, x1, y1 = face_box(i, n)
        canvas[y0:y1, x0:x1] = face
        proc.stdin.write(canvas.tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


def put(engine: Engine, path: Path) -> str:
    return engine.store.put_file(path).handle


def run(engine: Engine, handle: str, **args: Any) -> Any:
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "b", "op": "video.blur_faces", "in": "v", "args": {"min_score": 0.3, **args}}
        ],
        "outputs": ["b"],
    }  # fmt: skip
    result = engine.run(spec)
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    return result


def frame_at(path: Path, t: float, tmp: Path, tag: str) -> np.ndarray:
    png = tmp / f"frame_{tag}_{t}.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-update", "1", str(png)],
        check=True,
    )  # fmt: skip
    from PIL import Image

    return np.asarray(Image.open(png).convert("RGB"))


def test_every_frame_gets_a_detected_face(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, face_clip(tmp_path / "face.mp4"))
    result = run(engine, handle)
    faces = result.steps[0].meta["faces"]
    assert faces["frames"] == FPS * SECONDS
    assert faces["frames_with_a_face"] == faces["frames"]


def test_blur_visibly_softens_the_face_region(engine: Engine, tmp_path: Path) -> None:
    src = face_clip(tmp_path / "face.mp4")
    handle = put(engine, src)
    result = run(engine, handle, style="blur")
    out = engine.store.path(result.outputs["b"].hash)
    n = FPS * SECONDS
    i = n // 2
    t = i / FPS
    x0, y0, x1, y1 = face_box(i, n)
    before = frame_at(src, t, tmp_path, "before")[y0:y1, x0:x1]
    after = frame_at(out, t, tmp_path, "after")[y0:y1, x0:x1]
    # the eyes/brows/nose/mouth give the original region real texture; a blur softens it (the
    # low-frequency shading gradient across the whole face survives any reasonable kernel, so
    # this is a real but moderate drop, not a flattening to a single color)
    assert after.std() < before.std() * 0.8, (before.std(), after.std())


def test_box_style_fills_the_face_with_solid_black(engine: Engine, tmp_path: Path) -> None:
    src = face_clip(tmp_path / "face.mp4")
    handle = put(engine, src)
    result = run(engine, handle, style="box")
    out = engine.store.path(result.outputs["b"].hash)
    n = FPS * SECONDS
    i = n // 2
    t = i / FPS
    x0, y0, x1, y1 = face_box(i, n)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    pixel = frame_at(out, t, tmp_path, "box")[cy, cx]
    assert tuple(int(v) for v in pixel) == (0, 0, 0)


def test_pixelate_style_reduces_distinct_colors_in_the_face(engine: Engine, tmp_path: Path) -> None:
    src = face_clip(tmp_path / "face.mp4")
    handle = put(engine, src)
    result = run(engine, handle, style="pixelate")
    out = engine.store.path(result.outputs["b"].hash)
    n = FPS * SECONDS
    i = n // 2
    t = i / FPS
    x0, y0, x1, y1 = face_box(i, n)
    before = frame_at(src, t, tmp_path, "pbefore")[y0:y1, x0:x1]
    after = frame_at(out, t, tmp_path, "pafter")[y0:y1, x0:x1]
    colors_before = len(np.unique(before.reshape(-1, 3), axis=0))
    colors_after = len(np.unique(after.reshape(-1, 3), axis=0))
    assert colors_after < colors_before * 0.2, (colors_before, colors_after)


def test_a_clip_with_no_face_is_passed_through_unblurred(engine: Engine, tmp_path: Path) -> None:
    plain = tmp_path / "plain.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"color=c=gray:s={W}x{H}:r={FPS}:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)],
        check=True,
    )  # fmt: skip
    handle = put(engine, plain)
    result = run(engine, handle, min_score=0.9)
    assert result.steps[0].meta["faces"]["frames_with_a_face"] == 0


def test_style_expand_and_min_score_are_validated(engine: Engine, tmp_path: Path) -> None:
    handle = put(engine, face_clip(tmp_path / "face.mp4"))
    with pytest.raises(SpecInvalid, match="style"):
        engine.validate({"in": handle, "ops": [["video.blur_faces", {"style": "sideways"}]]})
    with pytest.raises(SpecInvalid, match="expand"):
        engine.validate({"in": handle, "ops": [["video.blur_faces", {"expand": 2}]]})
    with pytest.raises(SpecInvalid, match="min_score"):
        engine.validate({"in": handle, "ops": [["video.blur_faces", {"min_score": 1.5}]]})


def test_an_audio_only_input_is_rejected_at_validation(engine: Engine, tmp_path: Path) -> None:
    audio = tmp_path / "audio.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(audio)],
        check=True,
    )  # fmt: skip
    handle = put(engine, audio)
    with pytest.raises(SpecInvalid, match="video"):
        engine.validate({"in": handle, "ops": [["video.blur_faces", {}]]})


def test_does_not_fuse_with_neighboring_steps(engine: Engine, tmp_path: Path) -> None:
    """Unlike the filter-based video ops, this one needs Python between every frame, so it
    cannot join a fused ffmpeg graph: each step here runs as its own pass."""
    handle = put(engine, face_clip(tmp_path / "face.mp4"))
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "t", "op": "video.trim", "in": "v", "args": {"start": 0.2}},
            {"id": "b", "op": "video.blur_faces", "in": "t", "args": {"min_score": 0.3}},
            {
                "id": "out",
                "op": "export",
                "in": "b",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }
    result = engine.run(spec)
    assert result.passed
    assert len(result.steps) == 3
    assert result.fused_steps == 0


def test_a_clip_longer_than_the_limit_is_refused(tmp_path: Path) -> None:
    """The guard is pure logic (no need to encode a 180s clip): call the op directly with a
    fake Artifact, the way `test_video_auto_reframe.py` tests `TRACK_MAX_SECONDS`."""
    from atelier.media import MediaType
    from atelier.ml.faces import FACE_MAX_SECONDS, BlurFacesParams, blur_faces
    from atelier.ops.registry import Artifact, OpContext, OpError

    fake = Artifact(
        tmp_path / "x.mp4",
        MediaType.VIDEO,
        ".mp4",
        {
            "video": {"display": [640, 480], "fps": 25.0, "sar": "1:1"},
            "duration": FACE_MAX_SECONDS + 1,
            "audio": None,
        },
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(OpError, match=rf"limited to {FACE_MAX_SECONDS:g}s clips"):
        blur_faces(OpContext(out_dir, {}), [fake], BlurFacesParams())


def test_result_is_bit_identical_on_a_fresh_engine(engine: Engine, tmp_path: Path) -> None:
    src = face_clip(tmp_path / "face.mp4")
    r1 = run(engine, put(engine, src))
    first = engine.store.path(r1.outputs["b"].hash).read_bytes()
    fresh = Engine.open(tmp_path / "fresh", run_limits=RunLimits(wall_seconds=120))
    r2 = run(fresh, put(fresh, src))
    second = fresh.store.path(r2.outputs["b"].hash).read_bytes()
    assert first == second


def test_a_missing_model_is_an_actionable_error(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path / "no-models"))
    models.model_id.cache_clear()
    handle = put(engine, face_clip(tmp_path / "face.mp4"))
    with pytest.raises(StepFailed, match=r"atelier models fetch yunet"):
        engine.run({"in": handle, "ops": [["video.blur_faces", {}]]})
