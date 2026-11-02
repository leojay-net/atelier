"""video.concat and tree fusion: many clips, one decode, one encode."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops, ImageStat

from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, fuse
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


def frame(path: Path, t: float, tmp: Path, width: int | None = None) -> Image.Image:
    png = tmp / f"f_{path.stem}_{t}.png"
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(path)]
    argv += ["-frames:v", "1", *(["-vf", f"scale={width}:-2"] if width else []), str(png)]
    subprocess.run(argv, check=True)
    return Image.open(png).convert("RGB")


def closeness(a: Image.Image, b: Image.Image) -> float:
    b = b.resize(a.size)
    return float(ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0])


def luma(im: Image.Image) -> float:
    return ImageStat.Stat(im.convert("L")).mean[0]


@pytest.fixture
def handles(engine: Engine) -> dict[str, str]:
    return {
        name: engine.store.put_file(AV / f"{name}.mp4").handle
        for name in ("clip_landscape", "clip_portrait", "clip_silent")
    }


def cut(id_: str, src: str, start: float, end: float) -> dict[str, Any]:
    return {"id": id_, "op": "video.trim", "in": src, "args": {"start": start, "end": end}}


def joined(
    inputs: dict[str, str], steps: list[dict[str, Any]], parts: list[str], **args: Any
) -> dict[str, Any]:
    return {
        "version": 1,
        "inputs": inputs,
        "steps": [
            *steps,
            {"id": "j", "op": "video.concat", "in": parts, "args": args},
            {
                "id": "out",
                "op": "export",
                "in": "j",
                "args": {"preset": "web_h264", "overrides": FAST},
            },
        ],
        "outputs": ["out"],
    }


def out_path(engine: Engine, result: JobResult) -> Path:
    return engine.store.path(result.outputs["out"].hash)


@pytest.fixture
def ffmpeg_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "ffmpeg.log"
    wrapper = tmp_path / "ffmpeg-logged"
    wrapper.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexec "{shutil.which("ffmpeg")}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("ATELIER_FFMPEG", str(wrapper))
    return log


def encodes(log: Path) -> int:
    return sum("libx264" in line for line in log.read_text().splitlines()) if log.exists() else 0


# -- tree fusion: pure ---------------------------------------------------------------------------


def test_a_join_of_trims_is_one_group(engine: Engine, handles: dict[str, str]) -> None:
    spec = engine.validate(
        joined(
            {"a": handles["clip_landscape"], "b": handles["clip_portrait"]},
            [cut("x", "a", 0, 1), cut("y", "b", 0, 1)],
            ["x", "y"],
        )
    )
    (group,) = fuse(spec.canonical if hasattr(spec, "canonical") else spec)
    assert [s.id for s in group.steps] == ["x", "y", "j", "out"]
    assert group.inputs == ("a", "b")


def test_a_branch_someone_else_needs_stays_its_own_group(
    engine: Engine, handles: dict[str, str]
) -> None:
    raw = joined(
        {"a": handles["clip_landscape"], "b": handles["clip_portrait"]},
        [cut("x", "a", 0, 1), cut("y", "b", 0, 1)],
        ["x", "y"],
    )
    raw["steps"].append(
        {"id": "solo", "op": "export", "in": "x", "args": {"preset": "web_h264"}}
    )  # x now has two consumers
    raw["outputs"] = ["out", "solo"]
    groups = fuse(engine.validate(raw))
    assert [[s.id for s in g.steps] for g in groups] == [["x"], ["y", "j", "out"], ["solo"]]


def test_a_requested_branch_is_not_absorbed(engine: Engine, handles: dict[str, str]) -> None:
    raw = joined(
        {"a": handles["clip_landscape"], "b": handles["clip_portrait"]},
        [cut("x", "a", 0, 1), cut("y", "b", 0, 1)],
        ["x", "y"],
    )
    raw["outputs"] = ["out", "x"]
    groups = fuse(engine.validate(raw))
    assert [[s.id for s in g.steps] for g in groups] == [["x"], ["y", "j", "out"]]


# -- concatenation -------------------------------------------------------------------------------


def test_clips_play_in_order_and_are_encoded_once(
    engine: Engine, handles: dict[str, str], tmp_path: Path, ffmpeg_log: Path
) -> None:
    src = AV / "clip_landscape.mp4"
    spec = joined(
        {"a": handles["clip_landscape"]},
        [cut("x", "a", 0.0, 1.0), cut("y", "a", 3.0, 4.0), cut("z", "a", 1.5, 2.5)],
        ["x", "y", "z"],
    )
    result = engine.run(spec)
    assert result.passed, [r.line() for r in result.checks["out"]]
    assert encodes(ffmpeg_log) == 1  # three trims, a join and an export: one encode
    assert len(result.steps) == 1 and result.fused_steps == 4
    path = out_path(engine, result)
    info = probe(path)
    assert abs(float(info["format"]["duration"]) - 3.0) < 0.08
    video, audio = stream(info, "video"), stream(info, "audio")
    assert video and audio and (video["width"], video["height"]) == (640, 360)
    for t_out, t_src in ((0.5, 0.5), (1.5, 3.5), (2.5, 2.0)):
        ours = frame(path, t_out, tmp_path, 160)
        right = closeness(ours, frame(src, t_src, tmp_path, 160))
        wrong = closeness(ours, frame(src, (t_src + 1.7) % 3.9, tmp_path, 160))
        assert right < wrong, (t_out, t_src)


def test_the_same_source_can_feed_several_clips(engine: Engine, handles: dict[str, str]) -> None:
    result = engine.run(
        joined(
            {"a": handles["clip_landscape"]},
            [cut("x", "a", 0, 1), cut("y", "a", 2, 3)],
            ["x", "y"],
        )
    )
    assert result.passed and result.fused_steps == 3


def test_a_clip_of_another_shape_is_fitted_or_filled(
    engine: Engine, handles: dict[str, str], tmp_path: Path
) -> None:
    inputs = {"a": handles["clip_landscape"], "p": handles["clip_portrait"]}
    steps = [cut("x", "a", 0, 1), cut("y", "p", 0, 1)]
    fit = out_path(engine, engine.run(joined(inputs, steps, ["x", "y"], mode="fit")))
    fill = out_path(engine, engine.run(joined(inputs, steps, ["x", "y"], mode="fill")))
    for path in (fit, fill):
        v = stream(probe(path), "video")
        assert v and (v["width"], v["height"]) == (640, 360)  # the first clip's frame
    fitted, filled = frame(fit, 1.5, tmp_path), frame(fill, 1.5, tmp_path)
    side = fitted.crop((0, 0, 100, 360))  # a portrait clip in a landscape frame: bars at the sides
    assert luma(side) < 8
    assert luma(filled.crop((0, 0, 100, 360))) > luma(side) + 15  # filled: picture edge to edge


def test_a_silent_clip_gets_silence_so_sound_stays_in_step(
    engine: Engine, handles: dict[str, str]
) -> None:
    result = engine.run(
        joined(
            {"a": handles["clip_landscape"], "s": handles["clip_silent"]},
            [cut("x", "a", 0, 1.0), cut("y", "s", 0, 2.0)],
            ["x", "y"],
        )
    )
    assert result.passed, [r.line() for r in result.checks["out"]]  # av_sync included
    info = probe(out_path(engine, result))
    audio, video = stream(info, "audio"), stream(info, "video")
    assert audio and video
    assert abs(float(audio["duration"]) - float(video["duration"])) < 0.1
    assert abs(float(info["format"]["duration"]) - 3.0) < 0.1


def test_clips_that_all_lack_sound_stay_silent_files(
    engine: Engine, handles: dict[str, str]
) -> None:
    result = engine.run(
        joined(
            {"s": handles["clip_silent"]},
            [cut("x", "s", 0, 1), cut("y", "s", 1, 2)],
            ["x", "y"],
        )
    )
    assert result.passed
    assert stream(probe(out_path(engine, result)), "audio") is None


def test_a_crossfade_overlaps_the_clips(engine: Engine, handles: dict[str, str]) -> None:
    result = engine.run(
        joined(
            {"a": handles["clip_landscape"], "p": handles["clip_portrait"]},
            [cut("x", "a", 0, 2), cut("y", "p", 0, 2)],
            ["x", "y"],
            fade=0.5,
        )
    )
    assert result.passed, [r.line() for r in result.checks["out"]]
    info = probe(out_path(engine, result))
    assert abs(float(info["format"]["duration"]) - 3.5) < 0.1  # 2 + 2 - 0.5
    audio, video = stream(info, "audio"), stream(info, "video")
    assert audio and video and abs(float(audio["duration"]) - float(video["duration"])) < 0.1


def test_a_crossfade_longer_than_a_clip_can_take_says_so(
    engine: Engine, handles: dict[str, str]
) -> None:
    from atelier.engine.runner import StepFailed

    with pytest.raises(StepFailed, match=r"a 1\.00s clip is too short for a 0\.6s crossfade"):
        engine.run(
            joined(
                {"a": handles["clip_landscape"]},
                [cut("x", "a", 0, 1), cut("y", "a", 1, 3)],
                ["x", "y"],
                fade=0.6,
            )
        )


def test_the_result_is_cached_and_only_a_changed_clip_reruns(
    engine: Engine, handles: dict[str, str]
) -> None:
    spec = joined(
        {"a": handles["clip_landscape"]}, [cut("x", "a", 0, 1), cut("y", "a", 2, 3)], ["x", "y"]
    )
    first = engine.run(spec)
    assert not first.steps[0].cached
    assert engine.run(spec).steps[0].cached  # the whole graph is one cached unit
    spec["steps"][1]["args"]["end"] = 2.5
    assert not engine.run(spec).steps[0].cached


def test_a_join_needs_two_to_twenty_four_videos(engine: Engine, handles: dict[str, str]) -> None:
    with pytest.raises(SpecInvalid, match=r"video.concat takes 2\+ input\(s\), got 1"):
        engine.validate(joined({"a": handles["clip_landscape"]}, [], ["a"]))
    many = {f"i{n}": handles["clip_landscape"] for n in range(25)}
    with pytest.raises(SpecInvalid, match="at most 24 clips"):
        engine.validate(joined(many, [], list(many)))
    tone = engine.store.put_file(AV / "tone.wav").handle
    with pytest.raises(SpecInvalid, match="must be video"):
        engine.validate(joined({"a": handles["clip_landscape"], "t": tone}, [], ["a", "t"]))


# -- a join makes picture and sound together: using only one of them must still work ------------


def two_clips(handles: dict[str, str]) -> dict[str, Any]:
    return {"a": handles["clip_landscape"], "b": handles["clip_portrait"]}


def test_a_joined_video_can_be_loudness_normalized(engine: Engine, handles: dict[str, str]) -> None:
    """The loudness pass reads only the sound of the join; the join's picture output must be
    closed off or ffmpeg refuses the graph ("Error binding filtergraph inputs/outputs")."""
    spec = joined(two_clips(handles), [cut("x", "a", 0, 1), cut("y", "b", 0, 1)], ["x", "y"])
    spec["steps"][-1]["args"] = {
        "preset": "reels_1080x1920",
        "overrides": {**FAST, "size": {"width": 216, "height": 384, "mode": "fill"}},
    }
    result = engine.run(spec)
    assert result.passed, [r.line() for r in result.checks["out"]]
    assert result.outputs["out"].meta["loudness"] is not None


def test_a_joined_video_can_lose_its_sound_or_its_picture(
    engine: Engine, handles: dict[str, str]
) -> None:
    base = joined(two_clips(handles), [cut("x", "a", 0, 1), cut("y", "b", 0, 1)], ["x", "y"])
    muted = {**base, "steps": [*base["steps"][:-1]]}
    muted["steps"].append({"id": "m", "op": "video.mute", "in": "j", "args": {}})
    muted["steps"].append({**base["steps"][-1], "in": "m"})
    silent = stream(probe(out_path(engine, engine.run(muted))), "audio")
    assert silent is None

    extract = {**base, "steps": [*base["steps"][:-1]]}
    extract["steps"].append({"id": "sound", "op": "audio.extract", "in": "j", "args": {}})
    extract["steps"].append(
        {"id": "out", "op": "export", "in": "sound", "args": {"preset": "wav_master"}}
    )
    info = probe(out_path(engine, engine.run(extract)))
    assert stream(info, "video") is None and stream(info, "audio") is not None
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.1
