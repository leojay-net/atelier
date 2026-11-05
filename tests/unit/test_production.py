"""Production quality (docs/14): moving stills, beat-length shots, original music, and
word-timed captions for every voiceover."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_api import make_org

from atelier.agent.budget import Budget
from atelier.agent.config import AgentConfig
from atelier.agent.tools import MAX_MUSIC, Toolbox
from atelier.agent.voice import caption_cues, sentences, srt, word_times
from atelier.api.core import services
from atelier.engine.engine import Engine
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
]


@pytest.fixture(autouse=True)
def isolated(settings: Any, tmp_path: Path) -> None:
    settings.ATELIER_STORE_ROOT = tmp_path / "orgs"


def tone(tmp: Path, seconds: float, name: str = "t.mp3") -> bytes:
    out = tmp / name
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=330:d={seconds}",
         "-c:a", "libmp3lame", str(out)],
        check=True,
    )  # fmt: skip
    return out.read_bytes()


# -- captions -----------------------------------------------------------------------------------


def test_sentences_join_short_ones_and_keep_long_ones_whole() -> None:
    got = sentences("Hi. Welcome to Python in thirty seconds! First, install it.")
    assert got == ["Hi. Welcome to Python in thirty seconds!", "First, install it."]


def test_word_cues_are_short_ordered_and_cover_each_sentence() -> None:
    spoken = ["Welcome to Python in thirty seconds!", "First, install it from python.org."]
    cues = caption_cues(word_times(spoken, [2.5, 2.0]))
    assert all(len(text.split()) <= 3 for _, _, text in cues)
    starts = [a for a, _, _ in cues]
    assert starts == sorted(starts) and starts[0] == 0.0
    second = next(a for a, _, text in cues if text.startswith("First"))
    assert second == pytest.approx(2.5)  # the second sentence starts exactly when spoken
    assert all(b > a for a, b, _ in cues)
    text = srt(cues)
    assert text.startswith("1\n00:00:00,000 --> ") and "First\n" in text  # trailing comma dropped


class FakeVoice:
    def __init__(self, tmp: Path) -> None:
        self.tmp, self.calls = tmp, []

    def speak(self, text: str, voice: str) -> bytes:
        self.calls.append(text)
        return tone(self.tmp, 0.5 + len(text.split()) * 0.3, f"v{len(self.calls)}.mp3")


def test_a_voiceover_comes_with_captions_that_burn_into_a_video(tmp_path: Path) -> None:
    org, _ = make_org("acme")
    engine = services.engine_for(org)
    box = Toolbox(engine, Budget(AgentConfig().limits), voice=FakeVoice(tmp_path))
    out = box.dispatch(
        "make_voiceover",
        {
            "text": "Welcome to the launch, everyone. Here is what we built this week.",
            "voice": "serena",
        },
    )
    assert not out.is_error, out.content
    _voice, caps_h = re.findall(r"asset:[0-9a-f]{64}", str(out.content))
    assert {n for _, n in box.generated} == {"voiceover.mp3", "captions.srt"}
    captions = engine.store.path(engine.store.resolve(caps_h).hash).read_text()
    assert "Welcome to the" in captions and "-->" in captions
    clip = engine.store.put_file(FIXTURES / "av" / "clip_silent.mp4").handle
    result = engine.run(
        {
            "version": 1,
            "inputs": {"v": clip, "c": caps_h},
            "steps": [
                {
                    "id": "t",
                    "op": "video.captions",
                    "in": ["v", "c"],
                    "args": {"style": "bold_pop"},
                },
                {"id": "out", "op": "export", "in": "t", "args": {"preset": "web_h264"}},
            ],
            "outputs": ["out"],
        }
    )
    assert result.outputs["out"].media.value == "video"


# -- music -----------------------------------------------------------------------------------------


class FakeMusic:
    def __init__(self, tmp: Path) -> None:
        self.tmp, self.calls = tmp, []

    def compose(self, style: str, seconds: int, bpm: int, seed: int) -> bytes:
        self.calls.append((style, seconds, bpm, seed))
        return tone(self.tmp, seconds, f"m{len(self.calls)}.mp3")


def test_make_music_stores_a_track_and_says_how_long_a_beat_is(tmp_path: Path) -> None:
    org, _ = make_org("acme")
    fake = FakeMusic(tmp_path)
    box = Toolbox(services.engine_for(org), Budget(AgentConfig().limits), music=fake)
    out = box.dispatch("make_music", {"style": "warm lofi pop", "seconds": 12, "bpm": 100})
    assert not out.is_error, out.content
    assert "100 bpm" in str(out.content) and "one beat 0.600s" in str(out.content)
    assert fake.calls[0][:3] == ("warm lofi pop", 12, 100)
    assert box.generated[0][1] == "music.mp3"
    assert box.dispatch("make_music", {"style": "x", "seconds": 5, "bpm": 100}).is_error
    assert box.dispatch("make_music", {"style": "x", "seconds": 20, "bpm": 300}).is_error
    for _ in range(MAX_MUSIC - 1):
        box.dispatch("make_music", {"style": "calm piano", "seconds": 10, "bpm": 80})
    assert "per request" in str(
        box.dispatch("make_music", {"style": "more", "seconds": 10, "bpm": 80}).content
    )
    none = Toolbox(services.engine_for(org), Budget(AgentConfig().limits))
    assert "not available" in str(
        none.dispatch("make_music", {"style": "x", "seconds": 10, "bpm": 90}).content
    )


# -- moving stills --------------------------------------------------------------------------------


def frames(path: Path, at: list[float]) -> list[np.ndarray]:
    out = []
    for t in at:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
             "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        out.append(np.frombuffer(raw, np.uint8).reshape(90, 160).astype(float))
    return out


def slideshow(engine: Engine, **args: Any) -> Path:
    pic = engine.store.put_file(FIXTURES / "photo.png").handle
    result = engine.run(
        {
            "version": 1,
            "inputs": {"a": pic, "b": pic},
            "steps": [
                {
                    "id": "s",
                    "op": "video.slideshow",
                    "in": ["a", "b"],
                    "args": {"shape": "landscape", "short_side": 360, **args},
                },
                {"id": "out", "op": "export", "in": "s", "args": {"preset": "web_h264"}},
            ],
            "outputs": ["out"],
        }
    )
    return engine.store.path(result.outputs["out"].hash)


def test_every_still_moves_by_default_and_none_keeps_it_still(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "store")
    moving = frames(slideshow(engine, durations=[2.0, 1.2]), [0.1, 1.8])
    still = frames(slideshow(engine, durations=[2.0, 1.2], motion="none"), [0.1, 1.8])
    assert np.abs(moving[0] - moving[1]).mean() > 1.5  # the camera moved within the first shot
    assert np.abs(still[0] - still[1]).mean() < 0.5


def test_durations_set_each_shot(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "store")
    path = slideshow(engine, durations=[2.4, 1.2])  # e.g. 4 and 2 beats at 100 bpm
    length = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert float(length) == pytest.approx(3.6, abs=0.1)
    with pytest.raises(SpecInvalid, match="durations has 3 values for 2 pictures"):
        engine.validate(
            {
                "version": 1,
                "inputs": {"a": "asset:" + "0" * 64, "b": "asset:" + "0" * 64},
                "steps": [
                    {
                        "id": "s",
                        "op": "video.slideshow",
                        "in": ["a", "b"],
                        "args": {"durations": [1, 1, 1]},
                    }
                ],
                "outputs": ["s"],
            }
        )


def test_sound_effects_land_on_their_times_and_nothing_else_plays(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "store")
    pic = engine.store.put_file(FIXTURES / "photo.png").handle
    result = engine.run(
        {
            "version": 1,
            "inputs": {"a": pic, "b": pic},
            "steps": [
                {
                    "id": "s",
                    "op": "video.slideshow",
                    "in": ["a", "b"],
                    "args": {"durations": [1.5, 1.5], "short_side": 240, "motion": "none"},
                },
                {
                    "id": "x",
                    "op": "video.sfx",
                    "in": "s",
                    "args": {"hits": [{"at": 0.0, "kind": "pop"}, {"at": 1.5, "kind": "whoosh"}]},
                },
                {"id": "out", "op": "export", "in": "x", "args": {"preset": "web_h264"}},
            ],
            "outputs": ["out"],
        }
    )
    path = engine.store.path(result.outputs["out"].hash)
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    x = np.abs(np.frombuffer(pcm, np.float32))

    def peak(a: float, b: float) -> float:
        return float(x[int(a * 8000) : int(b * 8000)].max(initial=0.0))

    assert peak(0.0, 0.15) > 0.02  # the pop
    assert peak(1.2, 1.6) > 0.02  # the whoosh swelling into the cut
    assert peak(2.2, 2.9) < 0.001  # silence after it
