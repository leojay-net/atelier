"""Video specs are validated before anything runs: media types, presets, args, catalogue."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atelier import presets
from atelier.engine.engine import Engine
from atelier.ops.catalogue import describe_name, full_catalogue
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

AV = FIXTURES / "av"


@pytest.fixture
def clip(engine: Engine) -> str:
    return engine.store.put_file(AV / "clip_landscape.mp4").handle


@pytest.fixture
def tone(engine: Engine) -> str:
    return engine.store.put_file(AV / "tone.wav").handle


def errors(engine: Engine, spec: Any) -> list[str]:
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(spec)
    return [e.line() for e in exc.value.errors]


def linear(handle: str, *ops: Any) -> dict[str, Any]:
    return {"in": handle, "ops": [list(o) for o in ops]}


# -- export knows which presets fit which media ------------------------------------------------


def test_an_image_preset_on_a_video_says_which_presets_fit(engine: Engine, clip: str) -> None:
    (line,) = errors(engine, linear(clip, ("export", {"preset": "web_jpeg"})))
    assert line.startswith("ops[0].args.preset: 'web_jpeg' encodes image, but the input is video")
    assert "web_h264" in line and "reels_1080x1920" in line and "youtube_1080p" in line
    assert "web_webp" not in line  # only presets that fit a video are offered


def test_a_video_preset_on_an_image_says_which_presets_fit(engine: Engine, photo: str) -> None:
    (line,) = errors(engine, linear(photo, ("export", {"preset": "web_h264"})))
    assert "'web_h264' encodes video, but the input is image" in line
    assert "web_jpeg" in line and "og_image" in line and "reels_1080x1920" not in line


def test_a_video_preset_on_audio_is_refused(engine: Engine, tone: str) -> None:
    (line,) = errors(engine, linear(tone, ("export", {"preset": "reels_1080x1920"})))
    assert "encodes video, but the input is audio" in line


def test_an_unknown_preset_lists_every_export_preset_with_a_suggestion(
    engine: Engine, clip: str
) -> None:
    (line,) = errors(engine, linear(clip, ("export", {"preset": "reel_1080x1920"})))
    assert "'reel_1080x1920' invalid" in line and "did you mean reels_1080x1920?" in line


def test_overrides_are_checked_against_the_presets_own_settings(engine: Engine, clip: str) -> None:
    (line,) = errors(
        engine, linear(clip, ("export", {"preset": "web_h264", "overrides": {"quality": 80}}))
    )
    assert "export has no setting 'quality'" in line and "crf" in line
    (bad,) = errors(
        engine, linear(clip, ("export", {"preset": "web_h264", "overrides": {"crf": 99}}))
    )
    assert "crf" in bad and "less than or equal to 51" in bad
    (both,) = errors(
        engine,
        linear(clip, ("export", {"preset": "web_h264", "overrides": {"fps": 24, "max_fps": 30}})),
    )
    assert "give fps or max_fps, not both" in both


def test_the_expanded_preset_is_part_of_the_canonical_spec(engine: Engine, clip: str) -> None:
    spec = engine.validate(linear(clip, ("export", {"preset": "reels_1080x1920"})))
    args = spec.steps[0].args
    preset = presets.all_presets()["reels_1080x1920"]
    assert args["preset_version"] == preset.version == 1
    assert args["settings"]["size"] == {
        "width": 1080,
        "height": 1920,
        "mode": "fill",
        "focus_x": 0.5,
        "focus_y": 0.5,
    }
    assert args["settings"]["max_duration"] == 90 and args["settings"]["crf"] == 20


# -- ops check their inputs' media --------------------------------------------------------------


def test_video_ops_reject_images_and_audio(engine: Engine, photo: str, tone: str) -> None:
    (line,) = errors(engine, linear(photo, ("video.trim", {"start": 1})))
    assert line == "ops[0]: video.trim input 1 must be video, but 'in' is image" or (
        "video.trim input 1 must be video, but 'in' is image" in line
    )
    (audio,) = errors(engine, linear(tone, ("video.fade", {"fade_in": 1})))
    assert "must be video, but 'in' is audio" in audio


def test_image_ops_reject_video(engine: Engine, clip: str) -> None:
    (line,) = errors(engine, linear(clip, ("image.resize", {"width": 100})))
    assert "must be image, but 'in' is video" in line


def test_overlay_needs_a_video_and_an_image(engine: Engine, clip: str, logo: str) -> None:
    good = {
        "version": 1,
        "inputs": {"v": clip, "logo": logo},
        "steps": [{"id": "o", "op": "video.overlay", "in": ["v", "logo"], "args": {}}],
        "outputs": ["o"],
    }
    assert engine.validate(good).steps[0].op.name == "video.overlay"
    swapped = {**good, "steps": [{**good["steps"][0], "in": ["logo", "v"]}]}
    lines = errors(engine, swapped)
    assert any("input 1 must be video, but 'logo' is image" in ln for ln in lines)
    assert any("input 2 must be image, but 'v' is video" in ln for ln in lines)


@pytest.mark.parametrize(
    ("op", "args", "needle"),
    [
        ("video.trim", {"start": 5, "end": 2}, "end (2s) must be after start (5s)"),
        ("video.trim", {"start": "1:99"}, "invalid"),
        ("video.speed", {"factor": 9}, "factor"),
        ("video.speed", {}, "required by video.speed"),
        ("video.reframe", {"aspect": "portrait"}, "aspect"),
        ("video.reframe", {"aspect": "9:16", "mode": "zoom"}, "fill"),
        ("video.fade", {}, "set fade_in and/or fade_out"),
        ("video.fade", {"fade_in": -1}, "fade_in"),
    ],
)
def test_bad_args_are_reported_at_their_path(
    engine: Engine, clip: str, op: str, args: dict[str, Any], needle: str
) -> None:
    (line,) = errors(engine, linear(clip, (op, args)))
    assert line.startswith("ops[0].args") and needle in line


def test_timecodes_normalize_so_equivalent_specs_share_a_cache_key(
    engine: Engine, clip: str
) -> None:
    a = engine.validate(linear(clip, ("video.trim", {"start": "0:05", "end": "0:20"})))
    b = engine.validate(linear(clip, ("video.trim", {"start": 5, "end": 20.0})))
    assert a.steps[0].args == b.steps[0].args == {"start": 5.0, "end": 20.0}
    assert a.hash == b.hash


# -- the catalogue -----------------------------------------------------------------------------


def test_catalogue_lists_video_ops_presets_and_the_polymorphic_export() -> None:
    from atelier.ops.registry import load_builtin_ops

    text = full_catalogue(load_builtin_ops())
    assert "export(preset,overrides={}) image|video|audio|pdf|docx|xlsx|pptx→same" in text
    for name in ("video.trim", "video.reframe", "video.speed", "video.fade", "video.overlay"):
        assert name in text
    assert "video,image→video" in text  # overlay takes a video and an image
    for preset in ("web_h264", "reels_1080x1920", "youtube_1080p"):
        assert f"preset {preset} [export.video v1]" in text


@pytest.mark.parametrize(
    "name",
    [
        "video.trim",
        "video.reframe",
        "video.speed",
        "video.fade",
        "video.overlay",
        "video.text",
        "video.captions",
        "video.concat",
        "video.slideshow",
        "video.soundtrack",
        "video.color",
        "reels_1080x1920",
        "youtube_4k",
    ],
)
def test_describe_covers_video_ops_and_presets(name: str) -> None:
    from atelier.ops.registry import load_builtin_ops

    text = describe_name(load_builtin_ops(), name)
    assert text and name in text
    if name.startswith("video."):
        assert "params:" in text


def test_a_step_that_uses_video_needs_no_ffmpeg_to_validate_or_plan(
    engine: Engine, clip: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validation looks only at media types. (Running needs ffmpeg; the error then says so.)"""
    monkeypatch.setenv("PATH", str(tmp_path))
    spec = engine.validate(
        linear(clip, ("video.trim", {"end": 2}), ("export", {"preset": "web_h264"}))
    )
    assert [s.op.name for s in spec.steps] == ["video.trim", "export"]
