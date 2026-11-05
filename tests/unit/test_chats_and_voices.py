"""Renaming and deleting chats, and choosing the voiceover voice (an accent, as a woman's or
a man's voice)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_api import make_org

import api_support
from atelier.agent import voice as voice_mod
from atelier.agent.voice import ACCENTS, VOICES, VultrVoice
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
    api_support.model = None
    api_support.voice = None
    api_support.pictures = None


def titled(title: str) -> Any:
    body = json.dumps({"title": title, "summary": "Done.", "warnings": [], "question": ""})
    return message([text(body)], "end_turn")


def resize(handle: str) -> dict[str, Any]:
    return {
        "in": handle,
        "ops": [["image.resize", {"width": 100}], ["export", {"preset": "web_jpeg"}]],
    }


def test_every_accent_has_a_woman_and_a_man() -> None:
    assert len(ACCENTS) >= 11
    for accent in ACCENTS:
        assert f"{accent}_woman" in VOICES and f"{accent}_man" in VOICES
    assert VOICES["nigerian_man"] == "Nigerian man"


def test_a_designed_voice_is_described_and_seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    class Resp:
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def read(self) -> bytes:
            return b"mp3"

    def fake_open(req: Any, timeout: float) -> Resp:
        sent.append(json.loads(req.data))
        return Resp()

    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", fake_open)
    tts = VultrVoice("https://v/v1", "k")
    tts.speak("Hello there.", "nigerian_man")
    tts.speak("Second sentence.", "nigerian_man")
    tts.speak("Old style.", "serena")
    first, second, old = sent
    assert first["voice"] == "design" and "Nigerian English accent" in first["instructions"]
    assert "man in his thirties" in first["instructions"] and first["language"] == "English"
    assert first["seed"] == second["seed"] and first["instructions"] == second["instructions"]
    assert old["voice"] == "serena" and "instructions" not in old


def test_the_voice_list_and_a_chats_voice() -> None:
    _, api = make_org("acme")
    listed = api.get("/voices").json()
    nigerian = next(a for a in listed["accents"] if a["id"] == "nigerian")
    assert [v["id"] for v in nigerian["voices"]] == ["nigerian_woman", "nigerian_man"]
    assert listed["default"] in VOICES

    api_support.model = ScriptedModel(final("Ok", question="More?"), final("Ok", question="?"))
    first = api.post("/jobs", {"prompt": "a birthday video", "voice": "ghanaian_man"}).json()
    assert first["voice"] == "ghanaian_man"
    follow = api.post("/jobs", {"prompt": "now shorter", "parent": first["id"]}).json()
    assert follow["voice"] == "ghanaian_man"  # the chat keeps its voice
    bad = api.post("/jobs", {"prompt": "x", "voice": "martian_man"})
    assert bad.status_code == 422


def test_chats_can_be_renamed_and_deleted_and_their_results_stay() -> None:
    _, api = make_org("acme")
    _, other = make_org("other")
    upload = api.upload("a.png", PHOTO).json()["handle"]
    api_support.model = ScriptedModel(
        message([tool_use("run_pipeline", {"spec": resize(upload)})], "tool_use"),
        titled("Small Picture"),
        titled("Later Title"),
        final("Ok", question="?"),
    )
    first = api.post("/jobs", {"prompt": "make it small", "assets": [upload]}).json()
    renamed = api.patch(f"/conversations/{first['id']}", {"title": "  Holiday   thumbnail "})
    assert renamed.json()["title"] == "Holiday thumbnail"
    follow = api.post("/jobs", {"prompt": "again", "parent": first["id"]}).json()
    (chat,) = api.get("/conversations").json()["items"]
    assert chat["id"] == follow["id"] and chat["title"] == "Holiday thumbnail"  # the name sticks

    assert other.delete(f"/conversations/{first['id']}").status_code == 404  # not theirs
    keep = api.post("/jobs", {"prompt": "another chat"}).json()
    assert api.delete(f"/conversations/{first['id']}").json() == {"deleted": 2}
    assert [c["id"] for c in api.get("/conversations").json()["items"]] == [keep["id"]]
    created = api.get("/library", section="created").json()["items"]
    assert [c["title"] for c in created] == ["Holiday thumbnail"]  # the result stays
    assert api.patch(f"/conversations/{first['id']}", {"title": ""}).status_code == 422
