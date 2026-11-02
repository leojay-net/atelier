"""Reading PDFs: describe them, extract their text, find text on a page, render pages.

pikepdf sees the structure (fonts, forms, actions); pypdfium2 sees what a viewer sees (text with
positions, rendered pixels). Both are given untrusted files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pikepdf
import pypdfium2 as pdfium
from PIL import Image

from atelier.docs import pdfio
from atelier.ops.registry import OpError

MAX_LISTED = 50
TEXT_CAP = 400_000  # characters read for a description; more than this is not needed to judge
MAX_DEPTH = 4  # how deep into form XObjects the font search goes


# -- structure ---------------------------------------------------------------------------------


def _font_records(fonts: Any, seen: set[str], out: list[dict[str, Any]]) -> None:
    for _, font in fonts.items():
        base = str(font.get("/BaseFont", "/?")).lstrip("/")
        descriptor = font.get("/FontDescriptor")
        kids = font.get("/DescendantFonts")
        if descriptor is None and kids:
            descriptor = kids[0].get("/FontDescriptor")
        embedded = descriptor is not None and any(
            k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3")
        )
        if base not in seen:
            seen.add(base)
            out.append({"name": base.split("+")[-1], "embedded": bool(embedded)})


def _walk_resources(resources: Any, seen: set[str], out: list[dict[str, Any]], depth: int) -> None:
    if resources is None or depth > MAX_DEPTH:
        return
    if "/Font" in resources:
        _font_records(resources["/Font"], seen, out)
    xobjects = resources.get("/XObject")
    if xobjects is not None:
        for _, xo in xobjects.items():
            if xo.get("/Subtype") == "/Form":
                _walk_resources(xo.get("/Resources"), seen, out, depth + 1)


def fonts_of(pdf: pikepdf.Pdf) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for page in pdf.pages:
        _walk_resources(page.get("/Resources"), seen, out, 0)
        if len(out) >= MAX_LISTED:
            break
    return out[:MAX_LISTED]


def form_fields(pdf: pikepdf.Pdf) -> list[dict[str, Any]]:
    if "/AcroForm" not in pdf.Root:
        return []
    fields = []
    for f in pdf.acroform.fields:
        fields.append(
            {
                "name": f.fully_qualified_name,
                "type": "checkbox" if f.is_checkbox else "text" if f.is_text else "other",
                "value": ("on" if f.is_checked else "off")
                if f.is_checkbox
                else str(f.value_as_string)[:80],
            }
        )
    return fields[:MAX_LISTED]


def has_javascript(pdf: pikepdf.Pdf) -> bool:
    names = pdf.Root.get("/Names")
    if names is not None and "/JavaScript" in names:
        return True
    if "/OpenAction" in pdf.Root:
        return True
    for page in pdf.pages:
        for annot in page.get("/Annots", []):
            action = annot.get("/A")
            if action is not None and str(action.get("/S")) == "/JavaScript":
                return True
    return False


def describe(path: Path) -> dict[str, Any]:
    pdf = pdfio.open_pdf(path)
    with pdf:
        sizes = []
        for page in pdf.pages:
            x0, y0, x1, y1 = (float(v) for v in page.mediabox)
            w, h = abs(x1 - x0), abs(y1 - y0)
            if int(page.get("/Rotate", 0)) % 180:
                w, h = h, w
            size = [round(w, 1), round(h, 1)]
            if size not in sizes:
                sizes.append(size)
        names = pdf.Root.get("/Names")
        return {
            "pages": len(pdf.pages),
            "sizes_pt": sizes[:10],
            "text_chars": len(all_text(path)),
            "fonts": fonts_of(pdf),
            "fields": form_fields(pdf),
            "javascript": has_javascript(pdf),
            "attachments": names is not None and "/EmbeddedFiles" in names,
            "title": str(pdf.docinfo.get("/Title", ""))[:120],
            "problems": [str(x)[:160] for x in pdf.check_pdf_syntax()[:5]],
        }


# -- content (pypdfium2) -----------------------------------------------------------------------


def open_render(path: Path) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        raise OpError(f"the PDF cannot be rendered: {str(e)[:120]}") from None


def page_text(doc: pdfium.PdfDocument, index: int) -> str:
    page = doc[index]
    try:
        tp = page.get_textpage()
        try:
            return str(tp.get_text_range())
        finally:
            tp.close()
    finally:
        page.close()


def all_text(path: Path) -> str:
    """The text of the whole file as a viewer would select it (capped)."""
    doc = open_render(path)
    try:
        parts: list[str] = []
        total = 0
        for i in range(len(doc)):
            t = page_text(doc, i)
            parts.append(t)
            total += len(t)
            if total > TEXT_CAP:
                break
        return "\n".join(parts)
    finally:
        doc.close()


def find_boxes(
    doc: pdfium.PdfDocument, index: int, pattern: re.Pattern[str]
) -> list[tuple[float, float, float, float]]:
    """Boxes (PDF points, origin bottom-left) covering every match of `pattern` on a page.

    One box per run of characters on the same line, so a match that wraps gives one per line."""
    page = doc[index]
    try:
        tp = page.get_textpage()
        try:
            text = tp.get_text_range()
            boxes: list[tuple[float, float, float, float]] = []
            for m in pattern.finditer(text):
                run: list[float] | None = None
                for i in range(m.start(), m.end()):
                    if text[i] in "\r\n":
                        continue
                    left, bottom, right, top = tp.get_charbox(i)
                    if right <= left or top <= bottom:
                        continue
                    middle = (bottom + top) / 2
                    if run is not None and not run[1] <= middle <= run[3]:  # the next line
                        boxes.append((run[0], run[1], run[2], run[3]))
                        run = None
                    if run is None:
                        run = [left, bottom, right, top]
                    else:
                        run = [min(run[0], left), min(run[1], bottom), max(run[2], right),
                               max(run[3], top)]  # fmt: skip
                if run is not None:
                    boxes.append((run[0], run[1], run[2], run[3]))
            return boxes
        finally:
            tp.close()
    finally:
        page.close()


def render_page(doc: pdfium.PdfDocument, index: int, dpi: int = 100) -> Image.Image:
    page = doc[index]
    try:
        bitmap = page.render(scale=dpi / 72)
        image: Image.Image = bitmap.to_pil().convert("RGB")
        return image
    finally:
        page.close()
