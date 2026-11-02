"""Titles and captions on video: real ffmpeg, judged by decoded frames of a black clip.

The source is flat black, so anything bright in a frame can only be the text. None of this needs
drawtext or libass in ffmpeg: the text is a Pillow-drawn PNG overlaid for a time window.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageStat

from atelier.av.text_ops import MAX_CUES, POSITIONS
from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, StepFailed
from atelier.spec.errors import SpecInvalid

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
FAST = {"encoder_preset": "veryfast"}

SRT = """1
00:00:00,500 --> 00:00:01,500
First words

2
00:00:02,000 --> 00:00:02,800
Second line
"""


def make_black(path: Path, size: str, seconds: int = 3) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s={size}:r=30:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def black(engine: Engine, tmp_path: Path) -> str:
    return engine.store.put_file(make_black(tmp_path / "black.mp4", "640x360")).handle


@pytest.fixture
def portrait(engine: Engine, tmp_path: Path) -> str:
    return engine.store.put_file(make_black(tmp_path / "portrait.mp4", "360x640")).handle


def srt(engine: Engine, tmp_path: Path, body: str = SRT, name: str = "c.srt") -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return engine.store.put_file(path).handle


def frame_at(path: Path, t: float, tmp: Path) -> Image.Image:
    png = tmp / f"f_{t}.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", str(png)],
        check=True,
    )  # fmt: skip
    return Image.open(png).convert("RGB")


def bright(im: Image.Image) -> tuple[int, int, int, int] | None:
    return im.convert("L").point(lambda v: 255 if v > 150 else 0).getbbox()


def pipeline(source: str, step: dict[str, Any], **export: Any) -> dict[str, Any]:
    step = dict(step)  # `inputs` and `extra` are for this helper, not for the spec
    inputs = {"v": source, **step.pop("inputs", {})}
    ins = ["v", *step.pop("extra", [])]
    return {
        "version": 1,
        "inputs": inputs,
        "steps": [
            {"id": "t", "in": ins, **step},
            {
                "id": "out",
                "op": "export",
                "in": "t",
                "args": {"preset": "web_h264", "overrides": {**FAST, **export}},
            },
        ],
        "outputs": ["out"],
    }


def text_spec(source: str, **args: Any) -> dict[str, Any]:
    return pipeline(source, {"op": "video.text", "args": args})


def captions_spec(source: str, cues: str, **args: Any) -> dict[str, Any]:
    return pipeline(
        source,
        {"op": "video.captions", "args": args, "extra": ["c"], "inputs": {"c": cues}},
    )


def out_path(engine: Engine, result: JobResult) -> Path:
    return engine.store.path(result.outputs["out"].hash)


# -- video.text --------------------------------------------------------------------------------


def test_text_shows_only_during_its_time_range(engine: Engine, black: str, tmp_path: Path) -> None:
    result = engine.run(text_spec(black, text="Big news", start=1.0, end=2.0))
    assert result.passed, [r.line() for r in result.checks["out"]]
    path = out_path(engine, result)
    assert bright(frame_at(path, 0.4, tmp_path)) is None  # not yet
    assert bright(frame_at(path, 1.5, tmp_path)) is not None  # on screen
    assert bright(frame_at(path, 2.6, tmp_path)) is None  # gone again


@pytest.mark.parametrize("position", list(POSITIONS))
def test_each_position_puts_the_text_in_its_band(
    engine: Engine, black: str, tmp_path: Path, position: str
) -> None:
    result = engine.run(text_spec(black, text="Hello there", position=position))
    box = bright(frame_at(out_path(engine, result), 1.0, tmp_path))
    assert box is not None
    (bx, by, bw, bh), _ = POSITIONS[position]
    x0, y0, x1, y1 = box
    assert x0 >= bx * 640 - 2 and x1 <= (bx + bw) * 640 + 2
    assert y0 >= by * 360 - 2 and y1 <= (by + bh) * 360 + 2


def test_a_style_and_color_change_the_look(engine: Engine, black: str, tmp_path: Path) -> None:
    plain = engine.run(text_spec(black, text="Hi", style="clean"))
    pop = engine.run(text_spec(black, text="Hi", style="bold_pop"))
    tinted = engine.run(text_spec(black, text="Hi", color="#ff0000"))
    big, small = (bright(frame_at(out_path(engine, r), 1.0, tmp_path)) for r in (pop, plain))
    assert big is not None and small is not None
    assert (big[2] - big[0]) > (small[2] - small[0])  # bold_pop is larger than clean
    red = frame_at(out_path(engine, tinted), 1.0, tmp_path)
    r, g, b = (sum(ImageStat.Stat(band).sum) for band in red.split())
    assert r > g * 3 and r > b * 3


def test_text_that_looks_like_a_filter_is_just_pixels(
    engine: Engine, black: str, tmp_path: Path
) -> None:
    hostile = "'[a]movie=/etc/passwd,drawtext=text=x;:\\"
    result = engine.run(text_spec(black, text=hostile))
    assert result.passed
    assert bright(frame_at(out_path(engine, result), 1.0, tmp_path)) is not None


def test_text_starting_after_the_end_is_an_actionable_error(engine: Engine, black: str) -> None:
    with pytest.raises(StepFailed, match=r"text starts at 9s but the video is only 3\.00s long"):
        engine.run(text_spec(black, text="late", start=9.0))


def test_text_validation_is_precise(engine: Engine, black: str) -> None:
    with pytest.raises(SpecInvalid, match="must be after start"):
        engine.validate(text_spec(black, text="x", start=2.0, end=1.0))
    with pytest.raises(SpecInvalid, match="position"):
        engine.validate(text_spec(black, text="x", position="under_the_rug"))
    with pytest.raises(SpecInvalid, match="text"):
        engine.validate(text_spec(black, text=""))


def test_text_fuses_with_trim_and_export_into_one_encode(engine: Engine, black: str) -> None:
    spec = {
        "version": 1,
        "inputs": {"v": black},
        "steps": [
            {"id": "a", "op": "video.trim", "in": ["v"], "args": {"start": 0.5}},
            {"id": "b", "op": "video.text", "in": ["a"], "args": {"text": "Hello"}},
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
    assert result.passed and result.fused_steps == 2  # three steps, one run
    assert result.steps[0].fused == ("a", "b")  # folded into the export that encodes them


def test_text_output_is_bit_identical_across_fresh_engines(
    engine: Engine, black: str, tmp_path: Path
) -> None:
    from atelier.engine.runner import RunLimits

    hashes = {engine.run(text_spec(black, text="Same every time")).outputs["out"].hash}
    for n in range(2):
        fresh = Engine.open(tmp_path / f"fresh{n}", run_limits=RunLimits(wall_seconds=60))
        handle = fresh.store.put_file(engine.store.path(engine.store.resolve(black).hash)).handle
        hashes.add(fresh.run(text_spec(handle, text="Same every time")).outputs["out"].hash)
    assert len(hashes) == 1


# -- video.captions ----------------------------------------------------------------------------


def test_captions_appear_with_their_cues(engine: Engine, black: str, tmp_path: Path) -> None:
    cues = srt(engine, tmp_path)
    result = engine.run(captions_spec(black, cues))
    assert result.passed, [r.line() for r in result.checks["out"]]
    path = out_path(engine, result)
    assert bright(frame_at(path, 0.2, tmp_path)) is None  # before the first cue
    first = bright(frame_at(path, 1.0, tmp_path))
    assert bright(frame_at(path, 1.75, tmp_path)) is None  # between the cues
    second = bright(frame_at(path, 2.4, tmp_path))
    assert first is not None and second is not None
    assert first != second  # different words
    assert (first[1] + first[3]) / 2 > 360 * 0.5  # in the lower half (lower_safe)
    assert bright(frame_at(path, 2.9, tmp_path)) is None


def test_shift_moves_every_cue(engine: Engine, black: str, tmp_path: Path) -> None:
    cues = srt(engine, tmp_path)
    late = out_path(engine, engine.run(captions_spec(black, cues, shift=0.4)))
    assert bright(frame_at(late, 0.6, tmp_path)) is None  # first cue now starts at 0.9
    assert bright(frame_at(late, 1.0, tmp_path)) is not None
    early = out_path(engine, engine.run(captions_spec(black, cues, shift=-0.4)))
    assert bright(frame_at(early, 0.2, tmp_path)) is not None  # ...and at 0.1 the other way


def test_markup_in_the_file_is_not_drawn(engine: Engine, black: str, tmp_path: Path) -> None:
    plain = srt(engine, tmp_path, "1\n00:00:00,500 --> 00:00:02,500\nHello\n", "p.srt")
    marked = srt(
        engine,
        tmp_path,
        "1\n00:00:00,500 --> 00:00:02,500\n<i>Hel</i>{\\an8}<font color=red>lo</font>\n",
        "m.srt",
    )
    a = bright(frame_at(out_path(engine, engine.run(captions_spec(black, plain))), 1, tmp_path))
    b = bright(frame_at(out_path(engine, engine.run(captions_spec(black, marked))), 1, tmp_path))
    assert a is not None and a == b  # the same words in the same place


def test_webvtt_works_too(engine: Engine, black: str, tmp_path: Path) -> None:
    vtt = srt(engine, tmp_path, "WEBVTT\n\n00:01.000 --> 00:02.000\nHello\n", "c.vtt")
    path = out_path(engine, engine.run(captions_spec(black, vtt)))
    assert bright(frame_at(path, 1.5, tmp_path)) is not None


def test_a_bad_file_says_which_line(engine: Engine, black: str, tmp_path: Path) -> None:
    bad = srt(engine, tmp_path, "1\n00:00:03,000 --> 00:00:01,000\nbackwards\n", "bad.srt")
    with pytest.raises(StepFailed, match=r"captions line 2: the cue ends \(1s\) before it starts"):
        engine.run(captions_spec(black, bad))


def test_captions_outside_the_video_are_an_error_not_a_silent_no_op(
    engine: Engine, black: str, tmp_path: Path
) -> None:
    late = srt(engine, tmp_path, "1\n00:00:30,000 --> 00:00:32,000\nToo late\n", "late.srt")
    with pytest.raises(StepFailed, match="no caption falls inside the video"):
        engine.run(captions_spec(black, late))


def test_too_many_cues_is_refused_with_a_way_out(
    engine: Engine, black: str, tmp_path: Path
) -> None:
    lines = [
        f"{i}\n00:00:00,{i % 900 + 10:03d} --> 00:00:02,000\nw{i}\n" for i in range(MAX_CUES + 1)
    ]
    many = srt(engine, tmp_path, "\n".join(lines), "many.srt")
    with pytest.raises(StepFailed, match=rf"{MAX_CUES + 1} captions is more than the {MAX_CUES}"):
        engine.run(captions_spec(black, many))


def test_a_video_is_not_accepted_as_the_caption_file(engine: Engine, black: str) -> None:
    with pytest.raises(SpecInvalid, match="must be text"):
        engine.validate(captions_spec(black, black))


# -- the shared safe-margin check --------------------------------------------------------------

REELS = {"preset": "reels_1080x1920", "overrides": {**FAST, "size": {"width": 216, "height": 384}}}


def reels(source: str, step: dict[str, Any]) -> dict[str, Any]:
    spec = pipeline(source, step)
    spec["steps"][1]["args"] = REELS
    return spec


def failures(result: JobResult) -> list[Any]:
    return [r for r in result.checks["out"] if r.status.value == "fail"]


def test_text_in_the_platform_ui_zone_fails_and_the_safe_position_passes(
    engine: Engine, portrait: str
) -> None:
    low = engine.run(
        reels(portrait, {"op": "video.text", "args": {"text": "Hi", "position": "bottom"}})
    )
    (fail,) = failures(low)
    assert fail.name == "safe_margins" and "bottom safe margin" in fail.message
    ok = engine.run(reels(portrait, {"op": "video.text", "args": {"text": "Hi"}}))
    assert ok.passed, [r.line() for r in ok.checks["out"]]


def test_captions_are_judged_by_the_same_check(
    engine: Engine, portrait: str, tmp_path: Path
) -> None:
    cues = srt(engine, tmp_path)
    step = {
        "op": "video.captions",
        "args": {"position": "top"},
        "extra": ["c"],
        "inputs": {"c": cues},
    }
    (fail,) = failures(engine.run(reels(portrait, step)))
    assert fail.name == "safe_margins" and "top safe margin" in fail.message
    good = {**step, "args": {"position": "lower_safe"}}
    assert engine.run(reels(portrait, good)).passed
