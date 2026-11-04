"""Video pipelines end to end through the worker sandbox and real ffmpeg.

Outputs are verified independently of the engine: structure with ffprobe (duration, size, fps,
codecs) and content with real decoded frames, never by comparing encoder bytes across machines.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops, ImageStat

from atelier.engine.engine import Engine, format_plan, format_result
from atelier.engine.runner import JobResult, RunLimits, StepFailed
from conftest import FIXTURES

AV = FIXTURES / "av"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
FAST = {"encoder_preset": "veryfast"}


def export(preset: str = "web_h264", **overrides: Any) -> list[Any]:
    return ["export", {"preset": preset, "overrides": {**FAST, **overrides}}]


def spec(handle: str, *ops: Any) -> dict[str, Any]:
    return {"in": handle, "ops": [list(o) for o in ops]}


@pytest.fixture
def clip(engine: Engine) -> str:
    return engine.store.put_file(AV / "clip_landscape.mp4").handle


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(out.stdout)  # type: ignore[no-any-return]


def stream(data: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((s for s in data["streams"] if s["codec_type"] == kind), None)


def output(engine: Engine, result: JobResult, step: str | None = None) -> Path:
    rep = result.outputs[step or next(iter(result.outputs))]
    return engine.store.path(rep.hash)


def frame(path: Path, t: float, tmp: Path) -> Image.Image:
    png = tmp / f"frame_{t}.png"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(t),
            "-i",
            str(path),
            "-frames:v",
            "1",
            str(png),
        ],
        check=True,
    )
    return Image.open(png).convert("RGB")


def luma(im: Image.Image) -> float:
    return ImageStat.Stat(im.convert("L")).mean[0]


# -- trim, frame-accurate ----------------------------------------------------------------------


def test_trim_and_export_is_frame_accurate_and_in_sync(engine: Engine, clip: str) -> None:
    result = engine.run(spec(clip, ("video.trim", {"start": 0.5, "end": 3.0}), export()))
    assert result.passed
    info = probe(output(engine, result))
    v, a = stream(info, "video"), stream(info, "audio")
    assert v is not None and a is not None
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"]) == ("h264", 640, 360, "yuv420p")
    assert (
        v["r_frame_rate"] == "30/1" and a["codec_name"] == "aac" and int(a["sample_rate"]) == 48000
    )
    assert abs(float(info["format"]["duration"]) - 2.5) < 0.05
    assert abs(float(v["duration"]) - float(a["duration"])) < 0.1  # picture and sound cut alike


def test_trim_uses_the_right_frames(engine: Engine, clip: str, tmp_path: Path) -> None:
    """testsrc2 burns a clock into the picture: the first output frame must be the 0.5 s one."""
    result = engine.run(spec(clip, ("video.trim", {"start": 2.0, "end": 3.0}), export()))
    first, expected = (
        frame(output(engine, result), 0.0, tmp_path),
        frame(AV / "clip_landscape.mp4", 2.0, tmp_path),
    )
    diff = ImageChops.difference(first, expected)
    assert luma(diff) < 6  # the same picture, allowing for the re-encode


def test_open_ended_trim_keeps_the_rest(engine: Engine, clip: str) -> None:
    result = engine.run(spec(clip, ("video.trim", {"start": 1.0}), export()))
    assert abs(float(probe(output(engine, result))["format"]["duration"]) - 3.0) < 0.06


def test_trim_past_the_end_is_an_actionable_error(engine: Engine, clip: str) -> None:
    with pytest.raises(StepFailed) as exc:
        engine.run(spec(clip, ("video.trim", {"start": 0, "end": 9}), export()))
    assert (
        exc.value.reason == "trim end 9s is past the end: the video is 4.00s long (use end <= 4.00)"
    )
    with pytest.raises(
        StepFailed, match=r"trim start 5s is past the end: the video is 4\.00s long"
    ):
        engine.run(spec(clip, ("video.trim", {"start": 5}), export()))


# -- speed -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("factor", "seconds"), [(2.0, 2.0), (0.5, 8.0), (4.0, 1.0)])
def test_speed_changes_duration_and_keeps_audio_in_sync(
    engine: Engine, clip: str, factor: float, seconds: float
) -> None:
    result = engine.run(spec(clip, ("video.speed", {"factor": factor}), export()))
    info = probe(output(engine, result))
    v, a = stream(info, "video"), stream(info, "audio")
    assert v is not None and a is not None
    assert abs(float(v["duration"]) - seconds) < 0.15
    assert abs(float(a["duration"]) - seconds) < 0.15
    assert v["r_frame_rate"] == "30/1"  # resampled to the preset's frame rate


# -- fade --------------------------------------------------------------------------------------


def test_fade_goes_through_black_at_both_ends(engine: Engine, clip: str, tmp_path: Path) -> None:
    result = engine.run(spec(clip, ("video.fade", {"fade_in": 1.0, "fade_out": 1.0}), export()))
    path = output(engine, result)
    first, middle, last = (
        frame(path, 0.0, tmp_path),
        frame(path, 2.0, tmp_path),
        frame(path, 3.9, tmp_path),
    )
    assert luma(first) < 5 and luma(last) < 25 and luma(middle) > 60


def test_fades_longer_than_the_video_are_refused(engine: Engine, clip: str) -> None:
    with pytest.raises(StepFailed, match=r"fades \(5s\) are longer than the video \(4.00s\)"):
        engine.run(spec(clip, ("video.fade", {"fade_in": 2.5, "fade_out": 2.5}), export()))


# -- reframe -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "aspect", "size"),
    [
        ("fill", "9:16", (202, 360)),
        ("fill", "1:1", (360, 360)),
        ("fit", "1:1", (640, 640)),
        ("blur_pad", "9:16", (640, 1136)),
    ],
)
def test_reframe_modes_produce_the_shape(
    engine: Engine, clip: str, mode: str, aspect: str, size: tuple[int, int]
) -> None:
    result = engine.run(spec(clip, ("video.reframe", {"aspect": aspect, "mode": mode}), export()))
    v = stream(probe(output(engine, result)), "video")
    assert v is not None and (v["width"], v["height"]) == size


def test_fit_pads_with_black_and_fill_keeps_only_the_middle(
    engine: Engine, clip: str, tmp_path: Path
) -> None:
    fit = engine.run(spec(clip, ("video.reframe", {"aspect": "1:1", "mode": "fit"}), export()))
    top = frame(output(engine, fit), 1.0, tmp_path).crop((0, 0, 640, 100))
    assert luma(top) < 6  # black bar above the picture
    fill = engine.run(
        spec(clip, ("video.reframe", {"aspect": "1:1", "mode": "fill", "focus_x": 0.0}), export())
    )
    left = frame(output(engine, fill), 1.0, tmp_path).crop((0, 100, 60, 260))
    assert luma(left) > 40  # focus_x=0 keeps the left edge, so it is picture, not black


def test_a_rotated_phone_clip_is_upright_after_export(engine: Engine) -> None:
    handle = engine.store.put_file(AV / "clip_rotated.mp4").handle
    result = engine.run(spec(handle, export()))
    v = stream(probe(output(engine, result)), "video")
    assert v is not None and (v["width"], v["height"]) == (
        360,
        640,
    )  # displayed shape, rotation baked in
    assert not v.get("side_data_list")  # no leftover rotation flag


# -- overlay -----------------------------------------------------------------------------------


def overlay_spec(clip: str, logo: str, **args: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "inputs": {"v": clip, "logo": logo},
        "steps": [
            {"id": "o", "op": "video.overlay", "in": ["v", "logo"], "args": args},
            {
                "id": "out",
                "op": "export",
                "in": "o",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }


@pytest.fixture
def black(engine: Engine, tmp_path: Path) -> str:
    """3 s of flat black: anything bright in an output frame can only be the overlay."""
    path = tmp_path / "black.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "color=c=black:s=640x360:r=30:d=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )  # fmt: skip
    return engine.store.put_file(path).handle


def bright_box(path: Path, t: float, tmp: Path) -> tuple[int, int, int, int] | None:
    return frame(path, t, tmp).convert("L").point(lambda v: 255 if v > 150 else 0).getbbox()


def test_overlay_lands_where_asked(engine: Engine, black: str, logo: str, tmp_path: Path) -> None:
    result = engine.run(overlay_spec(black, logo, anchor="bottom_right", scale=0.2, margin=0.05))
    box = bright_box(output(engine, result), 1.0, tmp_path)
    assert box is not None
    x0, y0, x1, y1 = box
    assert x1 > 640 * 0.9 and y1 > 360 * 0.85 and x0 > 640 * 0.7 and y0 > 360 * 0.7  # bottom right
    assert x1 <= 640 * 0.96 + 2 and y1 <= 360 * 0.96 + 2  # inside the margin
    assert 100 <= (x1 - x0) <= 135  # about 20% of the width


@pytest.mark.parametrize(
    ("anchor", "x_range", "y_range"),
    [
        ("top_left", (0, 0.15), (0, 0.2)),
        ("center", (0.4, 0.6), (0.4, 0.6)),
        ("bottom_left", (0, 0.15), (0.75, 1)),
    ],
)
def test_overlay_anchors(
    engine: Engine,
    black: str,
    logo: str,
    tmp_path: Path,
    anchor: str,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
) -> None:
    result = engine.run(overlay_spec(black, logo, anchor=anchor, scale=0.2, margin=0.02))
    box = bright_box(output(engine, result), 1.0, tmp_path)
    assert box is not None
    cx, cy = (box[0] + box[2]) / 2 / 640, (box[1] + box[3]) / 2 / 360
    assert x_range[0] <= cx <= x_range[1] + 0.1 and y_range[0] <= cy <= y_range[1] + 0.1


def test_overlay_time_window(engine: Engine, black: str, logo: str, tmp_path: Path) -> None:
    path = output(
        engine, engine.run(overlay_spec(black, logo, anchor="top_left", start=1.0, end=2.0))
    )
    assert bright_box(path, 0.4, tmp_path) is None  # not yet
    assert bright_box(path, 1.5, tmp_path) is not None  # in the window
    assert bright_box(path, 2.7, tmp_path) is None  # gone again


def test_overlay_opacity(engine: Engine, black: str, logo: str, tmp_path: Path) -> None:
    solid = frame(
        output(engine, engine.run(overlay_spec(black, logo, anchor="center", opacity=1.0))),
        1.0,
        tmp_path,
    )
    faint = frame(
        output(engine, engine.run(overlay_spec(black, logo, anchor="center", opacity=0.4))),
        1.0,
        tmp_path,
    )
    assert luma(faint) < luma(solid) * 0.6 and luma(faint) > luma(solid) * 0.25


def test_overlay_records_its_box_for_the_safe_area_check(
    engine: Engine, clip: str, logo: str
) -> None:
    result = engine.run(overlay_spec(clip, logo, anchor="top_left", margin=0.02))
    (box,) = result.outputs["out"].meta["boxes"]
    assert box["kind"] == "overlay" and box["label"] == "logo"
    x, y, w, h = box["box"]
    assert 0 <= x < 0.1 and 0 <= y < 0.1 and 0.1 < w < 0.2 and h > 0


@pytest.mark.parametrize("anchor", ["top_right", "bottom_left", "bottom_right", "top_left"])
def test_a_corner_logo_on_a_reel_stays_inside_the_platform_safe_area(
    engine: Engine, clip: str, logo: str, anchor: str
) -> None:
    """The everyday ask, "make it a Reel with my logo in the corner", with default margins."""
    result = engine.run(
        {
            "version": 1,
            "inputs": {"v": clip, "logo": logo},
            "steps": [
                {"id": "t", "op": "video.trim", "in": "v", "args": {"end": 1}},
                {"id": "r", "op": "video.reframe", "in": "t", "args": {"aspect": "9:16"}},
                {"id": "o", "op": "video.overlay", "in": ["r", "logo"], "args": {"anchor": anchor}},
                {
                    "id": "out",
                    "op": "export",
                    "in": "o",
                    "args": {"preset": "reels_1080x1920", "overrides": FAST},
                },
            ],
            "outputs": ["out"],
        }
    )
    safe = {r.name: r for r in result.checks["out"]}["safe_margins"]
    assert safe.status.value == "pass", safe.line()


# -- presets and checks ------------------------------------------------------------------------


def test_reels_preset_gives_exact_vertical_video_from_a_landscape_clip(
    engine: Engine, clip: str
) -> None:
    result = engine.run(spec(clip, ("video.trim", {"end": 2}), export("reels_1080x1920")))
    assert result.passed, [r.line() for r in result.checks["s2"]]
    v = stream(probe(output(engine, result)), "video")
    assert (
        v is not None and (v["width"], v["height"]) == (1080, 1920) and v["r_frame_rate"] == "30/1"
    )
    names = {r.name for r in result.checks["s2"]}
    assert {"playable", "av_sync", "resolution", "frame_rate", "duration_max"} <= names


def test_a_clip_over_the_platform_limit_fails_the_duration_check_with_a_hint(
    engine: Engine, tmp_path: Path
) -> None:
    long = tmp_path / "long.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=100",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(long),
        ],
        check=True,
    )
    handle = engine.store.put_file(long).handle
    small = {"size": {"width": 180, "height": 320, "mode": "fill"}, "fps": 10}
    result = engine.run(spec(handle, export("reels_1080x1920", **small)))
    assert not result.passed
    (bad,) = [r for r in result.checks["s1"] if r.status.value == "fail"]
    assert (
        bad.name == "duration_max"
        and "100.0s exceeds the 90s limit of reels_1080x1920" in bad.message
    )
    assert bad.hint and "video.trim" in bad.hint


def test_a_logo_in_the_platform_ui_zone_fails_the_safe_margins_check(
    engine: Engine, clip: str, logo: str
) -> None:
    small = {"size": {"width": 216, "height": 384, "mode": "fill"}}
    bad = overlay_spec(clip, logo, anchor="top_left", margin=0.01)
    bad["steps"][1]["args"] = {"preset": "reels_1080x1920", "overrides": {**FAST, **small}}
    result = engine.run(bad)
    (fail,) = [r for r in result.checks["out"] if r.status.value == "fail"]
    assert (
        fail.name == "safe_margins"
        and "safe margin" in fail.message
        and "anchor" in (fail.hint or "")
    )
    good = overlay_spec(clip, logo, anchor="center", scale=0.1)
    good["steps"][1]["args"] = {"preset": "reels_1080x1920", "overrides": {**FAST, **small}}
    assert engine.run(good).passed


def test_a_silent_clip_exports_with_a_warning_not_a_failure(engine: Engine) -> None:
    handle = engine.store.put_file(AV / "clip_silent.mp4").handle
    result = engine.run(spec(handle, export()))
    assert result.passed  # a warning does not fail the job
    (warn,) = [r for r in result.checks["s1"] if r.status.value == "warn"]
    assert warn.name == "audio_present"
    assert stream(probe(output(engine, result)), "audio") is None
    assert stream(probe(output(engine, result)), "video")["r_frame_rate"] == "25/1"  # type: ignore[index]


def test_frame_rate_is_kept_capped_or_forced(engine: Engine, clip: str) -> None:
    kept = engine.run(spec(clip, export()))  # web_h264: max 30, source is 30
    assert stream(probe(output(engine, kept)), "video")["r_frame_rate"] == "30/1"  # type: ignore[index]
    forced = engine.run(spec(clip, export(fps=15, max_fps=None)))
    v = stream(probe(output(engine, forced)), "video")
    assert v is not None and v["r_frame_rate"] == "15/1" and forced.passed


def test_web_preset_caps_the_long_edge_and_never_enlarges(engine: Engine, clip: str) -> None:
    big = engine.run(
        spec(
            clip,
            ("video.reframe", {"aspect": "9:16", "mode": "blur_pad"}),
            export("web_h264", size={"width": 400, "height": 400, "mode": "max"}),
        )
    )
    v = stream(probe(output(engine, big)), "video")
    assert (
        v is not None
        and max(v["width"], v["height"]) <= 400
        and v["width"] % 2 == 0
        and v["height"] % 2 == 0
    )
    same = engine.run(spec(clip, export()))  # 640x360 is inside 1920x1920: untouched
    assert (stream(probe(output(engine, same)), "video") or {})["width"] == 640


def test_an_audio_only_input_cannot_take_a_video_op(engine: Engine) -> None:
    handle = engine.store.put_file(AV / "tone.wav").handle
    from atelier.spec.errors import SpecInvalid

    with pytest.raises(SpecInvalid):
        engine.run(spec(handle, ("video.fade", {"fade_in": 1})))


# -- fusion: one decode, one encode ------------------------------------------------------------


@pytest.fixture
def ffmpeg_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route ffmpeg through a wrapper that logs every invocation, so encodes can be counted."""
    log = tmp_path / "ffmpeg.log"
    real = shutil.which("ffmpeg")
    wrapper = tmp_path / "ffmpeg-logged"
    wrapper.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexec "{real}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("ATELIER_FFMPEG", str(wrapper))
    return log


