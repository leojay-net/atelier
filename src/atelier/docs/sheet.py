"""Excel workbooks: open, edit, save deterministically, describe."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.workbook.workbook import Workbook

from atelier.docs import package, text
from atelier.ops.registry import OpError

MAX_LISTED = 50
ERRORS = ("#REF!", "#DIV/0!", "#NAME?", "#VALUE!", "#N/A", "#NUM!", "#NULL!")
MAX_CELLS = 2_000_000  # a sheet larger than this is refused rather than read into memory


def open_xlsx(path: Path, *, values: bool = False) -> Workbook:
    """Open a workbook; with `values`, formulas are replaced by their cached results."""
    package.check(path)
    try:
        with path.open("rb") as handle:  # a file object: store paths carry no .xlsx extension
            wb = openpyxl.load_workbook(handle, data_only=values, keep_vba=False)
    except Exception as e:
        raise OpError(f"not a readable workbook: {str(e).splitlines()[0][:160]}") from None
    cells = sum(ws.max_row * ws.max_column for ws in wb.worksheets)
    if cells > MAX_CELLS:
        raise OpError(f"the workbook is too large ({cells:,} cells; limit {MAX_CELLS:,})")
    return wb


class CellText:
    """A string cell seen as a run of text, so the same replace engine works on it."""

    def __init__(self, cell: Any) -> None:
        self.cell = cell

    @property
    def text(self) -> str:
        return str(self.cell.value)


def _set(node: CellText, value: str) -> None:
    node.cell.value = value


def edit(wb: Workbook, matcher: text.Matcher) -> int:
    done = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "s" and isinstance(cell.value, str):
                    done += text.apply([CellText(cell)], matcher, _set)
    return done


def save(wb: Workbook, dst: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.xlsx"
        wb.save(str(raw))
        package.write(raw, dst)


def describe(path: Path) -> dict[str, Any]:
    wb = open_xlsx(path)
    cached = open_xlsx(path, values=True)
    sheets, found, formulas = [], [], 0
    for ws in wb.worksheets:
        n = 0
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    n += 1
                elif cell.data_type == "s" and isinstance(cell.value, str):
                    found += text.placeholders_in(cell.value)
        formulas += n
        sheets.append({"name": ws.title, "rows": ws.max_row, "cols": ws.max_column, "formulas": n})
    errors = []
    for ws in cached.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value in ERRORS:
                    errors.append({"sheet": ws.title, "cell": cell.coordinate, "value": cell.value})
    return {
        "sheets": sheets[:MAX_LISTED],
        "formulas": formulas,
        "errors": errors[:MAX_LISTED],
        "placeholders": sorted(set(found))[:MAX_LISTED],
    }
