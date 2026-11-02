"""PowerPoint presentations: open, edit text run-aware, save deterministically, describe."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation as PresentationType
from pptx.util import Pt

from atelier.docs import package, text
from atelier.ops.registry import OpError

EMU_PER_POINT = 12700
MAX_LISTED = 50


def open_pptx(path: Path) -> PresentationType:
    package.check(path)
    try:
        return Presentation(str(path))
    except Exception as e:
        raise OpError(f"not a readable presentation: {str(e).splitlines()[0][:160]}") from None


def edit(prs: PresentationType, matcher: text.Matcher) -> int:
    done = 0
    for slide in prs.slides:
        for p in text.paragraphs(slide.element, text.A):
            done += text.apply(text.text_nodes(p, text.A), matcher)
    return done


def slide_texts(prs: PresentationType) -> list[list[str]]:
    """The paragraph texts of each slide, in order."""
    return [
        [
            "".join(n.text or "" for n in text.text_nodes(p, text.A))
            for p in text.paragraphs(slide.element, text.A)
        ]
        for slide in prs.slides
    ]


def save(prs: PresentationType, dst: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.pptx"
        prs.save(str(raw))
        package.write(raw, dst)


INSET_PT = 7.2  # PowerPoint's default text inset on each side is 0.1 in
DEFAULT_PT = 18.0
TITLE_PT = 32.0
CALIBRI_RATIO = 0.92  # Inter runs wider than PowerPoint's default font; scale the estimate down


def _font_size_pt(shape: Any) -> float:
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.size is not None:
                return float(run.font.size.pt)
    is_title = shape.is_placeholder and shape.placeholder_format.type in (
        1,
        3,
    )  # title, center title
    return TITLE_PT if is_title else DEFAULT_PT


def _lines_needed(paragraph: str, width_pt: float, font: Any) -> int:
    words, lines, line = paragraph.split(), 1, ""
    for word in words:
        trial = f"{line} {word}".strip()
        if font.getlength(trial) * CALIBRI_RATIO > width_pt and line:
            lines += 1
            line = word
        else:
            line = trial
    return lines


def overflow_of(prs: PresentationType) -> list[dict[str, Any]]:
    """Text boxes whose text probably does not fit (an estimate from the bundled font's widths).

    Only boxes with a fixed size and no auto-shrink are judged; the answer is a warning, since
    PowerPoint's own font and spacing differ a little from ours."""
    from PIL import ImageFont

    from atelier import fonts

    found = []
    for number, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame or not shape.text_frame.text.strip():
                continue
            if not shape.width or not shape.height:
                continue
            frame = shape.text_frame
            if frame.auto_size is not None and int(frame.auto_size) == 2:  # shrink on overflow
                continue
            size = _font_size_pt(shape)
            font = ImageFont.truetype(str(fonts.font_path(fonts.DEFAULT_FAMILY, "regular")), size)
            width = shape.width / EMU_PER_POINT - 2 * INSET_PT
            lines = sum(_lines_needed(par.text, width, font) for par in frame.paragraphs)
            need = lines * size * 1.2 + 2 * INSET_PT
            have = shape.height / EMU_PER_POINT
            if need > have * 1.05:
                found.append(
                    {
                        "slide": number,
                        "shape": shape.name,
                        "need_pt": round(need),
                        "have_pt": round(have),
                    }
                )
    return found[:MAX_LISTED]


def describe(path: Path) -> dict[str, Any]:
    prs = open_pptx(path)
    found: list[str] = []
    titles: list[str] = []
    for slide, lines in zip(prs.slides, slide_texts(prs), strict=True):
        for line in lines:
            found += text.placeholders_in(line)
        title = slide.shapes.title
        titles.append(
            title.text_frame.text[:80] if title is not None and title.has_text_frame else ""
        )
    return {
        "slides": len(prs.slides),
        "size_pt": [
            int(prs.slide_width or 0) // EMU_PER_POINT,
            int(prs.slide_height or 0) // EMU_PER_POINT,
        ],
        "layouts": [layout.name for layout in prs.slide_layouts][:MAX_LISTED],
        "titles": titles[:MAX_LISTED],
        "placeholders": sorted(set(found))[:MAX_LISTED],
        "overflow": overflow_of(prs),
    }


# -- tables and pictures -----------------------------------------------------------------------

_TABLE_STYLE = {
    "plain": "{2D5ABB26-0587-4C30-8999-92F81FD0307C}",  # no style, no grid
    "grid": "{5940675A-B579-460E-94D1-54222C63F5DA}",  # no style, table grid
    "striped": "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}",  # medium style 2, accent 1
}


def find_shape(prs: PresentationType, at: str, occurrence: int) -> tuple[Any, Any]:
    """The (slide, shape) whose text contains `at`."""
    matches = [
        (slide, shape)
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame and at in shape.text_frame.text
    ]
    if not matches:
        raise OpError(f"{at!r} does not appear in any box of the presentation")
    if occurrence > len(matches):
        raise OpError(
            f"{at!r} appears {len(matches)} time(s) in the presentation, not {occurrence}"
        )
    return matches[occurrence - 1]


def _remove(shape: Any) -> None:
    shape._element.getparent().remove(shape._element)


def insert_table(prs: PresentationType, p: Any) -> None:
    slide, shape = find_shape(prs, p.at, p.occurrence)
    left, top, width, height = shape.left, shape.top, shape.width, shape.height
    _remove(shape)
    rows = p.rows
    frame = slide.shapes.add_table(len(rows), len(rows[0]), left, top, width, height)
    tbl = frame.table
    tbl.first_row = bool(p.header)
    tbl.horz_banding = p.style == "striped"
    frame._element.graphic.graphicData.tbl.tblPr.find(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}tableStyleId"
    ).text = _TABLE_STYLE[p.style]
    row_pt = height / len(rows) / 12700
    size = Pt(max(8, min(18, row_pt * 0.55)))
    for r, row in enumerate(rows):
        tbl.rows[r].height = int(height / len(rows))
        for c, value in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = text.render(value)
            para = cell.text_frame.paragraphs[0]
            if isinstance(value, int | float) and not isinstance(value, bool):
                para.alignment = PP_ALIGN.RIGHT
            for run in para.runs:
                run.font.size = size
                if p.header and r == 0:
                    run.font.bold = True


def insert_picture(prs: PresentationType, p: Any, png: bytes, size: tuple[int, int]) -> None:
    slide, shape = find_shape(prs, p.at, p.occurrence)
    left, top, box_w, box_h = shape.left, shape.top, shape.width, shape.height
    _remove(shape)
    ratio = size[0] / size[1]
    width = int(p.width_pt * 12700) if p.width_pt else box_w
    height = int(width / ratio)
    if height > box_h or (not p.width_pt and height > box_h):  # fit inside the box
        height = box_h
        width = int(height * ratio)
    slide.shapes.add_picture(
        io.BytesIO(png), left + (box_w - width) // 2, top + (box_h - height) // 2, width, height
    )
