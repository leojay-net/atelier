"""make_voiceover (AI voice via the inference provider) and follow-up jobs ("refine this")."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_api import Api, make_org

import api_support
from atelier.agent.budget import Budget
from atelier.agent.config import AgentConfig
from atelier.agent.tools import MAX_VOICEOVERS, Toolbox
from atelier.agent.voice import chunks
from atelier.api.core import services
from conftest import FIXTURES
from harness.fake_model import ScriptedModel, final, message, tool_use

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
]


@pytest.fixture(autouse=True)
def isolated(settings: Any, tmp_path: Path) -> None:
    settings.ATELIER_STORE_ROOT = tmp_path / "orgs"
    settings.ATELIER_QUEUE = "inline"
    settings.ATELIER_MODEL_CLIENT = "api_support.model_client"
    settings.ATELIER_VOICE_CLIENT = "api_support.voice_client"
    api_support.model = None
    api_support.voice = None


@pytest.fixture
def api() -> Api:
    return make_org("acme")[1]


class FakeVoice:
    """Speaks every chunk as one second of tone (real MP3 bytes, so ffmpeg can join them)."""

    def __init__(self, tmp: Path) -> None:
        self.calls: list[tuple[str, str]] = []
        tone = tmp / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=330:d=1",
             "-c:a", "libmp3lame", str(tone)],
            check=True,
        )  # fmt: skip
        self.mp3 = tone.read_bytes()

    def speak(self, text: str, voice: str) -> bytes:
        self.calls.append((text, voice))
        return self.mp3


def last_tool_result(request: dict[str, Any]) -> str:
    """The text of the most recent tool_result the agent sent back to the model."""
    for msg in reversed(request["messages"]):
        if isinstance(msg["content"], list):
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    content = block["content"]
                    return content if isinstance(content, str) else json.dumps(content)
    raise AssertionError("no tool_result in the request")


def user_text(request: dict[str, Any]) -> str:
    first = request["messages"][0]["content"]
    return first if isinstance(first, str) else " ".join(b.get("text", "") for b in first)


def upload(api: Api, path: Path) -> str:
    r = api.upload(path.name, path.read_bytes())
    assert r.status_code == 201, r.json()
    handle: str = r.json()["handle"]
    return handle


# -- make_voiceover ---------------------------------------------------------------------------


def test_chunks_split_long_scripts_at_sentence_ends() -> None:
    script = " ".join(f"Sentence number {i} is here." for i in range(60))
    parts = chunks(script, limit=200)
    assert all(len(p) <= 200 for p in parts) and len(parts) > 5
    assert all(p.endswith(".") for p in parts)  # never cut mid-sentence
    assert " ".join(parts) == script


def test_a_silent_video_gets_an_ai_voiceover(api: Api, tmp_path: Path) -> None:
    voice = api_support.voice = FakeVoice(tmp_path)
    clip = upload(api, FIXTURES / "av" / "clip_silent.mp4")

    def put_voice_on_video(request: dict[str, Any]) -> Any:
        handle = re.search(r"asset:[0-9a-f]{12,64}", last_tool_result(request)).group(0)
        spec = {
            "version": 1,
            "inputs": {"v": clip, "voice": handle},
            "steps": [
                {"id": "s", "op": "video.soundtrack", "in": ["v", "voice"],
                 "args": {"mode": "replace", "loop": False}},
                {"id": "out", "op": "export", "in": "s", "args": {"preset": "web_h264"}},
            ],
            "outputs": ["out"],
        }  # fmt: skip
        return message([tool_use("run_pipeline", {"spec": spec})], "tool_use")

    script = "Welcome to the launch. Here is what we built this week."
    api_support.model = ScriptedModel(
        message([tool_use("make_voiceover", {"text": script, "voice": "ryan"})], "tool_use"),
        put_voice_on_video,
        final("Added a spoken voiceover."),
    )
    job = api.post("/jobs", {"prompt": "Add a voiceover", "assets": [clip]}).json()
    assert job["status"] == "succeeded", job
    assert voice.calls == [(script, "ryan")]
    tool_said = last_tool_result(api_support.model.requests[1])
    assert "voiceover 1." in tool_said and "voice ryan" in tool_said  # duration reported
    out = job["result"]["outputs"]["out"]["handle"]
    assert "audio" in api.get(f"/assets/{out}").json()["description"]  # the video now speaks
    names = [a["filename"] for a in api.get("/assets").json()["items"]]
    assert "voiceover.mp3" in names  # the voice itself is downloadable too


def test_voiceover_tool_errors_are_explained(tmp_path: Path) -> None:
    org, _ = make_org("acme")
    engine = services.engine_for(org)
    box = Toolbox(engine, Budget(AgentConfig().limits))
    no_service = box.dispatch("make_voiceover", {"text": "Hello.", "voice": "serena"})
    assert no_service.is_error and "not available" in str(no_service.content)

    box.voice = FakeVoice(tmp_path)
    assert "empty" in str(box.dispatch("make_voiceover", {"text": " ", "voice": "serena"}).content)
    bad = box.dispatch("make_voiceover", {"text": "Hi.", "voice": "alloy"})
    assert bad.is_error and "serena" in str(bad.content)
    long = box.dispatch("make_voiceover", {"text": "word " * 1000, "voice": "serena"})
    assert long.is_error and "under" in str(long.content)
    for _ in range(MAX_VOICEOVERS):
        assert not box.dispatch("make_voiceover", {"text": "Hi.", "voice": "eric"}).is_error
    capped = box.dispatch("make_voiceover", {"text": "Hi.", "voice": "eric"})
    assert capped.is_error and "per request" in str(capped.content)


# -- follow-ups ---------------------------------------------------------------------------------


def test_a_follow_up_sees_the_earlier_request_result_and_files(api: Api) -> None:
    photo = upload(api, FIXTURES / "photo.png")
    spec = {
        "in": photo,
        "ops": [["image.resize", {"width": 320}], ["export", {"preset": "web_jpeg"}]],
    }
    api_support.model = ScriptedModel(
        message([tool_use("run_pipeline", {"spec": spec})], "tool_use"),
        final("Made a small JPEG."),
    )
    first = api.post("/jobs", {"prompt": "Make this small", "assets": [photo]}).json()
    earlier_out = first["result"]["outputs"]
    out_handle = next(iter(earlier_out.values()))["handle"]

    api_support.model = ScriptedModel(final("Noted.", question="Which color?"))
    second = api.post("/jobs", {"prompt": "Now make it warmer", "parent": first["id"]}).json()
    assert second["status"] == "needs_input" and second["parent"] == first["id"]
    seen = user_text(api_support.model.requests[0])
    assert seen.startswith("Follow-up to an earlier request")
    assert "Earlier request: Make this small" in seen and "Made a small JPEG." in seen
    assert out_handle[:18] in seen and "Now: Now make it warmer" in seen
    assert second["prompt"] == "Now make it warmer"  # history stays out of what the user sees

    # answering the question is another follow-up, and the question is part of its context
    api_support.model = ScriptedModel(final("Warmer now."))
    third = api.post("/jobs", {"prompt": "Orange", "parent": second["id"]}).json()
    assert "You asked then: Which color?" in user_text(api_support.model.requests[0])
    assert third["status"] in ("succeeded", "failed", "needs_input")


def test_follow_up_rules(api: Api) -> None:
    _, other = make_org("other")
    api_support.model = ScriptedModel(final("Ok.", question="What?"))
    theirs = other.post("/jobs", {"prompt": "hello"}).json()
    r = api.post("/jobs", {"prompt": "refine", "parent": theirs["id"]})
    assert r.status_code == 404  # another organization's job does not exist for you
    r = api.post("/jobs", {"spec": {"in": "asset:00000000", "ops": []}, "parent": theirs["id"]})
    assert r.status_code in (404, 422)
