"""Files given as links in a chat message ("use this song: https://…/track.mp3").

The worker downloads up to MAX_LINKS links from a prompt before the agent starts and adds them
as assets, so a user who only has a link (a Google Drive or Dropbox share, a direct file URL)
does not need to download and re-upload anything.

Guarded, because the server fetches what a stranger typed:
- http(s) only, standard ports, at most MAX_REDIRECTS redirects, each one re-checked;
- every address a host resolves to must be public (no loopback, private, link-local or
  reserved ranges), so a link cannot reach the API, the metadata service or the Docker network;
- a size cap (the organization's upload limit) and a time limit; web pages are refused;
- streaming sites (YouTube, Spotify, …) are refused with a plain explanation: their pages
  are not files, and their terms do not allow downloading.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path

MAX_LINKS = 3
MAX_REDIRECTS = 5
TIMEOUT = 30.0
USER_AGENT = "Atelier/1.0 (media fetch)"

_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
STREAMING = (
    "youtube.com", "youtu.be", "spotify.com", "soundcloud.com", "music.apple.com",
    "tiktok.com", "instagram.com", "facebook.com", "fb.watch", "deezer.com", "audiomack.com",
    "boomplay.com", "tidal.com", "x.com", "twitter.com", "vimeo.com",
)  # fmt: skip


class LinkError(Exception):
    """Why a link could not be used, in words for a non-technical person."""


def find_links(text: str) -> list[str]:
    """The distinct URLs in a message, in order, without trailing punctuation."""
    found: list[str] = []
    for match in _URL.findall(text):
        url = match.rstrip(".,;:!?)]}")
        if url not in found:
            found.append(url)
    return found


def direct_url(url: str) -> str:
    """Turn a Google Drive or Dropbox share link into a direct download link."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "drive.google.com":
        m = re.search(r"/file/d/([\w-]+)", parts.path)
        file_id = m.group(1) if m else urllib.parse.parse_qs(parts.query).get("id", [""])[0]
        if file_id:
            return f"https://drive.google.com/uc?export=download&id={file_id}"
    if host.endswith("dropbox.com"):
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
        query["dl"] = ["1"]
        return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, True)))
    return url


def _streaming(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in STREAMING)


def _check_host(url: str, allow_private: bool) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise LinkError("only http and https links can be downloaded")
    host = (parts.hostname or "").lower()
    if not host:
        raise LinkError("that link has no website in it")
    if _streaming(host):
        raise LinkError(
            f"{host} is a streaming site, so the file cannot be downloaded from it; "
            "please upload the file itself (or share it from Google Drive or Dropbox)"
        )
    if allow_private:  # tests only: a local test server on a random port
        return
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise LinkError(f"the website {host} could not be found") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise LinkError("that link points to a private network address and was not downloaded")
    if parts.port not in (None, 80, 443):
        raise LinkError("that link uses an unusual port and was not downloaded")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None  # handled by hand, so every hop is checked


_OPENER = urllib.request.build_opener(_NoRedirect)


def _filename(url: str, headers: Message) -> str:
    name = headers.get_filename() or Path(urllib.parse.urlsplit(url).path).name
    return urllib.parse.unquote(name or "")[:150] or "download"


def fetch(
    url: str,
    dest: Path,
    *,
    max_bytes: int,
    timeout: float = TIMEOUT,
    allow_private: bool = False,
) -> str:
    """Download `url` into `dest`; return a filename for it. Raises LinkError."""
    current = direct_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        _check_host(current, allow_private)
        request = urllib.request.Request(current, headers={"User-Agent": USER_AGENT})
        try:
            response = _OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                current = urllib.parse.urljoin(current, e.headers["Location"])
                continue
            raise LinkError(f"the website answered with an error ({e.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise LinkError("the website could not be reached") from None
        with response:
            ctype = (response.headers.get_content_type() or "").lower()
            if ctype in ("text/html", "application/xhtml+xml"):
                raise LinkError(
                    "that link opens a web page, not a file; use a direct link to the file "
                    "or upload it"
                )
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise LinkError(f"the file is larger than {max_bytes // 2**20} MB")
            written = 0
            with dest.open("wb") as out:
                while chunk := response.read(1 << 16):
                    written += len(chunk)
                    if written > max_bytes:
                        raise LinkError(f"the file is larger than {max_bytes // 2**20} MB")
                    out.write(chunk)
            if written == 0:
                raise LinkError("the link gave an empty file")
            return _filename(current, response.headers)
    raise LinkError("that link redirects too many times")
