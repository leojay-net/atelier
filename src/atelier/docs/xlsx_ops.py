"""Excel ops (docs/03 §3.4): write values, formulas, number formats and column widths."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from openpyxl.styles import Font
from openpyxl.utils.cell import (
    column_index_from_string,
    coordinate_from_string,
    get_column_letter,
    range_boundaries,
)
from openpyxl.utils.exceptions import CellCoordinatesException
from pydantic import Field, field_validator, model_validator

from atelier.docs import sheet
from atelier.docs.doc_ops import describe, doc_op
from atelier.media import MediaType
from atelier.ops.registry import Artifact, OpContext, OpError, Params

MAX_CELLS = 100_000
Scalar = str | int | float | bool | None

# Excel runs formulas the moment a file is opened. These can reach outside the file (another
# workbook, a URL, a program): never written, whoever asks.
_DANGEROUS = re.compile(
    r"\b(WEBSERVICE|FILTERXML|CALL|REGISTER|REGISTER\.ID|EXEC|RTD|HYPERLINK|DDE|SHELL|MSQUERY|"
    r"SQL\.REQUEST|IMPORTDATA|IMPORTXML|IMPORTHTML|IMPORTRANGE)\s*\(",
    re.IGNORECASE,
)


def check_formula(formula: str, where: str) -> None:
    if _DANGEROUS.search(formula):
        raise ValueError(f"{where}: that function can reach outside the file and is not allowed")
    if "|" in formula or "[" in formula or "\\" in formula or "://" in formula:
        raise ValueError(
            f"{where}: a formula may not refer to another file, a URL or a program "
            f"({formula[:40]!r})"
        )


def _cell(ref: str) -> str:
    try:
        coordinate_from_string(ref)
    except CellCoordinatesException:
        raise ValueError(f"{ref!r} is not a cell like B7") from None
    return ref.upper()


def _range(ref: str) -> str:
    try:
        range_boundaries(ref)
    except (ValueError, TypeError):
        raise ValueError(f"{ref!r} is not a cell or range like B7 or B2:B10") from None
    return ref.upper()


class WriteParams(Params):
    sheet: str | None = Field(None, description="sheet name; default the first sheet")
    cells: dict[str, Scalar] = Field(
        default_factory=dict, description="value by cell, e.g. {'B2': 5, 'B6': '=SUM(B2:B5)'}"
    )
    rows: list[list[Scalar]] = Field(
        default_factory=list, description="rows written from `start`, a value per cell"
    )
    start: str = Field("A1", description="top-left cell of `rows`")
    number_formats: dict[str, str] = Field(
        default_factory=dict, description="format by cell or range: {'B2:B9': '#,##0.00'}"
    )
    column_widths: dict[str, float] = Field(
        default_factory=dict, description="width in characters by column letter: {'A': 24}"
    )
    bold: list[str] = Field(default_factory=list, description="cells or ranges to make bold")
    create_sheet: bool = Field(
        False, description="create `sheet` if the workbook has none of that name"
    )

    @field_validator("cells")
    @classmethod
    def _cells(cls, v: dict[str, Scalar]) -> dict[str, Scalar]:
        out = {}
        for ref, value in v.items():
            if isinstance(value, str) and value.startswith("="):
                check_formula(value, ref)
            out[_cell(ref)] = value
        return out

    @field_validator("rows")
    @classmethod
    def _rows(cls, v: list[list[Scalar]]) -> list[list[Scalar]]:
        for r, row in enumerate(v):
            for c, value in enumerate(row):
                if isinstance(value, str) and value.startswith("="):
                    check_formula(value, f"rows[{r}][{c}]")
        return v

    @field_validator("start")
    @classmethod
    def _start(cls, v: str) -> str:
        return _cell(v)

    @field_validator("number_formats")
    @classmethod
    def _formats(cls, v: dict[str, str]) -> dict[str, str]:
        return {_range(k): fmt for k, fmt in v.items()}

    @field_validator("column_widths")
    @classmethod
    def _widths(cls, v: dict[str, float]) -> dict[str, float]:
        for col, width in v.items():
            if not re.fullmatch(r"[A-Za-z]{1,3}", col):
                raise ValueError(f"{col!r} is not a column letter like A")
            if not 1 <= width <= 255:
                raise ValueError(f"width {width} must be between 1 and 255")
        return {k.upper(): w for k, w in v.items()}

    @field_validator("bold")
    @classmethod
    def _bold(cls, v: list[str]) -> list[str]:
        return [_range(r) for r in v]

    @model_validator(mode="after")
    def _something(self) -> WriteParams:
        n = len(self.cells) + sum(len(r) for r in self.rows)
        if n > MAX_CELLS:
            raise ValueError(f"at most {MAX_CELLS:,} cells per step")
        if not (self.cells or self.rows or self.number_formats or self.column_widths or self.bold):
            raise ValueError("nothing to write")
        return self


def _cells_in(ref: str) -> Any:
    bounds = range_boundaries(ref)
    min_col, min_row, max_col, max_row = (int(x or 1) for x in bounds)
    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            yield row, col


@doc_op(
    name="xlsx.write",
    params=WriteParams,
    description="write values, formulas, number formats and widths into an Excel file",
    inputs=(MediaType.XLSX,),
)
def write(ctx: OpContext, inputs: Sequence[Artifact], p: WriteParams) -> Artifact:
    """Put values into cells (`cells`) or a block of rows (`rows` from `start`); a text starting
    with = is a formula (`=SUM(B2:B5)`). Numbers stay numbers; format them with `number_formats`
    ('#,##0.00', '0%', 'yyyy-mm-dd'). Formulas are stored but not calculated here: use
    `xlsx.recalc` to compute them and to find errors like #DIV/0!."""
    src = inputs[0]
    wb = sheet.open_xlsx(src.path)
    if p.sheet is None:
        ws = wb.worksheets[0]
    elif p.sheet in wb.sheetnames:
        ws = wb[p.sheet]
    elif p.create_sheet:
        if len(p.sheet) > 31 or re.search(r"[\[\]:*?/\\]", p.sheet):
            raise OpError("a sheet name has at most 31 characters and none of [ ] : * ? / \\")
        ws = wb.create_sheet(p.sheet)
    else:
        raise OpError(
            f"no sheet named {p.sheet!r}. The workbook has: {', '.join(wb.sheetnames)} "
            "(set create_sheet to add one)"
        )
    for ref, value in p.cells.items():
        ws[ref] = value
    if p.rows:
        col0, row0 = coordinate_from_string(p.start)
        base = column_index_from_string(col0)
        for r, row in enumerate(p.rows):
            for c, value in enumerate(row):
                ws.cell(row=row0 + r, column=base + c, value=value)
    for ref, fmt in p.number_formats.items():
        for row, col in _cells_in(ref):
            ws.cell(row=row, column=col).number_format = fmt
    for ref in p.bold:
        for row, col in _cells_in(ref):
            cell = ws.cell(row=row, column=col)
            cell.font = Font(bold=True, name=cell.font.name, size=cell.font.size)
    for col, width in p.column_widths.items():
        ws.column_dimensions[get_column_letter(column_index_from_string(col))].width = width
    out = ctx.output_path(src.ext)
    sheet.save(wb, out)
    return Artifact(out, src.media, src.ext, describe(out, src.media))


# -- recalc (LibreOffice) ----------------------------------------------------------------------


class RecalcParams(Params):
    pass


@doc_op(
    name="xlsx.recalc",
    params=RecalcParams,
    description="calculate every formula of an Excel file and report errors",
    inputs=(MediaType.XLSX,),
)
def recalc(ctx: OpContext, inputs: Sequence[Artifact], p: RecalcParams) -> Artifact:
    """Open the workbook in LibreOffice, calculate every formula and save it with the results
    stored, so other programs (and `inspect_media`) see numbers, not empty cells. Formulas that
    fail (#DIV/0!, #REF!, #NAME?…) are listed in the result and fail the formula_errors check.
    Formatting is kept, but LibreOffice rewrites the file, so run this last, before `export`."""
    from atelier.docs import convert as libreoffice
    from atelier.docs import package

    src = inputs[0]
    package.check(src.path)
    made = libreoffice.convert(src.path, ".xlsx", "xlsx", ctx.work_dir)
    out = ctx.output_path(src.ext)
    package.write(made, out)
    return Artifact(out, src.media, src.ext, describe(out, src.media))
