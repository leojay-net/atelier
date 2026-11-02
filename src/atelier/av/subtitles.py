"""SRT and WebVTT parsing for `video.captions`.

Caption files are untrusted text. The parser keeps only timing and plain words: markup tags,
ASS override blocks (`{\\an8}`), entities and control characters are removed, so nothing in a
caption file can influence styling or reach anything but the text renderer.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from atelier.ops.registry import OpError

MAX_BYTES = 1 << 20
_TIME = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_RANGE = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}(?:\s.*)?$")
_LOOKS_LIKE_RANGE = re.compile(r"-->")
_TAG = re.compile(r"<[^>\n]{0,200}>")
_ASS = re.compile(r"\{\\[^}\n]{0,200}\}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")


class SubtitleError(OpError):
    """A caption file that could not be read; the message says where and what."""


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def _seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def clean(text: str) -> str:
    """Plain words only: no tags, no ASS overrides, no entities, no control characters."""
    text = _ASS.sub("", _TAG.sub("", text))
    text = html.unescape(text).replace(" ", " ")
    text = _CONTROL.sub("", text)
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def parse(raw: str) -> list[Cue]:
    if len(raw) > MAX_BYTES:
        raise SubtitleError(f"the caption file is too large (over {MAX_BYTES // 1024} KB)")
    lines = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[Cue] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _LOOKS_LIKE_RANGE.search(line):
            i += 1
            continue
        match = _RANGE.match(line)
        if match is None:
            shown = line.strip()[:60]
            raise SubtitleError(f"captions line {i + 1}: cannot read the time range {shown!r}")
        g = match.groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        if end <= start:
            raise SubtitleError(f"captions line {i + 1}: the cue ends ({end:g}s) before it starts")
        body = []
        i += 1
        while i < len(lines) and lines[i].strip() != "":
            body.append(lines[i])
            i += 1
        if text := clean("\n".join(body)):
            cues.append(Cue(start, end, text))
    if not cues:
        raise SubtitleError("no captions found: expected SRT or WebVTT text with timed cues")
    return cues
