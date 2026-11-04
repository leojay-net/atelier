"""Unit tests for newly added video editing ops: crop, pip, transition, lower_third."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atelier.engine.engine import Engine
from conftest import FIXTURES

AV = FIXTURES / "av"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
FAST = {"encoder_preset": "veryfast"}


def export(preset: str = "web_h264", **overrides: Any) -> list[Any]:
    return ["export", {"preset": preset, "overrides": {**FAST, **overrides}}]


def probe(path: Path) -> dict[str, Any]:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    out = subprocess.run([*cmd, str(path)], capture_output=True, check=True)
    return json.loads(out.stdout)


def vstream(info: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in info["streams"] if s["codec_type"] == "video")


def test_video_crop(engine: Engine) -> None:
    h = engine.store.put_file(AV / "clip_landscape.mp4").handle
    spec = {
        "in": h,
        "ops": [
            ["video.crop", {"box": [0.1, 0.1, 0.6, 0.6]}],
            export(),
        ],
    }
    res = engine.run(spec)
    out_step = next(iter(res.outputs.values()))
    info = probe(engine.store.path(out_step.hash))
    v = vstream(info)
    assert v["width"] == 384  # 640 * 0.6
    assert v["height"] == 216  # 360 * 0.6


def test_video_pip(engine: Engine) -> None:
    main_h = engine.store.put_file(AV / "clip_landscape.mp4").handle
    pip_h = engine.store.put_file(AV / "clip_portrait.mp4").handle
    spec = {
        "inputs": {"main": main_h, "facecam": pip_h},
        "steps": [
            {
                "id": "pip_step",
                "op": "video.pip",
                "in": ["main", "facecam"],
                "args": {"scale": 0.3, "anchor": "bottom_right", "margin": 0.05},
            },
            {
                "id": "out",
                "op": "export",
                "in": ["pip_step"],
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }
    res = engine.run(spec)
    info = probe(engine.store.path(res.outputs["out"].hash))
    v = vstream(info)
    assert v["width"] == 640
    assert v["height"] == 360


def test_video_transition(engine: Engine) -> None:
    clip1 = engine.store.put_file(AV / "clip_landscape.mp4").handle
    clip2 = engine.store.put_file(AV / "clip_landscape.mp4").handle
    spec = {
        "inputs": {"c1": clip1, "c2": clip2},
        "steps": [
            {
                "id": "trans",
                "op": "video.transition",
                "in": ["c1", "c2"],
                "args": {"transition": "wipeleft", "duration": 0.5},
            },
            {
                "id": "out",
                "op": "export",
                "in": ["trans"],
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }
    res = engine.run(spec)
    info = probe(engine.store.path(res.outputs["out"].hash))
    assert float(info["format"]["duration"]) > 1.0


def test_video_lower_third(engine: Engine) -> None:
    h = engine.store.put_file(AV / "clip_landscape.mp4").handle
    spec = {
        "in": h,
        "ops": [
            [
                "video.lower_third",
                {
                    "name": "Jane Doe",
                    "title": "Lead Architect",
                    "start": 0.2,
                    "duration": 1.0,
                    "position": "bottom_left",
                },
            ],
            export(),
        ],
    }
    res = engine.run(spec)
    out_step = next(iter(res.outputs.values()))
    info = probe(engine.store.path(out_step.hash))
    v = vstream(info)
    assert v["width"] == 640
    assert v["height"] == 360
