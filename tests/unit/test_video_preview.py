"""Video previews: the cheap way to look at a video (docs/06 §6.2)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops, ImageStat

from atelier.agent.budget import Budget, BudgetLimits
from atelier.agent.tools import Toolbox
from atelier.engine.engine import Engine
from atelier.engine.runner import StepFailed
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

AV = FIXTURES / "av"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


@pytest.fixture
def clip(engine: Engine) -> str:
    return engine.store.put_file(AV / "clip_landscape.mp4").handle


def image_of(engine: Engine, result: Any) -> Image.Image:
    rep = result.outputs["preview"]
    return Image.open(engine.store.path(rep.hash)).convert("RGB")


def reference_frame(t: float, tmp: Path, width: int) -> Image.Image:
    png = tmp / f"ref_{t}.png"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t),
            "-i", str(AV / "clip_landscape.mp4"),
            "-frames:v", "1", "-vf", f"scale={width}:-2:flags=lanczos", str(png),
        ],
        check=True,
    )  # fmt: skip
    return Image.open(png).convert("RGB")


def closeness(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute luma difference after matching sizes: small means the same picture."""
    b = b.resize(a.size)
    return float(ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0])


def test_sheet_is_a_labelled_grid_of_evenly_spaced_frames(engine: Engine, clip: str) -> None:
    result = engine.preview("sheet", [clip], {"count": 6})
    rep = result.outputs["preview"]
    assert rep.summary.startswith("preview ") and "tokens" in rep.summary
    im = image_of(engine, result)
    assert im.width > im.height  # 3 columns x 2 rows of 16:9 frames
    tokens = int(rep.summary.split("~")[1].split(" ")[0])
    assert tokens < 500  # six frames for about the price of one large image


def test_the_frames_are_the_ones_at_the_stated_times(
    engine: Engine, clip: str, tmp_path: Path
) -> None:
    result = engine.preview("sheet", [clip], {"at": [0.5, 3.5], "columns": 2})
    im = image_of(engine, result)
    cell_w = im.width // 2
    first = im.crop((0, 0, cell_w, int(cell_w * 9 / 16)))
    second = im.crop((cell_w, 0, im.width, int(cell_w * 9 / 16)))
    assert closeness(first, reference_frame(0.5, tmp_path, cell_w)) < closeness(
        first, reference_frame(3.5, tmp_path, cell_w)
    )
    assert closeness(second, reference_frame(3.5, tmp_path, cell_w)) < closeness(
        second, reference_frame(0.5, tmp_path, cell_w)
    )


def test_thumb_of_a_video_is_one_frame_at_the_requested_time(
    engine: Engine, clip: str, tmp_path: Path
) -> None:
    im = image_of(engine, engine.preview("thumb", [clip], {"time": 2.0}))
    near, far = reference_frame(2.0, tmp_path, im.width), reference_frame(0.1, tmp_path, im.width)
    cropped = im.crop((0, 0, im.width, int(im.width * 9 / 16)))  # above the label strip
    assert closeness(cropped, near) < closeness(cropped, far)


def test_a_rotated_clip_previews_upright_and_costs_no_more_than_a_wide_one(
    engine: Engine, clip: str
) -> None:
    rotated = engine.store.put_file(AV / "clip_rotated.mp4").handle
    tall = engine.preview("thumb", [rotated])
    wide = engine.preview("thumb", [clip])
    t = image_of(engine, tall)
    assert t.height > t.width  # displayed as a portrait
    tokens = lambda r: int(r.outputs["preview"].summary.split("~")[1].split(" ")[0])  # noqa: E731
    assert tokens(tall) <= tokens(wide) * 1.2  # frames are fitted to a square box


def test_previews_are_cached(engine: Engine, clip: str) -> None:
    engine.preview("sheet", [clip], {"count": 4})
    assert engine.preview("sheet", [clip], {"count": 4}).reused == 1


def test_a_time_past_the_end_is_an_actionable_error(engine: Engine, clip: str) -> None:
    with pytest.raises(
        StepFailed, match=r"a preview time is past the end: the video is 4\.00s long"
    ):
        engine.preview("sheet", [clip], {"at": [9.0]})


def test_only_thumb_and_sheet_apply_to_a_video(engine: Engine, clip: str) -> None:
    for kind in ("crop", "compare"):
        with pytest.raises(ValueError, match=r"thumb \(one frame\) or sheet"):
            engine.preview(kind, [clip], {})


def test_preview_frames_rejects_images(engine: Engine, photo: str) -> None:
    with pytest.raises(SpecInvalid, match="must be video, but 'a0' is image"):
        engine.run(
            {
                "inputs": {"a0": photo},
                "steps": [{"id": "p", "op": "preview.frames", "in": ["a0"], "args": {}}],
                "outputs": ["p"],
                "checks": "none",
            }
        )


def test_the_agents_preview_tool_shows_video_frames_and_spends_budget(
    engine: Engine, clip: str
) -> None:
    box = Toolbox(engine, Budget(BudgetLimits(max_images=3)))
    sheet = box.dispatch("preview", {"assets": [clip], "kind": "contact_sheet"})
    assert not sheet.is_error and isinstance(sheet.content, list)
    image, caption = sheet.content
    assert image["type"] == "image"
    assert image["source"]["media_type"] in ("image/png", "image/jpeg")
    assert "tokens" in caption["text"] and box.budget.images == 1
    frame = box.dispatch("preview", {"assets": [clip], "kind": "thumb", "time": 1.5})
    assert not frame.is_error and box.budget.images == 2
    crop = box.dispatch("preview", {"assets": [clip], "kind": "crop", "region": [0, 0, 0.5, 0.5]})
    assert crop.is_error and "thumb" in str(crop.content) and box.budget.images == 2
