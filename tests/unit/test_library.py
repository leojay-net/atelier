"""The Library (created / uploaded / extras), recent conversations, result titles, and
presentations from scratch (pptx.create)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pptx import Presentation
from test_api import make_org

import api_support
from atelier.engine.engine import Engine
from atelier.spec.errors import SpecInvalid
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


def titled(title: str, summary: str = "Done.") -> Any:
    body = json.dumps({"title": title, "summary": summary, "warnings": [], "question": ""})
    return message([text(body)], "end_turn")


def resize(handle: str, width: int = 120) -> dict[str, Any]:
    return {
        "in": handle,
        "ops": [["image.resize", {"width": width}], ["export", {"preset": "web_jpeg"}]],
    }


def test_the_library_separates_created_uploaded_and_extras() -> None:
    _, api = make_org("acme")
    upload = api.upload("holiday.png", PHOTO).json()["handle"]
    api_support.model = ScriptedModel(
        message([tool_use("run_pipeline", {"spec": resize(upload)})], "tool_use"),
        titled("Holiday Thumbnail"),
    )
    job = api.post("/jobs", {"prompt": "make a small thumbnail", "assets": [upload]}).json()
    assert job["result"]["title"] == "Holiday Thumbnail"

    created = api.get("/library", section="created").json()
    assert created["counts"] == {"created": 1, "uploaded": 1, "extras": 0}
    (item,) = created["items"]
    assert item["title"] == "Holiday Thumbnail" and item["job"] == job["id"]
    assert item["media"] == "image"
    uploaded = api.get("/library", section="uploaded").json()["items"]
    assert [u["title"] for u in uploaded] == ["holiday.png"]
    assert api.get("/library", section="created", media="video").json()["count"] == 0
    assert api.get("/library", section="nope").status_code == 422


def test_a_title_falls_back_to_the_request() -> None:
    _, api = make_org("acme")
    api_support.model = ScriptedModel(final("Which file?", question="Which file?"))
    api.post("/jobs", {"prompt": "make me a birthday card for tolu please with balloons and cake"})
    (conv,) = api.get("/conversations").json()["items"]
    assert conv["title"] == "Make me a birthday card for tolu…"  # its first 7 words


def test_conversations_list_each_chat_once_by_its_latest_message() -> None:
    _, api = make_org("acme")
    _, other = make_org("other")
    upload = api.upload("a.png", PHOTO).json()["handle"]
    api_support.model = ScriptedModel(
        message([tool_use("run_pipeline", {"spec": resize(upload)})], "tool_use"),
        titled("Small Picture"),
        final("Ok", question="Bigger?"),
        titled("Second Chat"),
    )
    first = api.post("/jobs", {"prompt": "make it small", "assets": [upload]}).json()
    follow = api.post("/jobs", {"prompt": "now bigger", "parent": first["id"]}).json()
    second = api.post("/jobs", {"prompt": "another thing"}).json()
    items = api.get("/conversations").json()["items"]
    assert [c["id"] for c in items] == [second["id"], follow["id"]]  # newest first, once each
    assert items[1]["title"] == "Small Picture"  # the chat keeps its name
    assert items[1]["preview"]["media"] == "image"  # the earlier result, as its thumbnail
    assert other.get("/conversations").json()["items"] == []


# -- pptx.create ---------------------------------------------------------------------------------


def deck_spec(handles: list[str], slides: list[dict[str, Any]]) -> dict[str, Any]:
    names = [f"p{i}" for i in range(len(handles))]
    return {
        "version": 1,
        "inputs": dict(zip(names, handles, strict=True)),
        "steps": [
            {"id": "d", "op": "pptx.create", "in": names, "args": {"slides": slides}},
            {"id": "out", "op": "export", "in": "d", "args": {"preset": "pptx_clean"}},
        ],
        "outputs": ["out"],
    }


def test_a_presentation_from_pictures_has_real_text_and_notes(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "store")
    pic = engine.store.put_file(FIXTURES / "photo.png").handle
    slides = [
        {"title": "Python in Four Steps", "body": ["A beginner's guide"], "notes": "Welcome!"},
        {"title": "Install it", "body": ["python.org/downloads", 'print("Hello")']},
    ]
    result = engine.run(deck_spec([pic, pic], slides))
    path = engine.store.path(result.outputs["out"].hash)
    prs = Presentation(path)
    texts = [
        [s.text_frame.text for s in sl.shapes if s.has_text_frame and s.text_frame.text]
        for sl in prs.slides
    ]
    assert texts[0] == ["Python in Four Steps", "A beginner's guide"]
    assert texts[1][0] == "Install it" and 'print("Hello")' in texts[1][1]
    assert prs.slides[0].notes_slide.notes_text_frame.text == "Welcome!"
    assert prs.slide_width * 9 == prs.slide_height * 16  # 16:9
    assert (
        engine.run(deck_spec([pic, pic], slides)).outputs["out"].hash == result.outputs["out"].hash
    )
    one = engine.run(deck_spec([pic], slides))  # one picture for every slide is fine
    assert len(Presentation(engine.store.path(one.outputs["out"].hash)).slides) == 2


def test_a_presentation_needs_one_picture_per_slide_or_one(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "store")
    pic = engine.store.put_file(FIXTURES / "photo.png").handle
    slides = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    with pytest.raises(SpecInvalid, match="one picture per slide"):
        engine.validate(deck_spec([pic, pic], slides))
