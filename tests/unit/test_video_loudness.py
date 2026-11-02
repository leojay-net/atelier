"""Two-pass EBU R128 loudness normalization for video export."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atelier.av.graph import Graph, Node, Source, measure_file, parse_loudnorm
from atelier.checks import Status
from atelier.checks.video import loudness_target
from atelier.engine.engine import Engine
from atelier.media import MediaType

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

GOOD = b"""
[Parsed_loudnorm_0 @ 0x1]
{
	"input_i" : "-21.83",
	"input_tp" : "-20.79",
	"input_lra" : "0.00",
	"input_thresh" : "-32.00",
	"output_i" : "-14.20",
	"target_offset" : "0.20"
}
"""


# -- parsing and graph pruning: pure -----------------------------------------------------------


def test_parse_loudnorm_reads_the_measurement() -> None:
    m = parse_loudnorm(GOOD)
    assert m == {
        "input_i": -21.83, "input_tp": -20.79, "input_lra": 0.0,
        "input_thresh": -32.0, "target_offset": 0.2,
    }  # fmt: skip


def test_parse_loudnorm_uses_the_last_block_and_survives_noise() -> None:
    noisy = b"log line {not json}\n" + GOOD + b"\nframe=  1 fps=0.0\n"
    assert parse_loudnorm(noisy) is not None
    older = GOOD.replace(b"-21.83", b"-10.00") + b"\n" + GOOD
    assert parse_loudnorm(older)["input_i"] == -21.83  # type: ignore[index]


@pytest.mark.parametrize(
    "stderr",
    [
        b"",
        b"no json at all",
        b"{ broken",
        GOOD.replace(b"-21.83", b"-inf"),
        GOOD.replace(b'"input_tp"', b'"x"'),
    ],
    ids=["empty", "text", "broken", "silent", "missing-key"],
)
def test_parse_loudnorm_returns_none_when_there_is_nothing_to_use(stderr: bytes) -> None:
    """Digital silence measures as -inf; there is no gain to compute for it."""
    assert parse_loudnorm(stderr) is None


def test_only_the_nodes_feeding_a_stream_are_measured(tmp_path: Path) -> None:
    """The measuring pass maps audio only, so video branches must not be in its graph (ffmpeg
    refuses a graph with unconnected outputs)."""
    g = Graph([Source(tmp_path / "x", MediaType.VIDEO, ".mp4", {})], tmp_path)
    g.nodes = [
        Node("[0:v:0]scale=w=10[v1]", ("0:v:0",), ("v1",)),
        Node("[0:a:0]atrim=start=1[a1]", ("0:a:0",), ("a1",)),
        Node("[v1]fade=t=in[v2]", ("v1",), ("v2",)),
        Node("[a1]afade=t=in[a2]", ("a1",), ("a2",)),
        Node("[0:a:0]volume=2[unused]", ("0:a:0",), ("unused",)),
    ]
    assert [n.text for n in g._feeding("a2")] == ["[0:a:0]atrim=start=1[a1]", "[a1]afade=t=in[a2]"]
    assert [n.text for n in g._feeding("v2")] == ["[0:v:0]scale=w=10[v1]", "[v1]fade=t=in[v2]"]
    assert g._feeding("0:a:0") == []  # a raw input needs no filters at all


# -- the check: pure ---------------------------------------------------------------------------


def meta(
    integrated: float, peak: float, target: float = -14.0, ceiling: float = -1.0
) -> dict[str, Any]:
    return {
        "export": {"loudness": {"target_lufs": target, "true_peak_db": ceiling}},
        "loudness": {"integrated": integrated, "true_peak": peak, "range": 3.0},
    }


def test_loudness_check_passes_within_tolerance_and_fails_with_a_hint() -> None:
    assert loudness_target(meta(-14.3, -2.0)).status is Status.PASS  # type: ignore[union-attr]
    far = loudness_target(meta(-18.0, -6.0))
    assert far is not None and far.status is Status.FAIL
    assert "-18.0 LUFS, preset wants -14" in far.message and far.hint
    hot = loudness_target(meta(-14.0, 0.5))
    assert hot is not None and hot.status is Status.FAIL and "true peak 0.5 dBTP" in hot.message


def test_loudness_check_does_not_apply_without_a_target_or_a_measurement() -> None:
    assert loudness_target({"export": {"loudness": None}, "loudness": None}) is None
    assert (
        loudness_target({"export": {"loudness": {"target_lufs": -14, "true_peak_db": -1}}}) is None
    )  # silent
    assert loudness_target({"export": None}) is None
    assert loudness_target({}) is None


# -- real audio ---------------------------------------------------------------------------------


def make_clip(path: Path, audio: str | None) -> Path:
    """A 3 s clip with the given lavfi audio graph (None = no audio)."""
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=10:duration=3",
    ]
    if audio:
        argv += ["-f", "lavfi", "-i", audio]
    argv += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    argv += ["-c:a", "aac", "-b:a", "128k", "-shortest"] if audio else ["-an"]
    subprocess.run([*argv, str(path)], check=True)
    return path


def loudness_of(path: Path) -> dict[str, float]:
    m = measure_file(path)
    assert m is not None
    return m


def export_spec(handle: str, **overrides: Any) -> dict[str, Any]:
    small = {"encoder_preset": "veryfast", "fps": 10}
    small["size"] = {"width": 90, "height": 160, "mode": "fill"}  # type: ignore[assignment]
    args = {"preset": "reels_1080x1920", "overrides": {**small, **overrides}}
    return {"in": handle, "ops": [["export", args]]}


@needs_ffmpeg
@pytest.mark.parametrize(
    ("audio", "why"),
    [
        ("sine=frequency=300:duration=3,volume=0.02", "very quiet"),
        ("sine=frequency=300:duration=3,volume=0.4", "already fairly loud"),
        ("anoisesrc=color=pink:duration=3:amplitude=0.3", "noise"),
    ],
    ids=["quiet", "loud", "noise"],
)
def test_any_source_lands_on_the_target(
    engine: Engine, tmp_path: Path, audio: str, why: str
) -> None:
    handle = engine.store.put_file(make_clip(tmp_path / "c.mp4", audio)).handle
    result = engine.run(export_spec(handle))
    assert result.passed, [r.line() for r in result.checks["s1"]]
    out = engine.store.path(result.outputs["s1"].hash)
    m = loudness_of(out)  # measured independently of the engine
    assert abs(m["input_i"] - (-14.0)) < 0.6, why
    assert m["input_tp"] <= -1.0 + 0.5
    reported = result.outputs["s1"].meta["loudness"]
    assert (
        abs(reported["integrated"] - m["input_i"]) < 0.1
    )  # what the engine says is what a viewer gets


@needs_ffmpeg
def test_the_target_is_overridable_and_can_be_switched_off(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(
        make_clip(tmp_path / "c.mp4", "sine=frequency=300:duration=3,volume=0.1")
    ).handle
    quiet = engine.run(export_spec(handle, loudness={"target_lufs": -23.0, "true_peak_db": -2.0}))
    assert abs(loudness_of(engine.store.path(quiet.outputs["s1"].hash))["input_i"] - (-23.0)) < 0.6
    raw = engine.run(export_spec(handle, loudness=None))
    assert raw.outputs["s1"].meta["loudness"] is None  # not normalized, so not measured
    assert not any(r.name == "loudness_target" for r in raw.checks["s1"])
    assert raw.passed


@needs_ffmpeg
def test_silent_audio_is_left_alone_and_does_not_fail(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(
        make_clip(tmp_path / "c.mp4", "anullsrc=r=48000:cl=stereo")
    ).handle
    result = engine.run(export_spec(handle))
    assert result.passed
    meta_out = result.outputs["s1"].meta
    assert meta_out["audio"] is not None  # the track is kept
    assert meta_out["loudness"] is None  # nothing to measure, nothing to normalize


@needs_ffmpeg
def test_a_clip_without_audio_needs_no_loudness_pass(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(make_clip(tmp_path / "c.mp4", None)).handle
    result = engine.run(export_spec(handle))
    assert result.passed and result.outputs["s1"].meta["audio"] is None


@needs_ffmpeg
def test_loudness_is_part_of_the_preset_and_so_of_the_key(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(
        make_clip(tmp_path / "c.mp4", "sine=frequency=300:duration=3,volume=0.1")
    ).handle
    a = engine.validate(export_spec(handle))
    b = engine.validate(export_spec(handle, loudness={"target_lufs": -16.0}))
    assert a.steps[0].args["settings"]["loudness"]["target_lufs"] == -14.0
    assert a.hash != b.hash