def encodes(log: Path) -> int:
    return sum("libx264" in line for line in log.read_text().splitlines()) if log.exists() else 0


def test_a_fused_pipeline_is_encoded_exactly_once(
    engine: Engine, clip: str, logo: str, ffmpeg_log: Path
) -> None:
    del logo
    ops = (
        ("video.trim", {"start": 0.5, "end": 3.5}),
        ("video.reframe", {"aspect": "9:16", "mode": "blur_pad"}),
        ("video.speed", {"factor": 1.5}),
        ("video.fade", {"fade_in": 0.3, "fade_out": 0.3}),
        export("web_h264"),
    )
    result = engine.run(spec(clip, *ops))
    assert (
        encodes(ffmpeg_log) == 1
    )  # five steps, one encode (the other ffmpeg call verifies playback)
    assert (
        len(result.steps) == 1
        and result.fused_steps == 4
        and result.steps[0].fused == ("s1", "s2", "s3", "s4")
    )
    assert (
        format_result(result)
        .splitlines()[-1]
        .startswith("cache: 0/1 steps reused · 5 steps in 1 runs (fused)")
    )
    assert format_plan(engine.plan(spec(clip, *ops))).startswith(
        "valid · 5 steps → 1 runs · 1 cached"
    )


def test_an_intermediate_someone_else_needs_is_encoded_once_and_shared(
    engine: Engine, clip: str, ffmpeg_log: Path
) -> None:
    """`cut` feeds two exports, so it is materialized (one intermediate encode), not fused."""
    both = {
        "version": 1,
        "inputs": {"v": clip},
        "steps": [
            {"id": "cut", "op": "video.trim", "in": "v", "args": {"end": 2}},
            {
                "id": "web",
                "op": "export",
                "in": "cut",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
            {
                "id": "yt",
                "op": "export",
                "in": "cut",
                "args": {"preset": "youtube_1080p", "overrides": FAST},
            },
        ],
        "outputs": ["web", "yt"],
    }
    result = engine.run(both)
    assert encodes(ffmpeg_log) == 3  # the intermediate, then each export
    assert result.fused_steps == 0 and len(result.steps) == 3
    assert result.passed


def test_a_requested_intermediate_is_materialized(
    engine: Engine, clip: str, ffmpeg_log: Path
) -> None:
    both = {
        "version": 1,
        "inputs": {"v": clip},
        "steps": [
            {"id": "cut", "op": "video.trim", "in": "v", "args": {"end": 2}},
            {
                "id": "out",
                "op": "export",
                "in": "cut",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["cut", "out"],
    }
    result = engine.run(both)
    assert encodes(ffmpeg_log) == 2 and result.fused_steps == 0
    assert result.outputs["cut"].media.value == "video"  # a visually lossless intermediate


def test_a_logo_made_by_a_later_listed_step_is_ready_when_the_chain_runs(
    engine: Engine, clip: str, logo: str
) -> None:
    """The chain starts at `cut` but needs `small`, which is listed after it: order by last step."""
    s = {
        "version": 1,
        "inputs": {"v": clip, "logo": logo},
        "steps": [
            {"id": "cut", "op": "video.trim", "in": "v", "args": {"end": 2}},
            {"id": "small", "op": "image.resize", "in": "logo", "args": {"width": 80}},
            {
                "id": "marked",
                "op": "video.overlay",
                "in": ["cut", "small"],
                "args": {"anchor": "center"},
            },
            {
                "id": "out",
                "op": "export",
                "in": "marked",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }
    result = engine.run(s)
    assert result.passed and [r.id for r in result.steps] == ["small", "out"]
    assert result.steps[1].fused == ("cut", "marked")


def test_fused_results_are_cached_and_invalidated_by_any_change(engine: Engine, clip: str) -> None:
    base = (("video.trim", {"end": 2}), export())
    first = engine.run(spec(clip, *base))
    again = engine.run(spec(clip, *base))
    assert again.reused == 1 and again.outputs["s2"].hash == first.outputs["s2"].hash
    tweaked = engine.run(spec(clip, ("video.trim", {"end": 2.5}), export()))
    assert tweaked.reused == 0  # a different step arg is a different group
    other = engine.run(spec(clip, ("video.trim", {"end": 2}), export("web_h264", crf=30)))
    assert other.reused == 0 and other.outputs["s2"].hash != first.outputs["s2"].hash
    same_meaning = engine.run(
        spec(clip, ("video.trim", {"start": "0:00", "end": "0:02"}), export())
    )
    assert same_meaning.reused == 1  # "0:02" and 2 canonicalize alike


def test_the_ffmpeg_build_is_part_of_the_key(engine: Engine, clip: str, tmp_path: Path) -> None:
    from atelier.engine import runner
    from atelier.engine.runner import _group_resources, fuse

    canon = engine.validate(spec(clip, export()))
    (group,) = fuse(canon)
    resources = _group_resources(group, runner._types(canon))
    assert set(resources) == {"ffmpeg", "ffprobe"} and all(
        v.startswith(("ffmpeg:", "ffprobe:")) for v in resources.values()
    )


def test_identical_pipelines_encode_to_identical_bytes(tmp_path: Path) -> None:
    hashes = []
    for name in ("a", "b"):
        eng = Engine.open(tmp_path / name, run_limits=RunLimits(wall_seconds=120))
        handle = eng.store.put_file(AV / "clip_landscape.mp4").handle
        result = eng.run(
            spec(
                handle,
                ("video.trim", {"end": 2}),
                ("video.reframe", {"aspect": "1:1", "mode": "blur_pad"}),
                export(),
            )
        )
        hashes.append(result.outputs["s3"].hash)
    assert hashes[0] == hashes[1]  # same machine, same ffmpeg: byte-identical


def test_image_steps_still_get_repeated_inputs_as_written(engine: Engine, photo: str) -> None:
    """Regression: refs were de-duplicated for every step, so a grid of one photo lost its cells."""
    s = {
        "version": 1,
        "inputs": {"p": photo},
        "steps": [
            {
                "id": "g",
                "op": "image.grid",
                "in": ["p", "p", "p", "p"],
                "args": {"columns": 2, "cell_width": 100, "gap": 0},
            }
        ],
        "outputs": ["g"],
    }
    rep = engine.run(s).outputs["g"]
    # four 100-px cells in two columns with no gap: exactly 2 x 2 cells. With de-duplicated inputs
    # the grid saw a single photo and produced one 100x100 cell.
    assert (rep.meta["width"], rep.meta["height"]) == (200, 200)


# -- failure and safety ------------------------------------------------------------------------


def test_a_damaged_video_fails_cleanly_without_paths(engine: Engine, tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8)
    handle = engine.store.put_file(bad).handle
    with pytest.raises(StepFailed) as exc:
        engine.run(spec(handle, export()))
    message = str(exc.value)
    assert "Invalid data" in message or "ffmpeg failed" in message or "unreadable" in message
    assert str(engine.home) not in message and "/store/" not in message


def test_a_wedged_ffmpeg_is_killed_at_the_wall_time_limit_with_its_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stuck = tmp_path / "ffmpeg-stuck"
    stuck.write_text("#!/bin/sh\nsleep 31.7\n")
    stuck.chmod(0o755)
    monkeypatch.setenv("ATELIER_FFMPEG", str(stuck))
    eng = Engine.open(tmp_path / "home", run_limits=RunLimits(wall_seconds=2))
    handle = eng.store.put_file(AV / "clip_landscape.mp4").handle
    with pytest.raises(StepFailed, match=r"timed out \(wall-time limit 2s\)"):
        eng.run(spec(handle, export()))
    left = subprocess.run(["pgrep", "-f", "sleep 31.7"], capture_output=True, text=True)
    assert left.stdout.strip() == ""  # the worker's whole process group died with it
