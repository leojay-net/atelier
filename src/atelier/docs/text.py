"""Run-aware text editing for Word and PowerPoint paragraphs.

Word and PowerPoint split a paragraph into runs wherever the formatting changes, and a spell
checker or an edit history splits it in unpredictable places, so `{{name}}` is often stored as
`{{na` + `me}}` in two runs. Replacing per run misses those; replacing per paragraph and
rewriting the text loses the formatting. This does neither: it finds matches in the paragraph's
whole text, puts the replacement into the run where the match starts (so it keeps that run's
formatting) and cuts the rest of the match out of the runs that follow.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w.\-]*)\s*\}\}")

Element = etree._Element


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str


Matcher = Callable[[str], list[Edit]]


def text_nodes(paragraph: Element, ns: str) -> list[Element]:
    """The text elements (`w:t` or `a:t`) of a paragraph, in reading order, hyperlinks included."""
    return list(paragraph.iter(f"{{{ns}}}t"))


def set_text(node: Element, text: str) -> None:
    node.text = text
    if text != text.strip():
        node.set(XML_SPACE, "preserve")


def apply(nodes: list[Any], matcher: Matcher, setter: Callable[[Any, str], None] = set_text) -> int:
    """Apply `matcher`'s edits to the text of `nodes` as one string; returns how many were made.
    `nodes` are XML text elements, or anything with a `.text` if `setter` knows how to write it."""
    texts = [n.text or "" for n in nodes]
    edits = matcher("".join(texts))
    if not edits:
        return 0
    starts = [0]
    for t in texts:
        starts.append(starts[-1] + len(t))
    new = list(texts)
    for edit in reversed(edits):  # right to left: earlier offsets stay valid
        first = next(k for k in range(len(texts)) if starts[k] <= edit.start < starts[k + 1])
        for k in range(first, len(texts)):
            if starts[k] >= edit.end:
                break
            lo = max(edit.start, starts[k]) - starts[k]
            hi = min(edit.end, starts[k + 1]) - starts[k]
            new[k] = new[k][:lo] + (edit.text if k == first else "") + new[k][hi:]
    for node, before, after in zip(nodes, texts, new, strict=True):
        if after != before:
            setter(node, after)
    return len(edits)


def paragraphs(root: Element, ns: str) -> Iterator[Element]:
    """Every paragraph under `root` (tables, text boxes and groups included)."""
    yield from root.iter(f"{{{ns}}}p")


def fill_matcher(data: dict[str, Any], missing: set[str], used: set[str]) -> Matcher:
    """Replace `{{key}}` by data[key] (dotted keys reach into nested dicts)."""

    def lookup(key: str) -> tuple[bool, Any]:
        cur: Any = data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return False, None
            cur = cur[part]
        return True, cur

    def match(text: str) -> list[Edit]:
        edits = []
        for m in PLACEHOLDER.finditer(text):
            found, value = lookup(m.group(1))
            if not found:
                missing.add(m.group(1))
                continue
            used.add(m.group(1).split(".")[0])
            edits.append(Edit(m.start(), m.end(), render(value)))
        return edits

    return match


def render(value: Any) -> str:
    """A data value as it should read in a document."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.10g}"
    if value is None:
        return ""
    return str(value)


def replace_matcher(
    pairs: list[tuple[str, str]],
    *,
    match_case: bool,
    whole_word: bool,
    counts: dict[str, int] | None = None,
) -> Matcher:
    """Replace each `find` by its text; `counts[find]` says how often each one matched."""
    flags = 0 if match_case else re.IGNORECASE
    compiled = []
    for find, repl in pairs:
        body = re.escape(find)
        if whole_word:
            body = rf"(?<!\w){body}(?!\w)"
        compiled.append((find, re.compile(body, flags), repl))

    def match(text: str) -> list[Edit]:
        edits: list[Edit] = []
        for find, pattern, repl in compiled:
            for m in pattern.finditer(text):
                if not any(e.start < m.end() and m.start() < e.end for e in edits):
                    edits.append(Edit(m.start(), m.end(), repl))
                    if counts is not None:
                        counts[find] = counts.get(find, 0) + 1
        return sorted(edits, key=lambda e: e.start)

    return match


def placeholders_in(text: str) -> list[str]:
    return [m.group(1) for m in PLACEHOLDER.finditer(text)]
