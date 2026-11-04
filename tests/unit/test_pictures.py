"""find_images (openly licensed photos) and make_image (Vultr image generation) agent tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from test_api import make_org

import api_support
from atelier.agent import pictures
from atelier.agent.budget import Budget
from atelier.agent.config import AgentConfig
from atelier.agent.pictures import Found, PictureError
from atelier.agent.tools import MAX_MADE, Toolbox
from atelier.api.core import services
from conftest import FIXTURES
from harness.fake_model import ScriptedModel, final, message, tool_use

pytestmark = pytest.mark.django_db(transaction=True)
PHOTO = (FIXTURES / "photo.png").read_bytes()


def made_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (160, 90), "teal").save(buf, "PNG")
    return buf.getvalue()


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


class FakePictures:
    def __init__(self, broken: int = 0) -> None:
        self.searches: list[tuple[str, int, str]] = []
        self.prompts: list[tuple[str, str]] = []
        self.broken = broken  # how many downloads fail first

    def search(self, query: str, count: int, shape: str) -> list[Found]:
        self.searches.append((query, count, shape))
        return [
            Found(f"https://example.org/{i}.png", 1600, 900, f"Photo {i}",
                  f'"Photo {i}" by Someone, CC BY 2.0 (via Openverse)', "")
            for i in range(count)
        ]  # fmt: skip

    def download(self, found: Found) -> bytes:
        if self.broken:
            self.broken -= 1
            raise PictureError("the website could not be reached")
        return PHOTO

    def generate(self, prompt: str, shape: str) -> bytes:
        self.prompts.append((prompt, shape))
        return made_png()


def toolbox(fake: Any = None) -> Toolbox:
    org, _ = make_org("acme")
    return Toolbox(services.engine_for(org), Budget(AgentConfig().limits), pictures=fake)


def test_find_images_stores_photos_with_credits() -> None:
    fake = FakePictures(broken=1)
    box = toolbox(fake)
    out = box.dispatch("find_images", {"query": "python laptop", "count": 3, "shape": "landscape"})
    assert not out.is_error, out.content
    assert fake.searches == [("python laptop", 3, "landscape")]
    text = str(out.content)
    assert text.count("asset:") == 2 and "skipped one" in text  # one download failed, said so
    assert len(box.credits) == 2 and "CC BY 2.0" in box.credits[0]
    assert all(name.startswith("found-photo-") for _, name in box.generated)


def test_make_image_and_its_limits() -> None:
    fake = FakePictures()
    box = toolbox(fake)
    out = box.dispatch("make_image", {"prompt": "a woman coding in a cafe", "shape": "portrait"})
    assert not out.is_error and "generated" in str(out.content)
    assert fake.prompts == [("a woman coding in a cafe", "portrait")]
    assert box.generated[0][1].startswith("made-a-woman-coding-in-a-cafe")
    for _ in range(MAX_MADE - 1):
        box.dispatch("make_image", {"prompt": "x", "shape": "square"})
    capped = box.dispatch("make_image", {"prompt": "one more", "shape": "square"})
    assert capped.is_error and "per request" in str(capped.content)
    assert box.dispatch("make_image", {"prompt": " ", "shape": "square"}).is_error


def test_without_a_picture_service_the_tools_say_so() -> None:
    box = toolbox(None)
    for name, args in (
        ("find_images", {"query": "cats", "count": 2, "shape": "square"}),
        ("make_image", {"prompt": "a cat", "shape": "square"}),
    ):
        out = box.dispatch(name, args)
        assert out.is_error and "not available" in str(out.content)


def test_an_ai_made_picture_becomes_a_video_with_credits_in_the_result() -> None:
    _, api = make_org("acme")
    api_support.pictures = FakePictures()

    def slideshow(request: dict[str, Any]) -> Any:
        tool_results = json.dumps(request["messages"][-1]["content"])
        handles = re.findall(r"asset:[0-9a-f]{12,64}", tool_results)
        spec = {
            "version": 1,
            "inputs": {f"p{i}": h for i, h in enumerate(handles)},
            "steps": [
                {"id": "s", "op": "video.slideshow", "in": [f"p{i}" for i in range(len(handles))],
                 "args": {"seconds": 1}},
                {"id": "out", "op": "export", "in": "s", "args": {"preset": "web_h264"}},
            ],
            "outputs": ["out"],
        }  # fmt: skip
        return message([tool_use("run_pipeline", {"spec": spec})], "tool_use")

    api_support.model = ScriptedModel(
        message(
            [
                tool_use("find_images", {"query": "python code", "count": 1, "shape": "landscape"}),
                tool_use("make_image", {"prompt": "modern desk", "shape": "landscape"}),
            ],
            "tool_use",
        ),
        slideshow,
        final("Made your tutorial."),
    )
    job = api.post("/jobs", {"prompt": "search for images and create a tutorial for python"}).json()
    assert job["status"] in ("succeeded", "checks_failed"), job
    assert job["result"]["credits"] == ['"Photo 0" by Someone, CC BY 2.0 (via Openverse)']
    names = {a["filename"] for a in api.get("/assets").json()["items"]}
    assert any(n.startswith("found-photo-0") for n in names)
    assert any(n.startswith("made-modern-desk") for n in names)


def test_openverse_results_are_filtered_and_credited(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = {
        "results": [
            {"url": "https://a/small.jpg", "width": 400, "height": 300, "title": "tiny",
             "creator": "x", "license": "by", "license_version": "2.0"},
            {"url": "https://a/big.jpg", "width": 3000, "height": 2000, "title": "Big desk",
             "creator": "Ann", "license": "by-sa", "license_version": "4.0"},
            {"url": "https://a/pd.jpg", "width": 1200, "height": 800, "title": "Old map",
             "creator": "", "license": "pdm", "license_version": None},
        ]
    }  # fmt: skip
    seen: dict[str, str] = {}

    def fake_get(req: Any, timeout: float) -> dict[str, Any]:
        seen["url"] = req.full_url
        return sample

    monkeypatch.setattr(pictures, "_get_json", fake_get)
    found = pictures.OpenverseAndVultr(None, None, "m").search("desk", 5, "landscape")
    assert [f.title for f in found] == ["Big desk", "Old map"]  # small one dropped, big first
    assert found[0].credit == '"Big desk" by Ann, CC BY-SA 4.0 (via Openverse)'
    assert "public domain" in found[1].credit
    assert "license_type=commercial%2Cmodification" in seen["url"]
    assert "aspect_ratio=wide" in seen["url"] and "mature=false" in seen["url"]


def test_generation_adds_the_modern_style_and_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(PictureError, match="not configured"):
        pictures.OpenverseAndVultr(None, None, "m").generate("a cat", "square")
    sent: dict[str, Any] = {}

    def fake_get(req: Any, timeout: float) -> dict[str, Any]:
        sent.update(json.loads(req.data))
        return {"data": [{"b64_json": "aGVsbG8="}]}

    monkeypatch.setattr(pictures, "_get_json", fake_get)
    out = pictures.OpenverseAndVultr("https://v/v1", "k", "qwen-image-2.1").generate(
        "a cat", "portrait"
    )
    assert out == b"hello" and sent["size"] == "864x1536" and sent["model"] == "qwen-image-2.1"
    assert sent["prompt"].startswith("a cat ") and "No text" in sent["prompt"]


def test_commons_results_keep_reusable_licenses_and_put_recent_photos_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def page(i: int, license_: str, year: str, w: int = 4000, h: int = 3000) -> dict[str, Any]:
        return {
            "index": i,
            "title": f"File:Photo {i}.jpg",
            "imageinfo": [
                {
                    "width": w, "height": h, "mime": "image/jpeg",
                    "thumburl": f"https://upload.example/{i}.jpg",
                    "extmetadata": {
                        "LicenseShortName": {"value": license_},
                        "Artist": {"value": f"<a href='x'>Artist {i}</a>"},
                        "DateTimeOriginal": {"value": year},
                    },
                }
            ],
        }  # fmt: skip

    commons = {
        "query": {
            "pages": {
                "1": page(1, "CC BY-SA 4.0", "2009-05-01"),
                "2": page(2, "CC BY-NC 2.0", "2024-01-01"),  # not reusable commercially
                "3": page(3, "CC BY 2.0", "2024-03-02"),
                "4": page(4, "Public domain", ""),
                "5": page(5, "CC BY 4.0", "2023", w=600, h=400),  # too small
            }
        }
    }

    def fake_get(req: Any, timeout: float) -> dict[str, Any]:
        if "wikimedia" in req.full_url:
            return commons
        raise PictureError("the picture service answered 403: rate limited")

    monkeypatch.setattr(pictures, "_get_json", fake_get)
    monkeypatch.setattr(pictures.time, "sleep", lambda s: None)
    found = pictures.OpenverseAndVultr(None, None, "m").search("friends", 5, "landscape")
    assert [f.title for f in found] == ["Photo 3", "Photo 1", "Photo 4"]  # recent, then dated
    # old, then undated (on Commons usually a scan of an old artwork)
    assert found[0].credit == '"Photo 3" by Artist 3, CC BY 2.0 (via Wikimedia Commons)'
    assert found[0].year == 2024 and found[0].url == "https://upload.example/3.jpg"
