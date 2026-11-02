"""The bundled font pack. Ops may only use fonts listed in assets/fonts/fonts.json.

Host fonts differ between machines ("Arial" on macOS is not "Arial" on Linux), so nothing
outside the pack is ever loaded. The manifest records each file's SHA-256; files are
verified before use and the manifest hash is part of every text op's cache key.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from atelier.canonical import sha256_file, sha256_hex
from atelier.paths import FONTS

MANIFEST = FONTS / "fonts.json"
DEFAULT_FAMILY = "Inter"


class FontError(Exception):
    pass


@functools.cache
def manifest() -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FontError(f"font manifest missing at {MANIFEST}") from None
    return data


def families() -> tuple[str, ...]:
    return tuple(sorted(manifest()["families"]))


def weights(family: str) -> tuple[str, ...]:
    return tuple(manifest()["families"][family]["weights"])


@functools.cache
def font_path(family: str, weight: str) -> Path:
    try:
        entry = manifest()["families"][family]["weights"][weight]
    except KeyError:
        raise FontError(f"font {family!r} weight {weight!r} is not in the bundled pack") from None
    path = FONTS / str(entry["file"])
    if sha256_file(path) != entry["sha256"]:
        raise FontError(f"{path.name} does not match its manifest hash")
    return path


@functools.cache
def pack_id() -> str:
    return "fonts:" + sha256_hex(MANIFEST.read_bytes())[:16]
