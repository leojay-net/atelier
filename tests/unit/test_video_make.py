"""Making video: slideshow, soundtrack, color and single frames."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageStat

from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, RunLimits, StepFailed, fuse
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

AV = FIXTURES / "av"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
FAST = {"encoder_preset": "veryfast"}


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(path)],
        capture_output=True, check=True,
    )  # fmt: skip
    return json.loads(out.stdout)  # type: ignore[no-any-return]


def stream(info: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((s for s in info["streams"] if s["codec_type"] == kind), None)


def frame(path: Path, t: float, tmp: Path) -> Image.Image:
    png = tmp / f"f_{path.stem}_{t}.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", str(png)],
        check=True,
    )  # fmt: skip
    return Image.open(png).convert("RGB")


def mean_rgb(im: Image.Image) -> tuple[float, float, float]:
    r, g, b = ImageStat.Stat(im).mean
    return r, g, b


def out_path(engine: Engine, result: JobResult) -> Path:
    return engine.store.path(result.outputs["out"].hash)


def export_step(src: str) -> dict[str, Any]:
    return {
        "id": "out",
        "op": "export",
        "in": src,
        "args": {"preset": "web_h264", "overrides": FAST},
    }


def picture(
    engine: Engine, tmp: Path, name: str, rgb: tuple[int, int, int], size: tuple[int, int]
) -> str:
    path = tmp / f"{name}.png"
    Image.new("RGB", size, rgb).save(path)
    return engine.store.put_file(path).handle


@pytest.fixture
def pictures(engine: Engine, tmp_path: Path) -> dict[str, str]:
    return {
        "red": picture(engine, tmp_path, "red", (230, 30, 30), (640, 360)),
        "green": picture(engine, tmp_path, "green", (30, 200, 30), (640, 360)),
        "blue": picture(engine, tmp_path, "blue", (30, 30, 230), (640, 360)),
        "tall": picture(engine, tmp_path, "tall", (240, 240, 240), (360, 640)),
    }


def slideshow(pictures: dict[str, str], names: list[str], **args: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "inputs": {n: pictures[n] for n in dict.fromkeys(names)},
        "steps": [
            {"id": "show", "op": "video.slideshow", "in": names, "args": args},
            export_step("show"),
        ],
        "outputs": ["out"],
    }


# -- slideshow ---------------------------------------------------------------------------------


def test_each_picture_shows_for_its_time_in_order(
    engine: Engine, pictures: dict[str, str], tmp_path: Path
) -> None:
    result = engine.run(slideshow(pictures, ["red", "green", "blue"], seconds=1.0, short_side=240))
    assert result.passed, [r.line() for r in result.checks["out"]]
    path = out_path(engine, result)
    info = probe(path)
    v = stream(info, "video")
    assert v and (v["width"], v["height"]) == (426, 240) and v["r_frame_rate"] == "30/1"
    assert stream(info, "audio") is None  # a slideshow has no sound of its own
    assert abs(float(info["format"]["duration"]) - 3.0) < 0.1
    for t, dominant in ((0.5, 0), (1.5, 1), (2.5, 2)):
        rgb = mean_rgb(frame(path, t, tmp_path))
        assert rgb[dominant] == max(rgb) and rgb[dominant] > 100, (t, rgb)


def test_a_slideshow_is_one_encode(
    engine: Engine, pictures: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "ffmpeg.log"
    wrapper = tmp_path / "ffmpeg-logged"
    wrapper.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexec "{shutil.which("ffmpeg")}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("ATELIER_FFMPEG", str(wrapper))
    result = engine.run(slideshow(pictures, ["red", "green"], seconds=1.0, short_side=240))
    assert len(result.steps) == 1 and result.fused_steps == 1
    assert sum("libx264" in line for line in log.read_text().splitlines()) == 1


def test_crossfade_shortens_the_show_by_the_overlaps(
    engine: Engine, pictures: dict[str, str], tmp_path: Path
) -> None:
    result = engine.run(
        slideshow(pictures, ["red", "green", "blue"], seconds=2.0, short_side=240, fade=0.5)
    )
    path = out_path(engine, result)
    assert abs(float(probe(path)["format"]["duration"]) - 5.0) < 0.1  # 3 x 2 - 2 x 0.5
    mid = mean_rgb(frame(path, 1.75, tmp_path))  # in the red -> green dissolve
    assert mid[0] > 60 and mid[1] > 60


def test_pictures_of_another_shape_are_fitted_or_filled(
    engine: Engine, pictures: dict[str, str], tmp_path: Path
) -> None:
    fit = engine.run(slideshow(pictures, ["red", "tall"], seconds=1.0, short_side=240, mode="fit"))
    fill = engine.run(
        slideshow(pictures, ["red", "tall"], seconds=1.0, short_side=240, mode="fill")
    )
    left_fit = frame(out_path(engine, fit), 1.5, tmp_path).crop((0, 0, 60, 240))
    left_fill = frame(out_path(engine, fill), 1.5, tmp_path).crop((0, 0, 60, 240))
    assert ImageStat.Stat(left_fit.convert("L")).mean[0] < 10  # black bars beside a tall picture
    assert ImageStat.Stat(left_fill.convert("L")).mean[0] > 200  # the picture fills the frame


def test_shapes_and_sizes(engine: Engine, pictures: dict[str, str]) -> None:
    for shape, size in (("portrait", (240, 426)), ("square", (240, 240))):
        result = engine.run(slideshow(pictures, ["red", "green"], shape=shape, short_side=240))
        v = stream(probe(out_path(engine, result)), "video")
        assert v and (v["width"], v["height"]) == size


def test_slideshow_validation(engine: Engine, pictures: dict[str, str]) -> None:
    with pytest.raises(SpecInvalid, match=r"video.slideshow takes 2\+ input\(s\), got 1"):
        engine.validate(slideshow(pictures, ["red"]))
    with pytest.raises(SpecInvalid, match="must be less than half of seconds"):
        engine.validate(slideshow(pictures, ["red", "green"], seconds=1.0, fade=0.5))
    clip = engine.store.put_file(AV / "clip_landscape.mp4").handle
    with pytest.raises(SpecInvalid, match="must be image"):
        engine.validate(slideshow({"red": pictures["red"], "clip": clip}, ["red", "clip"]))


def test_the_same_picture_can_appear_twice(engine: Engine, pictures: dict[str, str]) -> None:
    result = engine.run(slideshow(pictures, ["red", "green", "red"], seconds=1.0, short_side=240))
    assert abs(float(probe(out_path(engine, result))["format"]["duration"]) - 3.0) < 0.1


def test_the_slideshow_key_includes_pillow_because_pictures_pass_through_it(
    engine: Engine, pictures: dict[str, str]
) -> None:
    spec = engine.validate(slideshow(pictures, ["red", "green"], short_side=240))
    from atelier.engine.runner import _group_resources, _types

    (group,) = fuse(spec)
    assert {"ffmpeg", "pillow"} <= set(_group_resources(group, _types(spec)))


# -- soundtrack --------------------------------------------------------------------------------


def music(path: Path, seconds: float, freq: int = 330) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency={freq}:duration={seconds}:sample_rate=48000", "-c:a", "pcm_s16le",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


def soundtrack_spec(video: str, track: str, **args: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "inputs": {"v": video, "m": track},
        "steps": [
            {"id": "s", "op": "video.soundtrack", "in": ["v", "m"], "args": args},
            export_step("s"),
        ],
        "outputs": ["out"],
    }


def pcm(path: Path, rate: int = 16000) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar",
         str(rate), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype=np.float32)


def level(x: np.ndarray, start: float, end: float, rate: int = 16000) -> float:
    seg = x[int(start * rate) : int(end * rate)]
    return float(np.sqrt(np.mean(seg**2))) if len(seg) else 0.0


@pytest.fixture
def clip(engine: Engine) -> str:
    return engine.store.put_file(AV / "clip_landscape.mp4").handle  # 4 s, with its own sound


def test_music_replaces_the_sound_and_fits_the_video(
    engine: Engine, clip: str, tmp_path: Path
) -> None:
    track = engine.store.put_file(music(tmp_path / "long.wav", 9.0)).handle
    result = engine.run(soundtrack_spec(clip, track, fade_out=1.0))
    assert result.passed, [r.line() for r in result.checks["out"]]
    path = out_path(engine, result)
    info = probe(path)
    audio, video = stream(info, "audio"), stream(info, "video")
    assert audio and video and abs(float(audio["duration"]) - float(video["duration"])) < 0.1
    x = pcm(path)
    assert level(x, 1.0, 2.0) > 0.05  # music is playing
    assert level(x, 3.9, 4.0) < level(x, 1.0, 2.0) * 0.3  # and fades out at the end


def test_short_music_loops_unless_told_not_to(engine: Engine, clip: str, tmp_path: Path) -> None:
    track = engine.store.put_file(music(tmp_path / "short.wav", 1.0)).handle
    looped = pcm(out_path(engine, engine.run(soundtrack_spec(clip, track, fade_out=0.0))))
    once = pcm(out_path(engine, engine.run(soundtrack_spec(clip, track, loop=False, fade_out=0.0))))
    assert level(looped, 2.5, 3.5) > 0.05  # still playing after a 1 s track would have ended
    assert level(once, 2.5, 3.5) < 0.005  # silence padded to the video's length
    for r in (looped, once):
        assert abs(len(r) / 16000 - 4.0) < 0.1


def test_mix_keeps_the_original_sound_and_adds_the_music(
    engine: Engine, clip: str, tmp_path: Path
) -> None:
    track = engine.store.put_file(music(tmp_path / "m.wav", 4.0, freq=1234)).handle
    both = pcm(out_path(engine, engine.run(soundtrack_spec(clip, track, mode="mix", fade_out=0))))
    only = pcm(
        out_path(engine, engine.run(soundtrack_spec(clip, track, mode="replace", fade_out=0)))
    )
    assert level(both, 0.5, 3.5) > level(only, 0.5, 3.5) * 1.05  # the clip's own sound is in there


def test_a_silent_video_can_take_music_in_either_mode(engine: Engine, tmp_path: Path) -> None:
    silent = engine.store.put_file(AV / "clip_silent.mp4").handle
    track = engine.store.put_file(music(tmp_path / "m.wav", 5.0)).handle
    for mode in ("replace", "mix"):
        result = engine.run(soundtrack_spec(silent, track, mode=mode))
        assert result.passed and stream(probe(out_path(engine, result)), "audio") is not None


def test_slideshow_with_music_is_one_graph(
    engine: Engine, pictures: dict[str, str], tmp_path: Path
) -> None:
    track = engine.store.put_file(music(tmp_path / "m.wav", 10.0)).handle
    spec = {
        "version": 1,
        "inputs": {"a": pictures["red"], "b": pictures["green"], "m": track},
        "steps": [
            {"id": "show", "op": "video.slideshow", "in": ["a", "b"],
             "args": {"seconds": 1.5, "short_side": 240}},
            {"id": "sound", "op": "video.soundtrack", "in": ["show", "m"], "args": {}},
            export_step("sound"),
        ],
        "outputs": ["out"],
    }  # fmt: skip
    result = engine.run(spec)
    assert result.passed and len(result.steps) == 1 and result.fused_steps == 2
    info = probe(out_path(engine, result))
    assert stream(info, "audio") and abs(float(info["format"]["duration"]) - 3.0) < 0.1


def test_soundtrack_and_concat_are_bit_identical_across_fresh_engines(
    engine: Engine, clip: str, tmp_path: Path
) -> None:
    """Joins read several inputs at once; the same inputs must still give the same bytes."""
    track_path = music(tmp_path / "m.wav", 6.0)
    spec = soundtrack_spec(clip, engine.store.put_file(track_path).handle, mode="mix")
    hashes = {engine.run(spec).outputs["out"].hash}
    for n in range(2):
        fresh = Engine.open(tmp_path / f"fresh{n}", run_limits=RunLimits(wall_seconds=60))
        v = fresh.store.put_file(AV / "clip_landscape.mp4").handle
        m = fresh.store.put_file(track_path).handle
        hashes.add(fresh.run(soundtrack_spec(v, m, mode="mix")).outputs["out"].hash)
    assert len(hashes) == 1


# -- color -------------------------------------------------------------------------------------


def color_spec(clip: str, **args: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "inputs": {"v": clip},
        "steps": [{"id": "c", "op": "video.color", "in": "v", "args": args}, export_step("c")],
        "outputs": ["out"],
    }


def test_color_changes_the_picture_and_leaves_the_sound(
    engine: Engine, clip: str, tmp_path: Path
) -> None:
    plain = frame(AV / "clip_landscape.mp4", 2.0, tmp_path)
    bright = engine.run(color_spec(clip, brightness=0.3))
    gray = engine.run(color_spec(clip, saturation=0.0))
    assert (
        ImageStat.Stat(frame(out_path(engine, bright), 2.0, tmp_path).convert("L")).mean[0]
        > ImageStat.Stat(plain.convert("L")).mean[0] + 20
    )
    r, g, b = mean_rgb(frame(out_path(engine, gray), 2.0, tmp_path))
    assert max(abs(r - g), abs(g - b)) < 6  # no color left
    assert stream(probe(out_path(engine, gray)), "audio") is not None


def test_color_with_nothing_to_change_is_refused(engine: Engine, clip: str) -> None:
    with pytest.raises(SpecInvalid, match="nothing to change"):
        engine.validate(color_spec(clip))
    with pytest.raises(SpecInvalid, match="saturation"):
        engine.validate(color_spec(clip, saturation=9))


# -- frame -------------------------------------------------------------------------------------


def test_a_frame_is_a_full_size_image(engine: Engine, clip: str, tmp_path: Path) -> None:
    spec = {
        "version": 1,
        "inputs": {"v": clip},
        "steps": [{"id": "f", "op": "video.frame", "in": "v", "args": {"time": 2.0}}],
        "outputs": ["f"],
    }
    result = engine.run(spec)
    assert result.outputs["f"].media.value == "image"
    im = Image.open(engine.store.path(result.outputs["f"].hash)).convert("RGB")
    assert im.size == (640, 360)
    ref = frame(AV / "clip_landscape.mp4", 2.0, tmp_path)
    assert (
        abs(ImageStat.Stat(im.convert("L")).mean[0] - ImageStat.Stat(ref.convert("L")).mean[0]) < 3
    )


def test_a_frame_feeds_image_ops(engine: Engine, clip: str) -> None:
    """The point of a frame: a thumbnail with a title, made from the video in one pipeline."""
    spec = {
        "version": 1,
        "inputs": {"v": clip},
        "steps": [
            {"id": "f", "op": "video.frame", "in": "v", "args": {"time": 1.0}},
            {"id": "t", "op": "image.text", "in": "f", "args": {"text": "Title"}},
            {"id": "out", "op": "export", "in": "t", "args": {"preset": "thumb_youtube"}},
        ],
        "outputs": ["out"],
    }
    result = engine.run(spec)
    assert result.passed, [r.line() for r in result.checks["out"]]


def test_a_frame_past_the_end_is_an_actionable_error(engine: Engine, clip: str) -> None:
    spec = {
        "version": 1,
        "inputs": {"v": clip},
        "steps": [{"id": "f", "op": "video.frame", "in": "v", "args": {"time": 9.0}}],
        "outputs": ["f"],
    }
    with pytest.raises(StepFailed, match=r"time 9s is past the end: the video is 4\.00s long"):
        engine.run(spec)
