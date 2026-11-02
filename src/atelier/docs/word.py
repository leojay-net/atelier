"""Word documents: open, edit paragraphs run-aware, save deterministically, describe."""

from __future__ import annotations

import io
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import docx
from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from atelier.docs import package, text
from atelier.ops.registry import OpError

_TEXT_PARTS = (RT.HEADER, RT.FOOTER, RT.FOOTNOTES, RT.ENDNOTES)
MAX_LISTED = 50


def open_docx(path: Path) -> Document:
    package.check(path)
    try:
        return docx.Document(str(path))
    except Exception as e:  # python-docx raises a variety of errors on damaged input
        raise OpError(f"not a readable Word document: {str(e).splitlines()[0][:160]}") from None


def roots(doc: Document) -> Iterator[text.Element]:
    """The XML trees that hold text: the body, then headers, footers and notes."""
    yield doc.element
    seen = set()
    for rel in doc.part.rels.values():
        if rel.reltype in _TEXT_PARTS and not rel.is_external:
            part = rel.target_part
            element = getattr(part, "element", None)
            if element is not None and id(element) not in seen:
                seen.add(id(element))
                yield element


def edit(doc: Document, matcher: text.Matcher) -> int:
    """Apply a text edit to every paragraph in the document; returns the number of edits."""
    done = 0
    for root in roots(doc):
        for p in text.paragraphs(root, text.W):
            done += text.apply(text.text_nodes(p, text.W), matcher)
    return done


def paragraph_texts(doc: Document) -> Iterator[str]:
    for root in roots(doc):
        for p in text.paragraphs(root, text.W):
            yield "".join(n.text or "" for n in text.text_nodes(p, text.W))


def save(doc: Document, dst: Path) -> None:
    """Write `doc` to `dst` with a deterministic package."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.docx"
        doc.save(str(raw))
        package.write(raw, dst)


def describe(path: Path) -> dict[str, Any]:
    doc = open_docx(path)
    found: list[str] = []
    words = 0
    for t in paragraph_texts(doc):
        words += len(t.split())
        found += text.placeholders_in(t)
    body = doc.element.body
    return {
        "paragraphs": len(doc.paragraphs),
        "tables": len(doc.tables),
        "images": len(doc.inline_shapes),
        "sections": len(doc.sections),
        "words": words,
        "placeholders": sorted(set(found))[:MAX_LISTED],
        "tracked_changes": len(body.xpath(".//w:ins | .//w:del")),
        "comments": any(r.reltype == RT.COMMENTS for r in doc.part.rels.values()),
    }


def xml_of(element: etree._Element) -> bytes:
    out: bytes = etree.tostring(element)
    return out


# -- tables and pictures -----------------------------------------------------------------------


def iter_paragraphs(doc: Document) -> Iterator[Paragraph]:
    """Body paragraphs, then the paragraphs inside tables (nested tables included)."""

    def in_table(table: Table) -> Iterator[Paragraph]:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
                for inner in cell.tables:
                    yield from in_table(inner)

    yield from doc.paragraphs
    for table in doc.tables:
        yield from in_table(table)


def find_paragraph(doc: Document, at: str, occurrence: int, only_body: bool = False) -> Paragraph:
    pool = doc.paragraphs if only_body else iter_paragraphs(doc)
    matches = [p for p in pool if at in p.text]
    if not matches:
        raise OpError(f"{at!r} does not appear in the document; it must be text the file contains")
    if occurrence > len(matches):
        raise OpError(f"{at!r} appears {len(matches)} time(s) in the document, not {occurrence}")
    return matches[occurrence - 1]


def _shade(cell: Any, fill: str) -> None:
    props = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    props.append(shd)


def _borders(table: Table) -> None:
    props = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "808080")
        borders.append(el)
    props.append(borders)


def insert_table(doc: Document, p: Any) -> None:
    """Replace the body paragraph containing `p.at` by a table of `p.rows`."""
    anchor = find_paragraph(doc, p.at, p.occurrence, only_body=True)
    rows = p.rows
    tbl = doc.add_table(rows=len(rows), cols=len(rows[0]))
    tbl.autofit = True
    width = OxmlElement("w:tblW")
    width.set(qn("w:type"), "pct")
    width.set(qn("w:w"), "5000")  # the full text width
    for old in tbl._tbl.tblPr.findall(qn("w:tblW")):
        tbl._tbl.tblPr.remove(old)
    tbl._tbl.tblPr.append(width)
    if p.style in ("grid", "striped"):
        _borders(tbl)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = text.render(value)
            para = cell.paragraphs[0]
            if isinstance(value, int | float) and not isinstance(value, bool):
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            head = p.header and r == 0
            if head:
                for run in para.runs:
                    run.bold = True
            if p.style == "striped" and (head or r % 2 == 0):
                _shade(cell, "D9D9D9" if head else "F2F2F2")
    anchor._p.addprevious(tbl._tbl)
    parent = anchor._p.getparent()
    assert parent is not None
    parent.remove(anchor._p)


def text_width_pt(doc: Document) -> float:
    section = doc.sections[0]
    if section.page_width is None:
        return 450.0
    left = section.left_margin or 0
    right = section.right_margin or 0
    return float(section.page_width - left - right) / 12700


def insert_picture(doc: Document, p: Any, png: bytes, size: tuple[int, int]) -> None:
    """Replace the content of the paragraph containing `p.at` by the picture."""
    anchor = find_paragraph(doc, p.at, p.occurrence)
    limit = text_width_pt(doc)
    width = min(p.width_pt or limit, limit)
    for child in list(anchor._p):
        if child.tag != qn("w:pPr"):
            anchor._p.remove(child)
    anchor.add_run().add_picture(io.BytesIO(png), width=Pt(width))
