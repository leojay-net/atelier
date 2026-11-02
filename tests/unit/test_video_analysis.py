"""Black frames, frozen picture and dead air: found in the finished file, judged by checks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atelier.av.analysis import parse
from atelier.checks import Status
from atelier.checks.video import black_frames, dead_air, frozen_frames
from atelier.engine.engine import Engine

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
FAST = {"encoder_preset": "veryfast"}

# -- parsing: pure -----------------------------------------------------------------------------

LOG = """
[Parsed_blackdetect_0 @ 0x7f] black_start:0 black_end:1.2 black_duration:1.2
[Parsed_blackdetect_0 @ 0x7f] black_start:5.5 black_end:8 black_duration:2.5
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_start: 2
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_duration: 1.5
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_end: 3.5
[Parsed_freezedetect_1 @ 0x7f] lavfi.freezedetect.freeze_start: 6
[silencedetect @ 0x7f] silence_start: 1.25
[silencedetect @ 0x7f] silence_end: 4.5 | silence_duration: 3.25
"""


def test_the_detector_log_becomes_intervals() -> None:
    found = parse(LOG, duration=8.0)
    assert found["black"] == [[0.0, 1.2], [5.5, 8.0]]
    assert found["frozen"] == [[2.0, 3.5], [6.0, 8.0]]  # the last one runs to the end
    assert found["silent"] == [[1.25, 4.5]]


def test_noise_in_the_log_is_ignored_and_the_count_is_capped() -> None:
    many = "\n".join(f"black_start:{i} black_end:{i + 0.5} black_duration:0.5" for i in range(100))
    assert len(parse("garbage\n" + many, None)["black"]) == 32
    assert parse("", None) == {"black": [], "frozen": [], "silent": []}


# -- the checks: pure --------------------------------------------------------------------------


def meta(kind: str, intervals: list[list[float]], duration: float = 10.0) -> dict[str, Any]:
    return {"duration": duration, "analysis": {kind: intervals}}


def test_black_frames_flags_the_start_the_end_and_a_long_middle() -> None:
    assert black_frames(meta("black", [[0, 0.8]])).message.startswith("opens with 0.8s")  # type: ignore[union-attr]
    assert black_frames(meta("black", [[9.2, 10]])).message.startswith("ends with 0.8s")  # type: ignore[union-attr]
    assert "has 1.5s of black at 4.0s" in black_frames(meta("black", [[4, 5.5]])).message  # type: ignore[union-attr]
    for short in ([[0, 0.4]], [[9.7, 10]], [[4, 4.8]], []):  # a fade, a blink, a cut
        result = black_frames(meta("black", short))
        assert result is not None and result.status is Status.PASS


def test_the_checks_do_not_apply_without_an_analysis() -> None:
    assert black_frames({"duration": 5}) is None
    assert frozen_frames({"analysis": None}) is None
    assert dead_air({"analysis": {"black": []}}) is None  # a silent file has no `silent` entry


def test_frozen_frames_and_dead_air_have_their_own_thresholds() -> None:
    assert frozen_frames(meta("frozen", [[1, 2.5]])).status is Status.PASS  # type: ignore[union-attr]
    assert frozen_frames(meta("frozen", [[1, 3.5]])).status is Status.WARN  # type: ignore[union-attr]
    assert dead_air(meta("silent", [[1, 3.5]])).status is Status.PASS  # type: ignore[union-attr]
    long = dead_air(meta("silent", [[1, 5]]))
    assert long is not None and long.status is Status.WARN and "audio.silence" in (long.hint or "")


# -- end to end --------------------------------------------------------------------------------


def make(path: Path, graph: str, audio: bool = False) -> Path:
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", graph]
    argv += ["-map", "[v]", *(["-map", "[a]"] if audio else [])]
    argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if audio else [])]
    subprocess.run([*argv, str(path)], check=True)
    return path


def export(engine: Engine, path: Path, checks: str | list[str] = "default") -> Any:
    handle = engine.store.put_file(path).handle
    return engine.run(
        {
            "version": 1,
            "inputs": {"v": handle},
            "steps": [
                {
                    "id": "out",
                    "op": "export",
                    "in": "v",
                    "args": {"preset": "web_h264", "overrides": FAST},
                }
            ],
            "outputs": ["out"],
            "checks": checks,
        }
    )


def test_a_video_that_opens_with_black_gets_a_warning_with_a_hint(
    engine: Engine, tmp_path: Path
) -> None:
    clip = make(
        tmp_path / "b.mp4",
        "color=c=black:s=160x90:r=30:d=1[a];testsrc2=s=160x90:r=30:d=2[b];"
        "[a][b]concat=n=2:v=1:a=0[v]",
    )
    result = export(engine, clip)
    (warn,) = [r for r in result.checks["out"] if r.name == "black_frames"]
    assert warn.status is Status.WARN and "opens with 1.0s of black" in warn.message
    assert warn.hint and "video.trim" in warn.hint
    assert result.passed  # a warning does not fail the run
    analysis = result.outputs["out"].meta["analysis"]
    assert analysis["black"] and analysis["black"][0][0] == 0.0


def test_a_fine_video_passes_and_a_frozen_one_is_caught_when_asked(
    engine: Engine, tmp_path: Path
) -> None:
    live = make(tmp_path / "live.mp4", "testsrc2=s=160x90:r=30:d=3[v]")
    ok = export(engine, live, ["black_frames", "frozen_frames"])
    assert all(r.status is Status.PASS for r in ok.checks["out"] if r.name != "playable")
    still = make(tmp_path / "still.mp4", "color=c=red:s=160x90:r=30:d=4[v]")
    bad = export(engine, still, ["frozen_frames"])
    (warn,) = [r for r in bad.checks["out"] if r.name == "frozen_frames"]
    assert warn.status is Status.WARN and "stands still for" in warn.message


def test_an_intermediate_is_not_analyzed(engine: Engine, tmp_path: Path) -> None:
    clip = make(tmp_path / "c.mp4", "testsrc2=s=160x90:r=30:d=3[v]")
    handle = engine.store.put_file(clip).handle
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [{"id": "cut", "op": "video.trim", "in": "v", "args": {"end": 2}}],
        "outputs": ["cut"],
    }
    assert engine.run(spec).outputs["cut"].meta["analysis"] is None


def test_podcast_presets_check_for_dead_air(engine: Engine, tmp_path: Path) -> None:
    quiet = tmp_path / "quiet.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex",
         "sine=frequency=440:duration=1[a];anullsrc=r=48000:cl=mono:d=4[b];[a][b]concat=n=2:v=0:a=1",
         "-ar", "48000", "-ac", "1", str(quiet)],
        check=True,
    )  # fmt: skip
    handle = engine.store.put_file(quiet).handle
    result = engine.run(
        {"in": handle, "ops": [["export", {"preset": "podcast_mp3"}]]},
    )
    (warn,) = [r for r in result.checks["s1"] if r.name == "dead_air"]
    assert warn.status is Status.WARN and "silence from" in warn.message
