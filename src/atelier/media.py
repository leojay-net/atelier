"""Media types and asset handles shared by every layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

HANDLE_PREFIX = "asset:"
SHORT_HASH_LEN = 12
MIN_PREFIX_LEN = 8
_HEX_RE = re.compile(r"^[0-9a-f]{8,64}$")


class MediaType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    BINARY = "binary"


@dataclass(frozen=True)
class AssetInfo:
    """What the store knows about one content-addressed object."""

    hash: str
    media: MediaType
    size: int
    ext: str

    @property
    def handle(self) -> str:
        return f"{HANDLE_PREFIX}{self.hash}"

    @property
    def short(self) -> str:
        """Display form. The validator accepts unique prefixes, so this is usable in specs."""
        return f"{HANDLE_PREFIX}{self.hash[:SHORT_HASH_LEN]}"

    def to_json(self) -> dict[str, Any]:
        return {"hash": self.hash, "media": self.media.value, "size": self.size, "ext": self.ext}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AssetInfo:
        return cls(
            hash=data["hash"], media=MediaType(data["media"]), size=data["size"], ext=data["ext"]
        )


class HandleError(Exception):
    """Base class for asset-handle resolution failures."""


class InvalidHandle(HandleError):
    pass


class AssetNotFound(HandleError):
    pass


class AmbiguousHandle(HandleError):
    def __init__(self, handle: str, count: int) -> None:
        super().__init__(f"{handle} matches {count} assets")
        self.count = count


def parse_handle(handle: object) -> str:
    """Return the (possibly abbreviated) hex digest of a handle, or raise InvalidHandle."""
    if not isinstance(handle, str) or not handle.startswith(HANDLE_PREFIX):
        raise InvalidHandle(str(handle))
    digest = handle[len(HANDLE_PREFIX) :].lower()
    if not _HEX_RE.match(digest):
        raise InvalidHandle(handle)
    return digest
