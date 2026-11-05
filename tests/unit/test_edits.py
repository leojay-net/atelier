"""Hand edits (the editor, no prompt): trim, cut, speed, shape, volume, text, a voiceover,
music and sound effects, rendered by the engine as the chat's next version."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from test_api import make_org

import api_support
from atelier.api.core.edits import Clock, EditIn, build_spec, describe, kept_parts
from atelier.engine.engine import Engine
from conftest import FIXTURES
from harness.fake_model import ScriptedModel, message, text, tool_use

pytestmark = pytest.mark.django_db(transaction=True)
CLIP = FIXTURES / "av" / "clip_landscape.mp4"  # 640x360, 4 s, with sound
TONE = FIXTURES / "av" / "tone.wav"


@pytest.fixture(autouse=True)
def isolated(settings: Any, tmp_path: Path) -> None:
    settings.ATELIER_STORE_ROOT = tmp_path / "orgs"
    settings.ATELIER_QUEUE = "inline"
    settings.ATELIER_MODEL_CLIENT = "api_support.model_client"
    settings.ATELIER_VOICE_CLIENT = "api_support.voice_client"
    settings.ATELIER_MUSIC_CLIENT = "api_support.voice_client"  # unused unless composing
    api_support.model = api_support.voice = None


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def loudness(path: Path, a: float, b: float) -> float:
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    x = np.abs(np.frombuffer(pcm, np.float32))
    return float(x[int(a * 8000) : int(b * 8000)].mean())


def test_the_clock_moves_times_past_the_cuts_and_the_speed() -> None:
    clock = Clock([(0.0, 1.0), (2.0, 4.0)], speed=2.0)
    assert clock.length == pytest.approx(1.5)
    assert clock(0.5) == pytest.approx(0.25)
    assert clock(1.5) == pytest.approx(0.5)  # inside the cut: the next kept part's start
    assert clock(3.0) == pytest.approx(1.0)
    assert clock(4.5) is None
    edit = EditIn(
        source="asset:x",
        parts=[
            {"start": 2, "end": 9},
            {"start": 0, "end": 1.1},
            {"start": 1.0, "end": 1.5},
            {"start": 3, "end": 3.1},
        ],
    )
    assert kept_parts(edit, 4.0) == [(0.0, 1.5), (2.0, 4.0)]  # merged, clipped, scraps dropped
    with pytest.raises(ValueError, match="nothing is left"):
        kept_parts(EditIn(source="asset:x", parts=[{"start": 0, "end": 0.1}]), 4.0)


def test_an_edit_with_everything_renders(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "store")
    clip = engine.store.put_file(CLIP).handle
    tone = engine.store.put_file(TONE).handle
    edit = EditIn(
        source=clip,
        parts=[{"start": 0, "end": 1.5}, {"start": 2.5, "end": 4}],  # cut 1.5-2.5
        speed=1.0,
        shape="portrait",
        volume=0.0,  # mute the clip's own sound
        texts=[{"text": "HELLO", "start": 0, "end": 1}],
        voiceover={"handle": tone, "start": 2.6, "volume": 1.0},  # source time: 1.6 s edited
        music=None,
        sounds=[{"at": 2.0, "kind": "pop"}],  # inside the cut: lands at the join (1.5 s)
    )
    spec = build_spec(edit, 4.0, voice_handle=tone)
    ops = [s["op"] for s in spec["steps"]]
    assert ops == [
        "video.trim",
        "video.trim",
        "video.concat",
        "video.reframe",
        "video.text",
        "video.layers",
        "video.sfx",
        "export",
    ]
    layers = next(s for s in spec["steps"] if s["op"] == "video.layers")["args"]
    assert layers == {"original": 0.0, "layers": [{"kind": "voice", "start": 1.6, "volume": 1.0}]}
    assert next(s for s in spec["steps"] if s["op"] == "video.sfx")["args"]["hits"] == [
        {"at": 1.5, "kind": "pop"}
    ]
    out = engine.store.path(engine.run(spec).outputs["out"].hash)
    info = probe(out)
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.1)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert video["height"] > video["width"]  # portrait
    assert loudness(out, 0.2, 1.3) < 0.002  # the clip's own sound is muted
    assert loudness(out, 1.7, 2.8) > 0.01  # the voiceover plays from 1.6 s
    assert "cut into 2 parts" in describe(edit, kept_parts(edit, 4.0), 4.0)


def test_music_loops_under_the_video_at_its_volume(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "store")
    clip = engine.store.put_file(FIXTURES / "av" / "clip_silent.mp4").handle  # 3 s, no sound
    short = tmp_path / "short.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", str(short)],
        check=True,
    )
    music = engine.store.put_file(short).handle
    edit = EditIn(source=clip, music={"handle": music, "volume": 0.5, "loop": True})
    out = engine.store.path(
        engine.run(build_spec(edit, 3.0, music_handle=music)).outputs["out"].hash
    )
    assert float(probe(out)["format"]["duration"]) == pytest.approx(3.0, abs=0.1)
    assert loudness(out, 2.2, 2.8) > 0.02  # still playing after the 1 s song: it looped


class FakeVoice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def speak(self, text: str, voice: str) -> bytes:
        self.calls.append((text, voice))
        return TONE.read_bytes()


def test_the_editor_makes_the_next_version_in_the_chat() -> None:
    _, api = make_org("acme")
    clip = api.upload("clip.mp4", CLIP.read_bytes()).json()["handle"]
    spec = {
        "in": clip,
        "ops": [["video.trim", {"start": 0, "end": 4}], ["export", {"preset": "web_h264"}]],
    }
    body = json.dumps({"title": "My Clip", "summary": "Done.", "warnings": [], "question": ""})
    api_support.model = ScriptedModel(
        message([tool_use("run_pipeline", {"spec": spec})], "tool_use"),
        message([text(body)], "end_turn"),
    )
    first = api.post("/jobs", {"prompt": "tidy my clip", "assets": [clip]}).json()
    (video,) = [o["handle"] for o in first["result"]["outputs"].values()]
    api_support.voice = voice = FakeVoice()
    res = api.post(
        f"/jobs/{first['id']}/edit",
        {
            "source": video,
            "parts": [{"start": 0.5, "end": 3.5}],
            "volume": 1.5,
            "texts": [{"text": "Hi!", "start": 0.5}],
            "voiceover": {"text": "Hello there.", "voice": "british_man"},
            "sounds": [{"at": 1, "kind": "whoosh"}],
        },
    )
    assert res.status_code == 202, res.content
    edited = res.json()
    assert edited["status"] == "succeeded" and edited["parent"] == first["id"]
    assert edited["prompt"].startswith("Edited by hand: trimmed, sound 150%, 1 text, a voiceover")
    assert voice.calls == [("Hello there.", "british_man")]
    assert "edited version" in edited["result"]["summary"]
    (out,) = edited["result"]["outputs"].values()
    assert out["media"] == "video"
    (chat,) = api.get("/conversations").json()["items"]
    assert chat["id"] == edited["id"] and chat["title"] == "My Clip"  # the chat goes on
    thread = api.get(f"/jobs/{edited['id']}/thread").json()["items"]
    assert [j["id"] for j in thread] == [first["id"], edited["id"]]
    bad = api.post(
        f"/jobs/{first['id']}/edit", {"source": video, "parts": [{"start": 0, "end": 0.05}]}
    )
    assert bad.status_code == 422 and "nothing is left" in bad.content.decode()
    upload = api.post(
        f"/jobs/{first['id']}/edit",
        {"source": api.upload("a.wav", TONE.read_bytes()).json()["handle"]},
    )
    assert upload.status_code == 422


class FakeComposer:
    def compose(self, style: str, seconds: int, bpm: int, seed: int) -> bytes:
        return subprocess.run(  # real MP3, as the music service returns
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=f=220:d={seconds}",
                "-c:a",
                "libmp3lame",
                "-f",
                "mp3",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout


def test_voiceover_and_music_are_made_ahead_to_hear_in_the_preview(settings: Any) -> None:
    _, api = make_org("acme")
    clip = api.upload("clip.mp4", CLIP.read_bytes()).json()["handle"]
    api_support.model = ScriptedModel(
        message(
            [text(json.dumps({"title": "T", "summary": "Ok", "warnings": [], "question": ""}))],
            "end_turn",
        )
    )
    first = api.post("/jobs", {"prompt": "hello", "assets": [clip]}).json()
    api_support.voice = voice = FakeVoice()
    made = api.post(
        f"/jobs/{first['id']}/edit/voiceover", {"text": "Hi there.", "voice": "kenyan_woman"}
    )
    assert made.status_code == 200, made.content
    vo = made.json()
    assert vo["handle"].startswith("asset:") and vo["seconds"] > 0
    assert voice.calls == [("Hi there.", "kenyan_woman")]
    assert api.get(f"/assets/{vo['handle']}/content").status_code == 200  # playable now
    assert api.post(f"/jobs/{first['id']}/edit/voiceover", {"text": " "}).status_code == 422
    settings.ATELIER_MUSIC_CLIENT = "test_edits.composer"
    music = api.post(
        f"/jobs/{first['id']}/edit/music", {"style": "calm piano", "seconds": 12}
    ).json()
    assert music["handle"].startswith("asset:")
    saved = api.post(
        f"/jobs/{first['id']}/edit",
        {
            "source": clip,
            "voiceover": {"handle": vo["handle"]},
            "music": {"handle": music["handle"]},
        },
    ).json()
    assert saved["status"] == "succeeded", saved["error"]
    assert voice.calls == [("Hi there.", "kenyan_woman")]  # reused


def composer() -> FakeComposer:
    return FakeComposer()
