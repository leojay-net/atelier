"""Pictures the agent gets by itself: `find_images` (openly licensed photos from Openverse) and
`make_image` (generated with Vultr Serverless Inference image models).

Both run in the trusted worker process, never in a step sandbox, and both hand back ordinary
image assets, so every later step stays deterministic (it consumes a content-addressed file).

Found photos are limited to licenses that allow commercial use and modification (CC0, PDM,
CC BY, CC BY-SA), large, non-mature photographs. Each keeps its credit line, which the job
result lists so a user can publish the video with proper attribution.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

OPENVERSE = "https://api.openverse.org/v1/images/"
COMMONS = "https://commons.wikimedia.org/w/api.php"
RECENT_YEAR = 2015  # photos taken since then rank first ("looks current")
SHAPES = {"landscape": "1536x864", "portrait": "864x1536", "square": "1024x1024"}
ASPECT = {"landscape": "wide", "portrait": "tall", "square": "square"}
# Appended to every generation prompt so pictures look current and photographic by default.
MODERN = (
    "Contemporary, modern 2026 aesthetic, high-end professional photography, natural light, "
    "sharp focus, clean composition, vibrant but realistic colors. No text, no letters, "
    "no watermarks, no logos."
)
USER_AGENT = "Atelier/1.0 (media agent; https://github.com/leojay-net/atelier)"


class PictureError(Exception):
    """A picture could not be found or made; the message is shown to the model as-is."""


@dataclass(frozen=True)
class Found:
    url: str
    width: int
    height: int
    title: str
    credit: str  # e.g. '"Team meeting" by Jane Doe, CC BY 2.0'
    source_page: str
    year: int | None = None  # when it was taken, if known


class PictureService(Protocol):
    def search(self, query: str, count: int, shape: str) -> list[Found]: ...

    def download(self, found: Found) -> bytes: ...

    def generate(self, prompt: str, shape: str, style: str | None = None) -> bytes:
        """A new picture; `style` replaces the default modern-photo look (e.g. animation)."""
        ...


def _get_json(req: urllib.request.Request, timeout: float) -> dict:  # type: ignore[type-arg]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        raise PictureError(f"the picture service answered {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise PictureError("the picture service could not be reached") from None


class OpenverseAndVultr:
    def __init__(self, vultr_base: str | None, vultr_key: str | None, model: str) -> None:
        self.vultr_base, self.vultr_key, self.model = vultr_base, vultr_key, model

    def search(self, query: str, count: int, shape: str) -> list[Found]:
        """Wikimedia Commons and Openverse together (either can be rate-limited or empty),
        only licenses that allow reuse and editing, recent photos first."""
        results: list[Found] = []
        errors = []
        for source in (self._commons, self._openverse):
            try:
                results += source(query, count * 3, shape)
            except PictureError as e:
                errors.append(str(e))
        if not results and errors:
            raise PictureError(errors[0])
        seen: set[str] = set()
        unique = [f for f in results if not (f.url in seen or seen.add(f.url))]

        def rank(f: Found) -> tuple[int, int, int]:
            tier = 1 if f.year is None else 0 if f.year >= RECENT_YEAR else 2
            return (tier, -(f.year or 0), -(f.width * f.height))

        return sorted(unique, key=rank)[:count]

    def _openverse(self, query: str, count: int, shape: str) -> list[Found]:
        params = {
            "q": query,
            "page_size": str(min(20, count)),
            "license_type": "commercial,modification",
            "category": "photograph",
            "size": "large",
            "mature": "false",
        }
        if shape in ASPECT:
            params["aspect_ratio"] = ASPECT[shape]
        req = urllib.request.Request(
            OPENVERSE + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT}
        )
        try:
            results = _get_json(req, 30).get("results") or []
        except PictureError:
            time.sleep(2)  # anonymous use is rate-limited (20/min); one polite retry
            results = _get_json(req, 30).get("results") or []
        found = []
        for r in results:
            w, h = int(r.get("width") or 0), int(r.get("height") or 0)
            if min(w, h) < 700 or not r.get("url"):
                continue  # too small to look good full-screen
            license_ = f"CC {r.get('license', '').upper()} {r.get('license_version') or ''}".strip()
            if r.get("license") in ("cc0", "pdm"):
                license_ = "public domain" if r["license"] == "pdm" else "CC0"
            title = (r.get("title") or "untitled")[:80]
            creator = (r.get("creator") or "unknown")[:60]
            found.append(
                Found(
                    url=r["url"],
                    width=w,
                    height=h,
                    title=title,
                    credit=f'"{title}" by {creator}, {license_} (via Openverse)',
                    source_page=r.get("foreign_landing_url") or "",
                )
            )
        return found

    def _commons(self, query: str, count: int, shape: str) -> list[Found]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6",
            "gsrlimit": str(min(30, count)),
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
            "iiurlwidth": "1600",
            "iiextmetadatafilter": "LicenseShortName|Artist|ObjectName|DateTimeOriginal",
        }
        req = urllib.request.Request(
            COMMONS + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT}
        )
        pages = (_get_json(req, 30).get("query") or {}).get("pages") or {}
        found = []
        for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata") or {}
            license_ = _meta(meta, "LicenseShortName")
            w, h = int(info.get("width") or 0), int(info.get("height") or 0)
            if not _reusable(license_) or min(w, h) < 900:
                continue
            if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
                continue
            if shape and _shape(w, h) != shape:
                continue
            title = _meta(meta, "ObjectName") or page.get("title", "").removeprefix("File:")
            title = re.sub(r"\.(jpe?g|png|webp)$", "", title, flags=re.I)[:80]
            artist = _meta(meta, "Artist")[:60] or "unknown"
            year = re.search(r"(19|20)\d\d", _meta(meta, "DateTimeOriginal"))
            found.append(
                Found(
                    url=info.get("thumburl") or info.get("url", ""),
                    width=w,
                    height=h,
                    title=title,
                    credit=f'"{title}" by {artist}, {license_} (via Wikimedia Commons)',
                    source_page=info.get("descriptionurl", ""),
                    # undated Commons files are often scans of old artworks: rank them as old
                    year=int(year.group(0)) if year else 1900,
                )
            )
        return found

    def download(self, found: Found) -> bytes:
        """The photo's bytes, through the same guards as chat links (public hosts only)."""
        import tempfile
        from pathlib import Path

        from atelier.links import LinkError, fetch

        with tempfile.TemporaryDirectory() as tmp:
            try:
                fetch(found.url, Path(tmp) / "p", max_bytes=25 << 20)
            except LinkError as e:
                raise PictureError(str(e)) from None
            return (Path(tmp) / "p").read_bytes()

    def generate(self, prompt: str, shape: str, style: str | None = None) -> bytes:
        if not (self.vultr_base and self.vultr_key):
            raise PictureError("image generation is not configured on this server")
        body = json.dumps(
            {
                "model": self.model,
                "prompt": f"{prompt.strip()} {style or MODERN}",
                "size": SHAPES.get(shape, SHAPES["square"]),
                "n": 1,
            }
        ).encode()
        req = urllib.request.Request(
            self.vultr_base.rstrip("/") + "/images/generations",
            data=body,
            headers={
                "Authorization": f"Bearer {self.vultr_key}",
                "Content-Type": "application/json",
            },
        )
        for wait in (4, 8, 16, None):  # the service rate-limits bursts (429); back off and retry
            try:
                data = (_get_json(req, 180).get("data") or [{}])[0]
                break
            except PictureError as e:
                if wait is None or " 429" not in str(e):
                    raise
                time.sleep(wait)
        if data.get("b64_json"):
            import base64

            return base64.b64decode(data["b64_json"])
        url = data.get("url")
        if not url:
            raise PictureError("the image service returned no picture")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return resp.read(30 << 20)
        except (urllib.error.URLError, TimeoutError):
            raise PictureError("the generated picture could not be downloaded") from None


def _meta(meta: dict, key: str) -> str:  # type: ignore[type-arg]
    """A Commons extmetadata value as plain text (it is often HTML)."""
    value = str((meta.get(key) or {}).get("value") or "")
    return " ".join(re.sub(r"<[^>]+>", " ", value).split())


def _reusable(license_: str) -> bool:
    """Licenses that allow commercial reuse and editing: public domain, CC0, CC BY, CC BY-SA."""
    parts = license_.lower().replace(" ", "-").split("-")
    if "nc" in parts or "nd" in parts:
        return False
    return license_.lower().startswith(("public domain", "cc0", "pd", "cc by"))


def _shape(w: int, h: int) -> str:
    return "landscape" if w > h * 1.15 else "portrait" if h > w * 1.15 else "square"


def from_environment() -> PictureService:
    key = os.environ.get("VULTR_INFERENCE_API_KEY") or os.environ.get("VULTR_API_KEY")
    base = os.environ.get("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1")
    return OpenverseAndVultr(base, key, os.environ.get("ATELIER_IMAGE_MODEL", "qwen-image-2.1"))
