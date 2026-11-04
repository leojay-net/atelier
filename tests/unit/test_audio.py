"""Audio ops and export presets, through real ffmpeg. Outputs are verified with ffprobe, an
independent loudness measurement, and decoded samples, not by comparing encoder bytes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from atelier.av.ducking import ANALYSIS_RATE, CONTROL_RATE, duck_curve
from atelier.av.graph import measure_file
from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits, StepFailed
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

AV = FIXTURES / "av"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


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


def samples(path: Path, rate: int = 16000) -> np.ndarray:
    """Mono float samples of the whole file, decoded by ffmpeg."""
    raw = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def tone(path: Path, spec: str, seconds: float = 4.0) -> Path:
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
            spec,
            "-t",
            str(seconds),
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )
    return path


def audio(engine: Engine, name: str = "tone.wav") -> str:
    return engine.store.put_file(AV / name).handle


def spec(handle: str, *ops: Any) -> dict[str, Any]:
    return {"in": handle, "ops": [list(o) for o in ops]}


def out_path(engine: Engine, result: Any) -> Path:
    return engine.store.path(next(iter(result.outputs.values())).hash)


# -- export presets ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset", "codec", "container", "rate", "channels"),
    [
        ("podcast_mp3", "mp3", "mp3", 44100, 2),
        ("podcast_m4a", "aac", "mov,mp4,m4a,3gp,3g2,mj2", 44100, 2),
        ("voice_opus", "opus", "ogg", 48000, 1),
        ("wav_master", "pcm_s24le", "wav", 48000, 2),
    ],
)
def test_every_audio_preset_produces_the_right_file(
    engine: Engine, preset: str, codec: str, container: str, rate: int, channels: int
) -> None:
    result = engine.run(spec(audio(engine), ["export", {"preset": preset}]))
    assert result.passed, [r.line() for rs in result.checks.values() for r in rs]
    info = probe(out_path(engine, result))
    (stream,) = info["streams"]
    assert (
        stream["codec_name"] == codec
        and int(stream["sample_rate"]) == rate
        and stream["channels"] == channels
    )
    assert info["format"]["format_name"] == container
    assert abs(float(info["format"]["duration"]) - 3.0) < 0.1
    tags = set(info["format"].get("tags", {}))
    assert tags <= {"major_brand", "minor_version", "compatible_brands"}  # structure, not metadata
    assert not tags & {"encoder", "creation_time", "title", "artist", "comment"}
    assert result.outputs["s1"].media.value == "audio"


@pytest.mark.parametrize("preset", ["podcast_mp3", "podcast_m4a", "voice_opus"])
def test_lossy_audio_presets_land_on_minus_16_lufs(engine: Engine, preset: str) -> None:
    result = engine.run(spec(audio(engine), ["export", {"preset": preset}]))
    m = measure_file(out_path(engine, result))  # measured independently of the engine
    assert m is not None and abs(m["input_i"] - (-16.0)) < 0.8 and m["input_tp"] <= -0.5
    assert any(
        r.name == "loudness_target" and r.status.value == "pass" for r in result.checks["s1"]
    )


def test_the_wav_master_is_not_loudness_changed(engine: Engine) -> None:
    result = engine.run(spec(audio(engine), ["export", {"preset": "wav_master"}]))
    source, out = measure_file(AV / "tone.wav"), measure_file(out_path(engine, result))
    assert source and out and abs(source["input_i"] - out["input_i"]) < 0.2
    assert result.outputs["s1"].meta["loudness"] is None


def test_an_m4a_we_made_is_recognized_as_audio_when_imported_again(
    engine: Engine, tmp_path: Path
) -> None:
    result = engine.run(spec(audio(engine), ["export", {"preset": "podcast_m4a"}]))
    copy = tmp_path / "again.m4a"
    shutil.copy(out_path(engine, result), copy)
    assert engine.store.put_file(copy).media.value == "audio"  # the M4A brand, not a video mp4


def test_audio_presets_and_media_are_matched_with_a_hint(engine: Engine) -> None:
    clip = engine.store.put_file(AV / "clip_landscape.mp4").handle
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(spec(clip, ["export", {"preset": "podcast_mp3"}]))
    (line,) = [e.line() for e in exc.value.errors]
    assert (
        "'podcast_mp3' encodes audio, but the input is video "
        "(take the sound out first with audio.extract)" in line
    )
    with pytest.raises(SpecInvalid, match=r"'web_h264' encodes video, but the input is audio"):
        engine.validate(spec(audio(engine), ["export", {"preset": "web_h264"}]))


# -- ops ---------------------------------------------------------------------------------------


def test_extract_takes_the_sound_of_a_video_in_one_fused_encode(engine: Engine) -> None:
    clip = engine.store.put_file(AV / "clip_landscape.mp4").handle
    result = engine.run(
        {"recipe": "extract_audio", "params": {"in": clip, "format": "podcast_mp3"}}
    )
    assert result.passed and result.fused_steps == 1 and len(result.steps) == 1
    info = probe(out_path(engine, result))
    assert [s["codec_type"] for s in info["streams"]] == ["audio"]
    assert abs(float(info["format"]["duration"]) - 4.0) < 0.1


def test_a_video_trim_then_extract_is_still_one_run(engine: Engine) -> None:
    clip = engine.store.put_file(AV / "clip_landscape.mp4").handle
    result = engine.run(
        spec(
            clip,
            ["video.trim", {"start": 1, "end": 3}],
            ["audio.extract", {}],
            ["export", {"preset": "voice_opus"}],
        )
    )
    assert len(result.steps) == 1 and result.steps[0].fused == ("s1", "s2")
    assert abs(float(probe(out_path(engine, result))["format"]["duration"]) - 2.0) < 0.1


def test_extract_from_a_silent_video_says_so(engine: Engine) -> None:
    silent = engine.store.put_file(AV / "clip_silent.mp4").handle
    with pytest.raises(StepFailed, match=r"this video has no audio track to extract"):
        engine.run(spec(silent, ["audio.extract", {}], ["export", {"preset": "podcast_mp3"}]))


def test_audio_trim_is_exact_and_bounded(engine: Engine) -> None:
    result = engine.run(
        spec(
            audio(engine),
            ["audio.trim", {"start": 0.5, "end": 2.0}],
            ["export", {"preset": "wav_master"}],
        )
    )
    assert abs(float(probe(out_path(engine, result))["format"]["duration"]) - 1.5) < 0.02
    with pytest.raises(StepFailed, match=r"trim end 9s is past the end: the audio is 3\.00s long"):
        engine.run(
            spec(audio(engine), ["audio.trim", {"end": 9}], ["export", {"preset": "wav_master"}])
        )


def test_audio_fade_reaches_silence_at_the_ends(engine: Engine) -> None:
    result = engine.run(
        spec(
            audio(engine),
            ["audio.fade", {"fade_in": 0.5, "fade_out": 0.5}],
            ["export", {"preset": "wav_master"}],
        )
    )
    x = samples(out_path(engine, result))
    rate = 16000
    head, middle, tail = x[: rate // 50], x[rate : 2 * rate], x[-rate // 50 :]
    rms = lambda a: float(np.sqrt(np.mean(a**2)))  # noqa: E731
    assert rms(head) < 0.02 and rms(tail) < 0.02 and rms(middle) > 0.05


def test_the_loudnorm_op_normalizes_before_later_steps(engine: Engine) -> None:
    result = engine.run(
        spec(
            audio(engine),
            ["audio.loudnorm", {"target_lufs": -20}],
            ["export", {"preset": "wav_master"}],
        )
    )
    m = measure_file(out_path(engine, result))
    assert m is not None and abs(m["input_i"] - (-20.0)) < 0.6


def test_audio_ops_reject_video_and_the_other_way_round(engine: Engine) -> None:
    clip = engine.store.put_file(AV / "clip_landscape.mp4").handle
    with pytest.raises(SpecInvalid, match=r"audio\.trim input 1 must be audio, but 'in' is video"):
        engine.validate(spec(clip, ["audio.trim", {"end": 1}]))
    with pytest.raises(
        SpecInvalid, match=r"audio\.extract input 1 must be video, but 'in' is audio"
    ):
        engine.validate(spec(audio(engine), ["audio.extract", {}]))
    with pytest.raises(SpecInvalid, match=r"video\.trim input 1 must be video, but 'in' is audio"):
        engine.validate(spec(audio(engine), ["video.trim", {"end": 1}]))


# -- mix and ducking ---------------------------------------------------------------------------


def band(x: np.ndarray, freq: float, start: float, end: float, rate: int = 16000) -> float:
    """Amplitude of one frequency in a time window (a single-bin DFT)."""
    seg = x[int(start * rate) : int(end * rate)]
    t = np.arange(len(seg)) / rate
    return float(2 * np.abs(np.sum(seg * np.exp(-2j * np.pi * freq * t))) / len(seg))


@pytest.fixture
def voice_and_music(engine: Engine, tmp_path: Path) -> tuple[str, str]:
    """A 4 s 'voice' (440 Hz for 2 s, then silence) and steady 'music' at 200 Hz."""
    voice = tmp_path / "voice.wav"
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
            "sine=frequency=440:duration=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=2",
            "-filter_complex",
            "[0][1]concat=n=2:v=0:a=1,aformat=sample_rates=48000",
            str(voice),
        ],
        check=True,
    )
    music = tone(tmp_path / "music.wav", "sine=frequency=200:duration=4")
    return engine.store.put_file(voice).handle, engine.store.put_file(music).handle


def mix_spec(voice: str, music: str, **args: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "inputs": {"voice": voice, "music": music},
        "steps": [
            {"id": "m", "op": "audio.mix", "in": ["voice", "music"], "args": args},
            {"id": "out", "op": "export", "in": "m", "args": {"preset": "wav_master"}},
        ],
        "outputs": ["out"],
    }


def test_mix_ducks_the_music_only_while_the_voice_is_present(
    engine: Engine, voice_and_music: tuple[str, str]
) -> None:
    voice, music = voice_and_music
    ducked = samples(
        out_path(engine, engine.run(mix_spec(voice, music, duck=True, music_volume=0.5)))
    )
    plain = samples(
        out_path(engine, engine.run(mix_spec(voice, music, duck=False, music_volume=0.5)))
    )
    with_voice, without = (0.6, 1.6), (2.6, 3.6)
    d_in, d_out = band(ducked, 200, *with_voice), band(ducked, 200, *without)
    p_in, p_out = band(plain, 200, *with_voice), band(plain, 200, *without)
    assert d_out > 0.02 and p_out > 0.02  # music is audible when nobody speaks
    assert d_in < d_out * 0.6  # ducked while the voice speaks
    assert p_in > p_out * 0.85  # not ducked without the option
    assert band(ducked, 440, *with_voice) > 0.1  # the voice itself is untouched


def test_mix_is_bit_identical_on_every_run(
    engine: Engine, voice_and_music: tuple[str, str], tmp_path: Path
) -> None:
    """A compressor filter fed two inputs gave different bytes from run to run, because how
    its inputs are chunked depends on thread timing. The ducking gain is computed up front, so
    equal inputs must give equal bytes even with nothing cached."""
    voice, music = voice_and_music
    spec = mix_spec(voice, music, duck=True, music_volume=0.4)
    hashes = {engine.run(spec).outputs["out"].hash}
    for n in range(3):
        fresh = Engine.open(tmp_path / f"fresh{n}", run_limits=RunLimits(wall_seconds=30))
        for handle in (voice, music):
            fresh.store.put_file(engine.store.path(engine.store.resolve(handle).hash))
        hashes.add(fresh.run(spec).outputs["out"].hash)
    assert len(hashes) == 1


def test_mix_volume_sets_the_music_level_and_lasts_as_long_as_the_voice(
    engine: Engine, voice_and_music: tuple[str, str]
) -> None:
    voice, music = voice_and_music
    loud = samples(
        out_path(engine, engine.run(mix_spec(voice, music, duck=False, music_volume=0.8)))
    )
    soft = samples(
        out_path(engine, engine.run(mix_spec(voice, music, duck=False, music_volume=0.2)))
    )
    assert band(soft, 200, 2.6, 3.6) < band(loud, 200, 2.6, 3.6) * 0.4
    result = engine.run(mix_spec(voice, music))
    assert abs(float(probe(out_path(engine, result))["format"]["duration"]) - 4.0) < 0.05


def test_a_short_voice_over_a_longer_song_keeps_the_whole_song(
    engine: Engine, tmp_path: Path
) -> None:
    """length=music: a 2 s voice over 8 s of music lasts 8 s; the music dips under the voice
    and comes back to its full level afterwards (the live "add a voice to my reel" case)."""
    voice = tone(tmp_path / "v.wav", "sine=frequency=440:duration=2", seconds=2.0)
    music = tone(tmp_path / "m.wav", "sine=frequency=200:duration=8", seconds=8.0)
    v, m = engine.store.put_file(voice).handle, engine.store.put_file(music).handle
    path = out_path(engine, engine.run(mix_spec(v, m, music_volume=0.5, length="music")))
    assert abs(float(probe(path)["format"]["duration"]) - 8.0) < 0.05
    x = samples(path)
    under, after = band(x, 200, 0.6, 1.6), band(x, 200, 4.0, 7.5)
    assert after > 0.055 and under < after * 0.6  # ducked under the voice; after it, full
    # level again (ffmpeg sine amplitude 0.125 x music_volume 0.5)
    assert band(x, 440, 0.6, 1.6) > 0.1 and band(x, 440, 4.0, 7.5) < 0.01  # voice, then none


# -- the ducking curve: pure ---------------------------------------------------------------------


def burst(seconds: float, start: float, stop: float, rate: int = ANALYSIS_RATE) -> np.ndarray:
    """Silence with a loud tone between `start` and `stop`."""
    t = np.arange(round(seconds * rate)) / rate
    tone_ = 0.5 * np.sin(2 * np.pi * 440 * t)
    return (tone_ * ((t >= start) & (t < stop))).astype(np.float32)


def test_the_curve_drops_under_the_voice_and_recovers_after_it() -> None:
    gains = duck_curve(burst(4, 1.0, 2.0), duck_db=12.0)
    at = lambda s: float(gains[int(s * CONTROL_RATE)])  # noqa: E731
    assert at(0.5) == pytest.approx(1.0, abs=1e-3)  # nobody speaking yet
    assert at(1.5) == pytest.approx(10 ** (-12 / 20), rel=0.02)  # fully ducked
    assert at(3.9) > 0.9  # recovered
    assert at(0.97) < 0.9  # it eases down before the first syllable, not after


def test_the_curve_is_silent_music_friendly_for_silence_and_empty_input() -> None:
    assert np.allclose(duck_curve(np.zeros(ANALYSIS_RATE, dtype=np.float32), duck_db=12.0), 1.0)
    assert duck_curve(np.zeros(0, dtype=np.float32), duck_db=12.0).shape == (0,)


def test_the_curve_is_a_pure_function_of_the_voice() -> None:
    voice = burst(3, 0.5, 1.5)
    assert np.array_equal(duck_curve(voice, duck_db=9.0), duck_curve(voice.copy(), duck_db=9.0))
    n = len(duck_curve(voice[:12345], duck_db=9.0))
    assert 12345 / ANALYSIS_RATE <= n / CONTROL_RATE < 12345 / ANALYSIS_RATE + 0.1  # covers it


def test_music_made_by_another_step_is_fused_into_the_mix(
    engine: Engine, voice_and_music: tuple[str, str]
) -> None:
    """The second input of a join is a step too: trimming the music happens inside the same
    graph as the mix and the export, so nothing is encoded in between."""
    voice, music = voice_and_music
    spec = {
        "version": 1,
        "inputs": {"voice": voice, "music": music},
        "steps": [
            {"id": "bed", "op": "audio.trim", "in": "music", "args": {"start": 0.5, "end": 3.5}},
            {"id": "m", "op": "audio.mix", "in": ["voice", "bed"], "args": {"duck": True}},
            {"id": "out", "op": "export", "in": "m", "args": {"preset": "wav_master"}},
        ],
        "outputs": ["out"],
    }
    result = engine.run(spec)
    assert result.passed, [r.line() for r in result.checks["out"]]
    assert len(result.steps) == 1 and result.fused_steps == 2
    assert abs(float(probe(out_path(engine, result))["format"]["duration"]) - 4.0) < 0.05


# -- silence and denoise -----------------------------------------------------------------------


def padded_tone(path: Path) -> Path:
    """1 s of silence, 1 s of tone, 1 s of silence, 1 s of tone, 1 s of silence."""
    graph = (
        "anullsrc=r=48000:cl=mono:d=1[s0];sine=frequency=440:duration=1:sample_rate=48000[t0];"
        "anullsrc=r=48000:cl=mono:d=1[s1];sine=frequency=440:duration=1:sample_rate=48000[t1];"
        "anullsrc=r=48000:cl=mono:d=1[s2];[s0][t0][s1][t1][s2]concat=n=5:v=0:a=1"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", graph, "-ac", "1",
         "-c:a", "pcm_s16le", str(path)],
        check=True,
    )  # fmt: skip
    return path


def duration_of(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def silence_spec(handle: str, **args: Any) -> dict[str, Any]:
    return spec(handle, ["audio.silence", args], ["export", {"preset": "wav_master"}])


def test_silence_trims_the_ends(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(padded_tone(tmp_path / "p.wav")).handle
    result = engine.run(silence_spec(handle, where="edges"))
    assert result.passed, [r.line() for r in result.checks[next(iter(result.checks))]]
    # the tone, the pause between the tones and the tone: 3 s, give or take the 0.1 s margin
    assert 2.9 < duration_of(out_path(engine, result)) < 3.4


def test_silence_all_also_shortens_pauses_inside(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(padded_tone(tmp_path / "p.wav")).handle
    edges = duration_of(out_path(engine, engine.run(silence_spec(handle, where="edges"))))
    every = duration_of(
        out_path(engine, engine.run(silence_spec(handle, where="all", min_pause=0.5)))
    )
    assert every < edges - 0.4  # the one-second pause in the middle is cut down to min_pause


def test_a_step_after_silence_sees_the_new_length(engine: Engine, tmp_path: Path) -> None:
    """A fade out is placed from the end: it only lands right if the new length is known."""
    handle = engine.store.put_file(padded_tone(tmp_path / "p.wav")).handle
    result = engine.run(
        spec(
            handle,
            ["audio.silence", {"where": "all"}],
            ["audio.fade", {"fade_out": 0.5}],
            ["export", {"preset": "wav_master"}],
        )
    )
    x = samples(out_path(engine, result))
    n = len(x)
    tail, body = float(np.abs(x[n - 800 :]).max()), float(np.abs(x[n // 4 : n // 2]).max())
    assert tail < body * 0.2  # the fade reaches the actual end of the shortened recording


def test_a_recording_that_is_all_silence_says_so(engine: Engine, tmp_path: Path) -> None:
    quiet = tone(tmp_path / "quiet.wav", "anullsrc=r=48000:cl=mono", 2.0)
    with pytest.raises(StepFailed, match="silent at this threshold"):
        engine.run(silence_spec(engine.store.put_file(quiet).handle))


def test_denoise_lowers_steady_noise_and_keeps_the_tone(engine: Engine, tmp_path: Path) -> None:
    graph = (
        "sine=frequency=1000:duration=4:sample_rate=48000,volume=0.3[t];"
        "anoisesrc=color=white:amplitude=0.05:duration=4:sample_rate=48000[n];"
        "[t][n]amix=inputs=2:normalize=0"
    )
    noisy = tmp_path / "noisy.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", graph, "-ac", "1",
         "-c:a", "pcm_s16le", str(noisy)],
        check=True,
    )  # fmt: skip
    handle = engine.store.put_file(noisy).handle
    clean = out_path(
        engine,
        engine.run(
            spec(
                handle,
                ["audio.denoise", {"strength": 20, "floor_db": -35}],
                ["export", {"preset": "wav_master"}],
            )
        ),
    )
    before, after = samples(noisy), samples(clean)

    def hiss(x: np.ndarray) -> float:  # energy away from the tone
        return float(np.mean([band(x, f, 1.0, 3.0) for f in (2500, 3500, 4500, 5500)]))

    assert hiss(after) < hiss(before) * 0.6
    assert band(after, 1000, 1.0, 3.0) > band(before, 1000, 1.0, 3.0) * 0.5  # the tone survives
