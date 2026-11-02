"""xlsx.write and pptx.slide."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import openpyxl
import pytest
from pptx import Presentation

from atelier.engine.engine import Engine
from atelier.engine.runner import StepFailed
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

DOCS = FIXTURES / "docs"


def put(engine: Engine, name: str) -> str:
    return engine.store.put_file(DOCS / name).handle


def step(engine: Engine, name: str, op: str, **args: Any) -> Path:
    spec = {"in": put(engine, name), "ops": [[op, args]]}
    return engine.store.path(next(iter(engine.run(spec).outputs.values())).hash)


def workbook(path: Path) -> Any:
    with path.open("rb") as handle:
        return openpyxl.load_workbook(handle)


# -- xlsx.write -------------------------------------------------------------------------------


def test_cells_rows_formulas_formats_widths_and_bold(engine: Engine) -> None:
    out = step(
        engine, "book.xlsx", "xlsx.write",
        cells={"D1": "Notes", "D2": "=SUM(B3:B5)*2"},
        rows=[["Region", "Sales"], ["North", 1200.5], ["South", 800]],
        start="F1",
        number_formats={"G2:G3": "#,##0.00", "D2": "0%"},
        column_widths={"a": 30, "F": 12.5},
        bold=["F1:G1"],
    )  # fmt: skip
    ws = workbook(out).active
    assert ws["D1"].value == "Notes" and ws["D2"].value == "=SUM(B3:B5)*2"
    assert [ws.cell(row=r, column=6).value for r in (1, 2, 3)] == ["Region", "North", "South"]
    assert ws["G2"].value == 1200.5 and ws["G2"].number_format == "#,##0.00"
    assert ws["D2"].number_format == "0%"
    assert ws["F1"].font.bold and ws["G1"].font.bold and not ws["F2"].font.bold
    assert ws.column_dimensions["A"].width == 30 and ws.column_dimensions["F"].width == 12.5
    assert ws["B6"].value == "=SUM(B3:B5)"  # existing formulas are kept


def test_numbers_stay_numbers_and_a_text_looking_like_one_stays_text(engine: Engine) -> None:
    ws = workbook(
        step(engine, "book.xlsx", "xlsx.write", cells={"E1": 3, "E2": "3", "E3": True, "E4": None})
    ).active
    assert ws["E1"].value == 3 and ws["E1"].data_type == "n"
    assert ws["E2"].value == "3" and ws["E2"].data_type == "s"
    assert ws["E3"].value is True


def test_another_sheet_by_name_or_created(engine: Engine) -> None:
    with pytest.raises(StepFailed, match=r"no sheet named 'Summary'. The workbook has: Report"):
        step(engine, "book.xlsx", "xlsx.write", sheet="Summary", cells={"A1": "x"})
    wb = workbook(
        step(
            engine, "book.xlsx", "xlsx.write", sheet="Summary", cells={"A1": "x"}, create_sheet=True
        )
    )
    assert wb.sheetnames == ["Report", "Summary"] and wb["Summary"]["A1"].value == "x"
    with pytest.raises(StepFailed, match="at most 31 characters"):
        step(engine, "book.xlsx", "xlsx.write", sheet="a/b", cells={"A1": "x"}, create_sheet=True)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"cells": {"1A": 1}}, "is not a cell like B7"),
        ({"cells": {"A1": '=WEBSERVICE("http://x")'}}, "can reach outside the file"),
        ({"cells": {"A1": "=cmd|'/c calc'!A0"}}, "may not refer to another file"),
        ({"cells": {"A1": "='[other.xlsx]S'!A1"}}, "may not refer to another file"),
        ({"rows": [['=HYPERLINK("x")']]}, "can reach outside the file"),
        ({"number_formats": {"A1:": "0"}}, "is not a cell or range"),
        ({"column_widths": {"1": 5}}, "is not a column letter"),
        ({"column_widths": {"A": 0}}, "between 1 and 255"),
        ({}, "nothing to write"),
    ],
)
def test_bad_writes_are_refused_at_validation(
    engine: Engine, args: dict[str, Any], message: str
) -> None:
    with pytest.raises(SpecInvalid, match=message):
        engine.validate({"in": put(engine, "book.xlsx"), "ops": [["xlsx.write", args]]})


def test_a_word_file_is_not_a_workbook(engine: Engine) -> None:
    with pytest.raises(SpecInvalid, match="must be xlsx"):
        engine.validate(
            {"in": put(engine, "letter.docx"), "ops": [["xlsx.write", {"cells": {"A1": 1}}]]}
        )


# -- pptx.slide -------------------------------------------------------------------------------


def titles(path: Path) -> list[str]:
    prs = Presentation(str(path))
    return [s.shapes.title.text if s.shapes.title is not None else "" for s in prs.slides]


def test_add_slides_at_the_end_or_before_one(engine: Engine) -> None:
    items = [
        {"layout": "Title and Content", "title": "Risks", "body": ["Cost", "Time"]},
        {"layout": "Title Only", "title": "Thanks"},
    ]
    out = step(engine, "deck.pptx", "pptx.slide", action="add", items=items)
    assert titles(out) == ["Welcome {{name}}", "Agenda", "Numbers", "Risks", "Thanks"]
    body = Presentation(str(out)).slides[3].placeholders[1].text_frame
    assert [p.text for p in body.paragraphs] == ["Cost", "Time"]
    early = step(engine, "deck.pptx", "pptx.slide", action="add", items=items[:1], index=2)
    assert titles(early) == ["Welcome {{name}}", "Risks", "Agenda", "Numbers"]


def test_duplicate_keeps_the_content_and_puts_the_copy_next_to_it(engine: Engine) -> None:
    out = step(engine, "deck.pptx", "pptx.slide", action="duplicate", index=2, copies=2)
    assert titles(out) == ["Welcome {{name}}", "Agenda", "Agenda", "Agenda", "Numbers"]
    slide = Presentation(str(out)).slides[2]
    assert slide.placeholders[1].text_frame.text == "Revenue: {{revenue}}"


def test_delete_and_move(engine: Engine) -> None:
    gone = step(engine, "deck.pptx", "pptx.slide", action="delete", index=2)
    assert titles(gone) == ["Welcome {{name}}", "Numbers"]
    moved = step(engine, "deck.pptx", "pptx.slide", action="move", index=3, to=1)
    assert titles(moved) == ["Numbers", "Welcome {{name}}", "Agenda"]
    end = step(engine, "deck.pptx", "pptx.slide", action="move", index=1, to=3)
    assert titles(end) == ["Agenda", "Numbers", "Welcome {{name}}"]


def test_slide_errors_say_what_exists(engine: Engine) -> None:
    with pytest.raises(StepFailed, match=r"slide 9 is out of range: the deck has 3 slide"):
        step(engine, "deck.pptx", "pptx.slide", action="delete", index=9)
    with pytest.raises(StepFailed, match=r"no layout named 'Nope'. The deck has: Title Slide"):
        step(engine, "deck.pptx", "pptx.slide", action="add", items=[{"layout": "Nope"}])
    with pytest.raises(StepFailed, match="has no body box"):
        step(
            engine, "deck.pptx", "pptx.slide",
            action="add", items=[{"layout": "Title Only", "body": ["x"]}],
        )  # fmt: skip
    handle = put(engine, "deck.pptx")
    for args, message in (
        ({"action": "add"}, "add needs items"),
        ({"action": "move", "index": 1}, "move needs to"),
        ({"action": "delete"}, "delete needs index"),
    ):
        with pytest.raises(SpecInvalid, match=message):
            engine.validate({"in": handle, "ops": [["pptx.slide", args]]})


def test_a_deck_is_edited_then_filled_in_one_pipeline(engine: Engine) -> None:
    spec = {
        "in": put(engine, "deck.pptx"),
        "ops": [
            ["pptx.slide", {"action": "add", "items": [{"title": "Hello {{who}}"}]}],
            [
                "doc.fill",
                {"data": {"name": "A", "revenue": "1", "figures": "f", "chart": "c", "who": "W"}},
            ],
        ],
    }
    out = engine.store.path(next(iter(engine.run(spec).outputs.values())).hash)
    assert titles(out)[-1] == "Hello W"
