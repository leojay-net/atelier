"""Regressions from the 2026-11-04 live user test (docs/13-live-user-test-2026-11-04.md)."""

from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

from atelier.av.ops import SpeedParams, _atempo_chain
from atelier.ops.image.text import check_glyphs
from atelier.ops.registry import OpError


def test_the_model_client_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import atelier.api.settings as settings

    monkeypatch.delenv("ATELIER_MODEL_CLIENT", raising=False)
    assert importlib.reload(settings).ATELIER_MODEL_CLIENT.endswith(".vultr_client")
    monkeypatch.setenv("ATELIER_MODEL_CLIENT", "atelier.api.core.clients.ollama_client")
    assert importlib.reload(settings).ATELIER_MODEL_CLIENT.endswith(".ollama_client")
    monkeypatch.delenv("ATELIER_MODEL_CLIENT")
    importlib.reload(settings)


def test_quarter_speed_slow_motion_is_allowed() -> None:
    assert SpeedParams(factor=0.25).factor == 0.25
    assert _atempo_chain(0.25) == [0.5, 0.5]
    with pytest.raises(ValueError):
        SpeedParams(factor=0.2)


@pytest.mark.parametrize("text", ["It's 50% off — “ok” • €", "Nguyễn Привет Ελληνικά", "⚠️"])
def test_text_the_font_can_draw_passes(text: str) -> None:
    check_glyphs(text, "Inter", "bold")


@pytest.mark.parametrize(("text", "named"), [("Launch 🎬", "🎬"), ("中文字幕", "中")])
def test_text_the_font_cannot_draw_fails_naming_the_characters(text: str, named: str) -> None:
    with pytest.raises(OpError, match=named):
        check_glyphs(text, "Inter", "bold")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_slideshow_shows_whole_photos_over_a_blur_by_default(tmp_path: Path) -> None:
    """Live test: a landscape family photo in a vertical reel lost both parents to a center
    crop. The default now shows all of it over a blurred copy (no black bars, nothing cut)."""
    from PIL import Image

    from atelier.engine.engine import Engine

    wide = Image.new("RGB", (800, 400), "red")
    wide.paste(Image.new("RGB", (400, 400), "blue"), (400, 0))
    wide.save(tmp_path / "wide.png")
    engine = Engine.open(tmp_path / "store")
    pic = engine.store.put_file(tmp_path / "wide.png").handle
    result = engine.run(
        {
            "version": 1,
            "inputs": {"a": pic, "b": pic},
            "steps": [
                {
                    "id": "s",
                    "op": "video.slideshow",
                    "in": ["a", "b"],
                    "args": {"seconds": 1, "shape": "portrait", "short_side": 360},
                },
                {"id": "out", "op": "export", "in": "s", "args": {"preset": "web_h264"}},
            ],
            "outputs": ["out"],
        }
    )
    frame = tmp_path / "f.png"
    video = engine.store.path(result.outputs["out"].hash)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            "0.5",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
    )
    im = Image.open(frame).convert("RGB")
    w, h = im.size
    left, right = im.getpixel((5, h // 2)), im.getpixel((w - 6, h // 2))
    assert left[0] > 150 > left[2] and right[2] > 150 > right[0]  # both edges of the photo
    top = im.getpixel((w // 2, 5))
    assert sum(top) > 60  # a blurred copy above the photo, not black bars


def test_code_on_a_slide_keeps_its_indentation() -> None:
    """Live: a tutorial slide showed `for fruit in fruits:` / `print(fruit)` unindented."""
    from atelier.ops.image.text import _font, _wrap

    font = _font("Inter", "bold", 40)
    assert _wrap("for fruit in fruits:\n    print(fruit)", font, 2000) == [
        "for fruit in fruits:",
        "    print(fruit)",
    ]
    assert _wrap("\tx = 1", font, 2000) == ["    x = 1"]
