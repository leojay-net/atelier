"""Animated series: a cast of characters and places (design pictures), scenes animated from
them (MiniMax H3 on Vultr) with each character's own voice, and the Characters API."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_api import make_org

import api_support
from atelier.agent import animation as anim_mod
from atelier.agent.animation import VultrAnimation
from atelier.agent.budget import Budget
from atelier.agent.cast import CastMember
from atelier.agent.config import AgentConfig
from atelier.agent.tools import MAX_ANIMATION_SECONDS, Toolbox
from atelier.api.core import services
from atelier.api.core.models import Character
from conftest import FIXTURES
from harness.fake_model import ScriptedModel, final, message, text, tool_use

pytestmark = pytest.mark.django_db(transaction=True)
PHOTO = (FIXTURES / "photo.png").read_bytes()


@pytest.fixture(autouse=True)
def isolated(settings: Any, tmp_path: Path) -> None:
    settings.ATELIER_STORE_ROOT = tmp_path / "orgs"
    settings.ATELIER_QUEUE = "inline"
    settings.ATELIER_MODEL_CLIENT = "api_support.model_client"
    settings.ATELIER_VOICE_CLIENT = "api_support.voice_client"
    settings.ATELIER_PICTURES_CLIENT = "api_support.pictures_client"
    settings.ATELIER_ANIMATION_CLIENT = "api_support.animation_client"
    api_support.model = api_support.voice = api_support.pictures = api_support.animation = None


def media(tmp: Path, name: str, args: list[str]) -> bytes:
    out = tmp / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(out)], check=True)
    return out.read_bytes()


class FakePictures:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str, str | None]] = []

    def generate(self, prompt: str, shape: str, style: str | None = None) -> bytes:
        self.asked.append((prompt, shape, style))
        return PHOTO


class FakeVoice:
    def __init__(self, tmp: Path, seconds: float = 1.5) -> None:
        self.tmp, self.seconds, self.lines = tmp, seconds, []

    def speak_as(self, text: str, description: str) -> bytes:
        self.lines.append((text, description))
        return media(self.tmp, "line.mp3", ["-f", "lavfi", "-i", f"sine=f=300:d={self.seconds}"])


class FakeAnimator:
    def __init__(self, tmp: Path) -> None:
        self.tmp, self.calls = tmp, []

    def animate(
        self,
        prompt: str,
        pictures: list[bytes],
        seconds: int,
        shape: str,
        line: bytes | None = None,
        seed: int = 7,
    ) -> bytes:
        self.calls.append((prompt, len(pictures), seconds, shape, line is not None))
        return media(
            self.tmp,
            "clip.mp4",
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc=d={seconds}:s=320x180:r=24",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
            ],
        )


class MemoryCast:
    def __init__(self) -> None:
        self.saved: dict[str, CastMember] = {}

    def members(self) -> list[CastMember]:
        return list(self.saved.values())

    def save(self, member: CastMember, filename: str) -> None:
        self.saved[member.name] = member


def test_the_service_sends_the_cast_and_the_line_and_turns_its_own_sound_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Any] = []

    class Resp:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def read(self, *a: Any) -> bytes:
            return self.body

    answers = iter(
        [
            {"id": "v1", "status": "pending", "polling_url": "https://v/v1/videos/v1"},
            {"status": "completed", "unsigned_urls": ["https://v/v1/videos/v1/content?index=0"]},
        ]
    )

    def fake_open(req: Any, timeout: float) -> Resp:
        sent.append((req.full_url, json.loads(req.data) if req.data else None))
        if req.full_url.endswith("content?index=0"):
            return Resp(b"MP4")
        return Resp(json.dumps(next(answers)).encode())

    monkeypatch.setattr(anim_mod.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(anim_mod.time, "sleep", lambda s: None)
    out = VultrAnimation("https://v/v1", "k").animate(
        "Kofi waves.", [PHOTO, PHOTO], 20, "portrait", line=b"ID3"
    )
    assert out == b"MP4"
    url, body = sent[0]
    assert url == "https://v/v1/videos" and body["model"] == "minimax-h3"
    assert body["duration"] == 15 and body["aspect_ratio"] == "9:16"  # clamped to one scene
    assert body["generate_audio"] is False and body["prompt"].startswith("Kofi waves. ")
    images = [r for r in body["input_references"] if r["type"] == "image_url"]
    assert len(images) == 2 and all(r["role"] == "reference" for r in images)
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    (audio,) = [r for r in body["input_references"] if r["type"] == "audio_url"]
    assert audio["audio_url"]["url"].startswith("data:audio/mpeg;base64,")


def test_designing_a_cast_and_animating_a_scene_with_a_line(tmp_path: Path) -> None:
    org, _ = make_org("acme")
    pics, cast, voice, animator = (
        FakePictures(),
        MemoryCast(),
        FakeVoice(tmp_path),
        FakeAnimator(tmp_path),
    )
    box = Toolbox(
        services.engine_for(org),
        Budget(AgentConfig().limits),
        voice=voice,
        pictures=pics,
        animator=animator,
        cast=cast,
    )
    kofi = box.dispatch(
        "make_character",
        {
            "name": "Kofi",
            "kind": "character",
            "look": "a 10-year-old boy in an orange hoodie",
            "voice": "a cheerful boy",
        },
    )
    market = box.dispatch(
        "make_character",
        {
            "name": "Market",
            "kind": "place",
            "look": "a colorful futuristic market",
            "voice": "ignored",
        },
    )
    assert not kofi.is_error and not market.is_error, (kofi.content, market.content)
    assert "Character design:" in pics.asked[0][0] and pics.asked[0][1] == "square"
    assert "no people" in pics.asked[1][0] and pics.asked[1][1] == "landscape"
    assert pics.asked[0][2] and "animated" in pics.asked[0][2]  # the animation look, not photos
    assert cast.saved["Market"].voice == ""

    out = box.dispatch(
        "animate",
        {
            "scene": "Kofi walks in and waves.",
            "cast": ["kofi", "Market"],
            "seconds": 5,
            "shape": "landscape",
            "speaker": "Kofi",
            "line": "Hi, I'm Kofi!",
        },
    )
    assert not out.is_error, out.content
    assert voice.lines == [("Hi, I'm Kofi!", "a cheerful boy")]  # in his own voice
    prompt, n_refs, seconds, shape, with_line = animator.calls[0]
    assert shape == "landscape"
    assert n_refs == 2 and seconds == 5 and with_line and "Kofi says" in prompt
    handle = str(out.content).split(" ")[0]
    meta = box.engine.probe(box.engine.store.resolve(handle))
    assert meta.get("audio") and abs(float(meta["duration"]) - 5) < 0.3  # his line is the sound
    assert ("scene-1.mp4") in [n for _, n in box.generated]

    silent = box.dispatch(
        "animate",
        {
            "scene": "The market at dawn.",
            "cast": ["Market"],
            "seconds": 4,
            "shape": "landscape",
            "speaker": "",
            "line": "",
        },
    )
    assert not silent.is_error and "no line" in str(silent.content)


def test_animate_explains_what_is_wrong(tmp_path: Path) -> None:
    org, _ = make_org("acme")
    cast = MemoryCast()
    cast.saved["Kofi"] = CastMember(
        "Kofi", "character", "a boy", "a boy's voice", "asset:" + "0" * 64
    )
    box = Toolbox(
        services.engine_for(org),
        Budget(AgentConfig().limits),
        voice=FakeVoice(tmp_path, 6.0),
        animator=FakeAnimator(tmp_path),
        cast=cast,
    )

    def go(**kw: Any) -> str:
        args = {
            "scene": "x",
            "cast": ["Kofi"],
            "seconds": 5,
            "shape": "landscape",
            "speaker": "",
            "line": "",
        }
        return str(box.dispatch("animate", args | kw).content)

    assert "not in the cast: Bolt" in go(cast=["Kofi", "Bolt"]) and "make_character" in go(
        cast=["Bolt"]
    )
    assert "4 to 15" in go(seconds=30)
    assert "both speaker and line" in go(speaker="Kofi")
    box.animated = MAX_ANIMATION_SECONDS - 4
    assert "left in this request" in go(seconds=5)
    none = Toolbox(services.engine_for(org), Budget(AgentConfig().limits))
    assert "not available" in str(
        none.dispatch(
            "animate",
            {
                "scene": "x",
                "cast": [],
                "seconds": 5,
                "shape": "landscape",
                "speaker": "",
                "line": "",
            },
        ).content
    )


def test_a_line_longer_than_its_scene_is_refused(tmp_path: Path) -> None:
    org, _ = make_org("acme")
    pics, cast = FakePictures(), MemoryCast()
    box = Toolbox(
        services.engine_for(org),
        Budget(AgentConfig().limits),
        voice=FakeVoice(tmp_path, 6.0),
        pictures=pics,
        animator=FakeAnimator(tmp_path),
        cast=cast,
    )
    box.dispatch(
        "make_character", {"name": "Kofi", "kind": "character", "look": "a boy", "voice": "a boy"}
    )
    out = box.dispatch(
        "animate",
        {
            "scene": "x",
            "cast": ["Kofi"],
            "seconds": 5,
            "shape": "landscape",
            "speaker": "Kofi",
            "line": "a long speech",
        },
    )
    assert out.is_error and "make the scene at least 7s" in str(out.content)


# -- the Characters API and the cast in requests -------------------------------------------------


def test_users_create_edit_hear_and_delete_characters(tmp_path: Path) -> None:
    _, api = make_org("acme")
    _, other = make_org("other")
    assert api.post("/characters", {"name": "Kofi", "look": "a boy"}).status_code == 503
    api_support.pictures = FakePictures()
    made = api.post(
        "/characters",
        {"name": "Kofi", "look": "a 10-year-old boy, orange hoodie", "voice": "a cheerful boy"},
    )
    assert made.status_code == 201, made.content
    kofi = made.json()
    assert kofi["kind"] == "character" and kofi["image"].startswith("asset:")
    place = api.post(
        "/characters",
        {"name": "Market", "kind": "place", "look": "a market", "voice": "should be dropped"},
    ).json()
    assert place["voice"] == ""
    assert api.post("/characters", {"name": "kofi", "look": "another"}).status_code == 409
    assert [c["name"] for c in api.get("/characters").json()["items"]] == ["Kofi", "Market"]
    assert other.get("/characters").json()["items"] == []
    assert other.delete(f"/characters/{kofi['id']}").status_code == 404

    renamed = api.patch(
        f"/characters/{kofi['id']}", {"name": "Kofi A.", "voice": "a calm boy"}
    ).json()
    assert renamed["name"] == "Kofi A." and renamed["image"] == kofi["image"]  # no redraw
    api_support.pictures.generate = lambda p, s, style=None: (
        (FIXTURES / "photo_landscape.png").read_bytes()
        if (FIXTURES / "photo_landscape.png").exists()
        else PHOTO + b"\0"
    )  # a different picture
    redrawn = api.patch(f"/characters/{kofi['id']}", {"look": "a boy in a green hoodie"}).json()
    assert redrawn["image"] != kofi["image"] and redrawn["look"] == "a boy in a green hoodie"

    api_support.voice = FakeVoice(tmp_path)
    hear = api.get(f"/characters/{kofi['id']}/voice")
    assert hear.status_code == 200 and hear["content-type"] == "audio/mpeg"
    assert api_support.voice.lines[0][1] == "a calm boy"
    assert api.get(f"/characters/{place['id']}/voice").status_code == 422
    extras = api.get("/library", section="extras").json()["items"]
    assert any(i["title"].startswith("Character design:") for i in extras)
    assert api.delete(f"/characters/{kofi['id']}").json() == {"deleted": kofi["id"]}
    assert [c["name"] for c in api.get("/characters").json()["items"]] == ["Market"]


def test_the_cast_rides_along_with_every_request_and_survives_deleting_the_chat() -> None:
    org, api = make_org("acme")
    api_support.pictures = FakePictures()
    api_support.model = scripted = ScriptedModel(
        message(
            [
                tool_use(
                    "make_character",
                    {
                        "name": "Bolt",
                        "kind": "character",
                        "look": "a small teal robot",
                        "voice": "a tiny robot voice",
                    },
                )
            ],
            "tool_use",
        ),
        message(
            [
                text(
                    json.dumps(
                        {"title": "Cast", "summary": "Made Bolt.", "warnings": [], "question": ""}
                    )
                )
            ],
            "end_turn",
        ),
        final("Ok", question="?"),
    )
    first = api.post("/jobs", {"prompt": "create a robot character called Bolt"}).json()
    assert [c.name for c in Character.objects.filter(org=org)] == ["Bolt"]
    api.post("/jobs", {"prompt": "animate bolt"})
    last = json.dumps(scripted.requests[-1]["messages"])
    assert "Your cast" in last and "Bolt (character)" in last
    api.delete(f"/conversations/{first['id']}?files=true")
    bolt = Character.objects.get(org=org)
    assert api.get(f"/assets/{bolt.image}").status_code == 200  # the design is kept
