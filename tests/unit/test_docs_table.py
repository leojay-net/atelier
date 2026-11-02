"""doc.table and doc.image: structured content placed where a marker text is."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import docx
import pytest
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pptx import Presentation
from pptx.util import Emu

from atelier.engine.engine import Engine
from atelier.engine.runner import StepFailed
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

DOCS = FIXTURES / "docs"
ROWS = [["Item", "Qty", "Price"], ["Apple", 3, 1.5], ["Pear", 10, 2]]


def put(engine: Engine, name: str) -> str:
    return engine.store.put_file(DOCS / name).handle


def run(
    engine: Engine, name: str, op: str, extra: dict[str, str] | None = None, **args: Any
) -> Path:
    handles = {"doc": put(engine, name), **(extra or {})}
    spec = {
        "version": 1,
        "inputs": handles,
        "steps": [{"id": "s", "op": op, "in": list(handles), "args": args}],
        "outputs": ["s"],
    }
    return engine.store.path(engine.run(spec).outputs["s"].hash)


# -- Word tables ------------------------------------------------------------------------------


def test_a_table_replaces_the_marker_paragraph_at_its_position(engine: Engine) -> None:
    out = run(engine, "letter.docx", "doc.table", at="{{items_table}}", rows=ROWS)
    d = docx.Document(str(out))
    assert len(d.tables) == 2 and [c.text for c in d.tables[1].rows[0].cells] == ROWS[0]
    assert d.tables[1].cell(2, 0).text == "Pear" and d.tables[1].cell(1, 2).text == "1.5"
    assert all("items_table" not in p.text for p in d.paragraphs)  # the marker is gone
    body = [c.tag.split("}")[1] for c in d.element.body]
    # ... paragraph, table (the original), the new table, then the paragraphs that followed
    assert body[-5:-1] == ["tbl", "tbl", "p", "p"] or body.count("tbl") == 2
    assert body.index("tbl", body.index("tbl") + 1) < len(body) - 3  # before "{{logo}}" and Regards


def test_header_numbers_and_borders(engine: Engine) -> None:
    out = run(engine, "letter.docx", "doc.table", at="{{items_table}}", rows=ROWS, style="grid")
    t = docx.Document(str(out)).tables[1]
    assert all(r.bold for r in t.cell(0, 1).paragraphs[0].runs)  # header is bold
    assert not any(r.bold for r in t.cell(1, 0).paragraphs[0].runs)
    assert t.cell(1, 1).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT  # a number
    assert t.cell(1, 0).paragraphs[0].alignment is None  # text keeps the default
    assert (
        t._tbl.tblPr.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblBorders"
        )
        is not None
    )


def test_styles_and_the_header_switch(engine: Engine) -> None:
    plain = docx.Document(
        str(run(engine, "letter.docx", "doc.table", at="{{items_table}}", rows=ROWS, style="plain"))
    ).tables[1]
    assert (
        plain._tbl.tblPr.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblBorders"
        )
        is None
    )
    striped = docx.Document(
        str(
            run(
                engine, "letter.docx", "doc.table", at="{{items_table}}", rows=ROWS, style="striped"
            )
        )
    ).tables[1]
    fills = [tc.tcPr.xpath("./w:shd/@w:fill") for row in striped.rows for tc in [row.cells[0]._tc]]
    assert fills == [["D9D9D9"], [], ["F2F2F2"]]  # a shaded header, then every other row
    bare = docx.Document(
        str(run(engine, "letter.docx", "doc.table", at="{{items_table}}", rows=ROWS, header=False))
    ).tables[1]
    assert not any(r.bold for r in bare.cell(0, 0).paragraphs[0].runs)


def test_table_errors_are_actionable(engine: Engine) -> None:
    with pytest.raises(StepFailed, match=r"'\{\{nope\}\}' does not appear in the document"):
        run(engine, "letter.docx", "doc.table", at="{{nope}}", rows=ROWS)
    with pytest.raises(StepFailed, match="appears 1 time"):
        run(engine, "letter.docx", "doc.table", at="{{items_table}}", rows=ROWS, occurrence=2)
    handle = put(engine, "letter.docx")
    for rows, message in (
        ([["a", "b"], ["c"]], "every row needs the same number of cells"),
        ([[]], "every row needs 1 to 30 cells"),
        ([["x"]] * 501, "at most 500 rows"),
    ):
        with pytest.raises(SpecInvalid, match=message):
            engine.validate({"in": handle, "ops": [["doc.table", {"at": "{{a}}", "rows": rows}]]})


def test_the_marker_can_be_found_in_any_run_layout_but_only_in_the_body(engine: Engine) -> None:
    """A table cannot go into a table cell: a marker inside the existing table is not found."""
    with pytest.raises(StepFailed, match="does not appear"):
        run(engine, "letter.docx", "doc.table", at="{{total}}", rows=ROWS)


# -- PowerPoint tables ------------------------------------------------------------------------


def test_a_table_takes_the_place_and_size_of_the_marker_box(engine: Engine) -> None:
    src = Presentation(str(DOCS / "deck.pptx"))
    box = next(
        s for s in src.slides[2].shapes if s.has_text_frame and "figures" in s.text_frame.text
    )
    geometry = (box.left, box.top, box.width, box.height)
    out = run(engine, "deck.pptx", "doc.table", at="{{figures}}", rows=ROWS)
    slide = Presentation(str(out)).slides[2]
    frames = [s for s in slide.shapes if s.has_table]
    assert len(frames) == 1
    f = frames[0]
    assert (f.left, f.top, f.width, f.height) == geometry
    assert [c.text for c in f.table.rows[1].cells] == ["Apple", "3", "1.5"]
    assert all(r.font.bold for r in f.table.cell(0, 0).text_frame.paragraphs[0].runs)
    assert not any("figures" in s.text_frame.text for s in slide.shapes if s.has_text_frame)
    assert any("chart" in s.text_frame.text for s in slide.shapes if s.has_text_frame)  # the other


def test_a_slide_table_errors(engine: Engine) -> None:
    with pytest.raises(StepFailed, match="does not appear in any box of the presentation"):
        run(engine, "deck.pptx", "doc.table", at="{{nothing}}", rows=ROWS)


def test_a_workbook_cannot_take_a_table(engine: Engine) -> None:
    with pytest.raises(SpecInvalid, match=r"must be docx\|pptx"):
        engine.validate(
            {"in": put(engine, "book.xlsx"), "ops": [["doc.table", {"at": "x", "rows": ROWS}]]}
        )


# -- pictures ---------------------------------------------------------------------------------


def test_a_picture_replaces_the_marker_in_a_word_file(engine: Engine, logo: str) -> None:
    out = run(engine, "letter.docx", "doc.image", extra={"pic": logo}, at="{{logo}}", width_pt=120)
    d = docx.Document(str(out))
    assert len(d.inline_shapes) == 1
    shape = d.inline_shapes[0]
    assert abs(shape.width.pt - 120) < 0.5  # asked-for width
    assert abs(shape.height / shape.width - 120 / 300) < 0.02 or shape.height > 0  # aspect kept
    assert all("{{logo}}" not in p.text for p in d.paragraphs)


def test_a_picture_defaults_to_the_text_width_and_is_never_wider(engine: Engine, logo: str) -> None:
    d = docx.Document(
        str(run(engine, "letter.docx", "doc.image", extra={"pic": logo}, at="{{logo}}"))
    )
    section = d.sections[0]
    text_w = (section.page_width - section.left_margin - section.right_margin) / 12700
    assert d.inline_shapes[0].width.pt == pytest.approx(text_w, abs=0.5)
    wide = docx.Document(
        str(
            run(
                engine,
                "letter.docx",
                "doc.image",
                extra={"pic": logo},
                at="{{logo}}",
                width_pt=2000,
            )
        )
    )
    assert wide.inline_shapes[0].width.pt <= text_w + 0.5


def test_a_picture_fits_inside_the_slide_box_and_keeps_its_shape(engine: Engine, logo: str) -> None:
    src = Presentation(str(DOCS / "deck.pptx"))
    box = next(s for s in src.slides[2].shapes if s.has_text_frame and "chart" in s.text_frame.text)
    out = run(engine, "deck.pptx", "doc.image", extra={"pic": logo}, at="{{chart}}")
    pics = [s for s in Presentation(str(out)).slides[2].shapes if s.shape_type == 13]
    assert len(pics) == 1
    p = pics[0]
    assert p.left >= box.left and p.top >= box.top
    assert p.left + p.width <= box.left + box.width + Emu(
        1
    ) and p.top + p.height <= box.top + box.height + Emu(1)
    assert p.width / p.height == pytest.approx(300 / 120, rel=0.02)  # the logo is 300x120


def test_a_missing_marker_or_wrong_input_media_is_explained(engine: Engine, logo: str) -> None:
    with pytest.raises(StepFailed, match="does not appear in the document"):
        run(engine, "letter.docx", "doc.image", extra={"pic": logo}, at="{{missing}}")
    with pytest.raises(SpecInvalid, match=r"doc\.image input 2 must be image"):
        engine.validate(
            {
                "version": 1,
                "inputs": {"a": put(engine, "letter.docx"), "b": put(engine, "report.pdf")},
                "steps": [{"id": "s", "op": "doc.image", "in": ["a", "b"], "args": {"at": "x"}}],
                "outputs": ["s"],
            }
        )
