"""Files given as links in a chat message, and a job's conversation thread."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_api import make_org

import api_support
from atelier.api.core import links
from conftest import FIXTURES
from harness.fake_model import ScriptedModel, final

pytestmark = pytest.mark.django_db(transaction=True)

PHOTO = (FIXTURES / "photo.png").read_bytes()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        routes: dict[str, tuple[int, dict[str, str], bytes]] = {
            "/photo.png": (200, {"Content-Type": "image/png"}, PHOTO),
            "/page": (200, {"Content-Type": "text/html"}, b"<html>hi</html>"),
            "/hop": (302, {"Location": "/photo.png"}, b""),
            "/loop": (302, {"Location": "/loop"}, b""),
            "/big": (200, {"Content-Type": "audio/mpeg", "Content-Length": "999999999"}, b"x"),
            "/empty": (200, {"Content-Type": "audio/mpeg"}, b""),
            "/named": (
                200,
                {
                    "Content-Type": "image/png",
                    "Content-Disposition": 'attachment; filename="me.png"',
                },
                PHOTO,
            ),
        }
        code, headers, body = routes.get(self.path, (404, {}, b""))
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        if "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def isolated(settings: Any, tmp_path: Path) -> None:
    settings.ATELIER_STORE_ROOT = tmp_path / "orgs"
    settings.ATELIER_QUEUE = "inline"
    settings.ATELIER_MODEL_CLIENT = "api_support.model_client"
    settings.ATELIER_VOICE_CLIENT = "api_support.voice_client"
    api_support.model = None
    api_support.voice = None


# -- the link helpers -------------------------------------------------------------------------


def test_find_links_in_a_casual_message() -> None:
    text = "use this song pls https://example.com/a.mp3, and https://x.org/b.wav) thx!! https://example.com/a.mp3"
    assert links.find_links(text) == ["https://example.com/a.mp3", "https://x.org/b.wav"]


def test_share_links_become_direct_downloads() -> None:
    assert (
        links.direct_url("https://drive.google.com/file/d/AbC_123/view?usp=sharing")
        == "https://drive.google.com/uc?export=download&id=AbC_123"
    )
    assert "dl=1" in links.direct_url("https://www.dropbox.com/s/xyz/song.mp3?dl=0")
    assert links.direct_url("https://example.com/a.mp3") == "https://example.com/a.mp3"


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("https://www.youtube.com/watch?v=abc", "streaming site"),
        ("https://youtu.be/abc", "streaming site"),
        ("https://open.spotify.com/track/1", "streaming site"),
        ("ftp://example.com/a.mp3", "only http and https"),
        ("http://127.0.0.1/a.mp3", "private network"),
        ("http://localhost/a.mp3", "private network"),
        ("http://10.0.0.5/a.mp3", "private network"),
        ("http://169.254.169.254/latest/meta-data", "private network"),
        ("http://example.com:8080/a.mp3", "unusual port"),
    ],
)
def test_unsafe_or_unsupported_links_are_refused(url: str, says: str, tmp_path: Path) -> None:
    with pytest.raises(links.LinkError, match=says):
        links.fetch(url, tmp_path / "f", max_bytes=10_000_000)


def test_fetch_follows_checked_redirects_and_refuses_pages_and_big_files(
    server: str, tmp_path: Path
) -> None:
    def get(path: str, **kw: Any) -> str:
        return links.fetch(server + path, tmp_path / "f", allow_private=True, **kw)

    assert get("/hop", max_bytes=10_000_000) == "photo.png"
    assert (tmp_path / "f").read_bytes() == PHOTO
    assert get("/named", max_bytes=10_000_000) == "me.png"
    with pytest.raises(links.LinkError, match="web page"):
        get("/page", max_bytes=10_000_000)
    with pytest.raises(links.LinkError, match="larger than"):
        get("/big", max_bytes=10_000_000)
    with pytest.raises(links.LinkError, match="larger than"):
        get("/photo.png", max_bytes=100)
    with pytest.raises(links.LinkError, match="empty"):
        get("/empty", max_bytes=10_000_000)
    with pytest.raises(links.LinkError, match="redirects too many"):
        get("/loop", max_bytes=10_000_000)
    with pytest.raises(links.LinkError, match="error \\(404\\)"):
        get("/missing", max_bytes=10_000_000)


# -- in a chat message --------------------------------------------------------------------------


def test_a_linked_file_becomes_an_asset_the_ai_can_use(settings: Any, server: str) -> None:
    settings.ATELIER_LINKS_ALLOW_PRIVATE = True
    _, api = make_org("acme")
    api_support.model = ScriptedModel(final("Got your picture."))
    job = api.post(
        "/jobs",
        {"prompt": f"make a card from this {server}/named and also https://youtu.be/xyz"},
    ).json()
    seen = api_support.model.requests[0]["messages"][0]["content"]
    seen = seen if isinstance(seen, str) else " ".join(b.get("text", "") for b in seen)
    assert "Links in the message:" in seen
    assert f"{server}/named was downloaded: image" in seen
    assert "youtu.be/xyz could not be used: youtu.be is a streaming site" in seen
    assets = api.get("/assets").json()["items"]
    assert [a["filename"] for a in assets] == ["me.png"]
    events = [e for e in api.get(f"/jobs/{job['id']}").json()["events"] if e["kind"] == "link"]
    states = [e["data"]["state"] for e in events]
    assert states.count("downloading") == 2 and "done" in states and "failed" in states


def test_private_links_are_refused_in_production_settings(server: str) -> None:
    _, api = make_org("acme")
    api_support.model = ScriptedModel(final("Ok."))
    api.post("/jobs", {"prompt": f"use {server}/photo.png"})
    seen = str(api_support.model.requests[0]["messages"][0]["content"])
    assert "private network address" in seen
    assert api.get("/assets").json()["count"] == 0


# -- conversation thread ------------------------------------------------------------------------


def test_thread_lists_the_conversation_oldest_first() -> None:
    _, api = make_org("acme")
    _, other = make_org("other")
    api_support.model = ScriptedModel(final("One."), final("Two."), final("Three."))
    first = api.post("/jobs", {"prompt": "first"}).json()
    second = api.post("/jobs", {"prompt": "second", "parent": first["id"]}).json()
    third = api.post("/jobs", {"prompt": "third", "parent": second["id"]}).json()
    thread = api.get(f"/jobs/{third['id']}/thread").json()["items"]
    assert [j["prompt"] for j in thread] == ["first", "second", "third"]
    assert api.get(f"/jobs/{first['id']}/thread").json()["items"][0]["id"] == first["id"]
    assert other.get(f"/jobs/{third['id']}/thread").status_code == 404
