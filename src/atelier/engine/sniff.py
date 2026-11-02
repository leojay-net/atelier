"""Media-type detection from magic bytes. Extensions are never trusted."""

from __future__ import annotations

import zipfile
from pathlib import Path

from atelier.media import MediaType

_TEXT_PROBE = 64 * 1024
_MAX_ZIP_ENTRIES = 10_000
_OFFICE_MARKERS = (
    ("word/document.xml", MediaType.DOCX, ".docx"),
    ("xl/workbook.xml", MediaType.XLSX, ".xlsx"),
    ("ppt/presentation.xml", MediaType.PPTX, ".pptx"),
)


def sniff(path: Path) -> tuple[MediaType, str]:
    with path.open("rb") as f:
        head = f.read(64)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return MediaType.IMAGE, ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return MediaType.IMAGE, ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return MediaType.IMAGE, ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return MediaType.IMAGE, ".webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return MediaType.AUDIO, ".wav"
    if head.startswith(b"%PDF-"):
        return MediaType.PDF, ".pdf"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return MediaType.AUDIO, ".m4a"
        if brand in (b"avif", b"avis"):
            return MediaType.IMAGE, ".avif"
        if brand in (b"heic", b"heix", b"mif1"):
            return MediaType.IMAGE, ".heic"
        return MediaType.VIDEO, ".mov" if brand == b"qt  " else ".mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return MediaType.VIDEO, ".mkv"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return MediaType.AUDIO, ".mp3"
    if head.startswith(b"OggS"):
        return MediaType.AUDIO, ".ogg"
    if head.startswith(b"fLaC"):
        return MediaType.AUDIO, ".flac"
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(path)
    if _looks_like_text(path):
        return MediaType.TEXT, ".txt"
    return MediaType.BINARY, ""


def _sniff_zip(path: Path) -> tuple[MediaType, str]:
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, OSError):
        return MediaType.BINARY, ""
    if len(infos) <= _MAX_ZIP_ENTRIES:
        names = {i.filename for i in infos}
        for marker, media, ext in _OFFICE_MARKERS:
            if marker in names:
                return media, ext
    return MediaType.BINARY, ".zip"


def _looks_like_text(path: Path) -> bool:
    with path.open("rb") as f:
        data = f.read(_TEXT_PROBE + 1)
    truncated = len(data) > _TEXT_PROBE
    data = data[:_TEXT_PROBE]
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte character cut off by the probe boundary is still text.
        return truncated and e.start >= len(data) - 3 and e.reason == "unexpected end of data"
    return True
