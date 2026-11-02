"""Ops that work on Word, PowerPoint and Excel files alike (docs/03 §3.4).

`doc.fill` and `doc.replace` edit text without disturbing formatting; `doc.table` and
`doc.image` put structured content where the text says. Every op opens the file through the
safe readers in `docs`, and every result is a deterministic package (sorted entries, fixed
timestamps and properties), so equal input and equal args give equal bytes.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from atelier.docs import sheet, slides, text, word
from atelier.media import MediaType
from atelier.ops.registry import (
    SAME,
    AnyOf,
    Artifact,
    Determinism,
    OpContext,
    OpError,
    Params,
    op,
)

DOC_MEDIA = frozenset({MediaType.DOCX, MediaType.PPTX, MediaType.XLSX})
RESOURCES = ("office",)
MAX_PAIRS = 200


def doc_summary(result: Artifact) -> str:
    m = result.meta
    parts = [result.media.value]
    if result.media is MediaType.DOCX:
        parts += [f"{m['paragraphs']} paragraphs", f"{m['tables']} tables"]
    elif result.media is MediaType.PPTX:
        parts += [f"{m['slides']} slides"]
    else:
        parts += [f"{len(m['sheets'])} sheets", f"{m['formulas']} formulas"]
    if m.get("edits"):
        parts.append(f"{m['edits']} edits")
    if m.get("placeholders"):
        parts.append(f"{len(m['placeholders'])} unfilled placeholders")
    return " · ".join(parts)


def describe(path: Path, media: MediaType) -> dict[str, Any]:
    if media is MediaType.DOCX:
        return word.describe(path)
    if media is MediaType.PPTX:
        return slides.describe(path)
    return sheet.describe(path)


def edit_text(ctx: OpContext, src: Artifact, matcher: text.Matcher) -> Artifact:
    """Apply a text edit to a Word, PowerPoint or Excel file and save the result."""
    out = ctx.output_path(src.ext)
    if src.media is MediaType.DOCX:
        doc = word.open_docx(src.path)
        n = word.edit(doc, matcher)
        word.save(doc, out)
    elif src.media is MediaType.PPTX:
        prs = slides.open_pptx(src.path)
        n = slides.edit(prs, matcher)
        slides.save(prs, out)
    else:
        wb = sheet.open_xlsx(src.path)
        n = sheet.edit(wb, matcher)
        sheet.save(wb, out)
    return Artifact(out, src.media, src.ext, {**describe(out, src.media), "edits": n})


def doc_op(
    *,
    name: str,
    params: type[Params],
    description: str,
    inputs: Sequence[Any] = (AnyOf(DOC_MEDIA),),
) -> Callable[[Callable[..., Artifact]], Callable[..., Artifact]]:
    return op(
        name=name,
        version="1.0.0",
        inputs=list(inputs),
        output=SAME,
        determinism=Determinism.BIT_EXACT,
        params=params,
        summary=doc_summary,
        description=description,
        resources=RESOURCES,
    )


# -- fill --------------------------------------------------------------------------------------


def _plain(value: Any, path: str = "data") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _plain(v, f"{path}.{k}")
    elif not isinstance(value, str | int | float | bool | type(None)):
        raise ValueError(
            f"{path} must be text, a number, true/false or an object of those, not a list "
            "(put rows in with doc.table)"
        )


class FillParams(Params):
    data: dict[str, Any] = Field(
        min_length=1, description="values by placeholder name; {{a.b}} reads data.a.b"
    )
    strict: bool = Field(
        True, description="fail if the file has a placeholder with no value, or a value unused"
    )

    @field_validator("data")
    @classmethod
    def _scalars(cls, v: dict[str, Any]) -> dict[str, Any]:
        _plain(v)
        return v


@doc_op(
    name="doc.fill",
    params=FillParams,
    description="fill {{placeholders}} in a Word, PowerPoint or Excel file, keeping formatting",
)
def fill(ctx: OpContext, inputs: Sequence[Artifact], p: FillParams) -> Artifact:
    """Replace every `{{name}}` in the body, tables, headers, footers, slides and cells with
    the value of `name` from `data` (`{{customer.city}}` reads `data.customer.city`). Text is
    replaced inside the run where the placeholder starts, so its font, size and color stay,
    even when Word stored the placeholder in several pieces. With `strict` (the default) a
    placeholder without a value, or a value that matches no placeholder, is an error that lists
    what the file does contain."""
    src = inputs[0]
    present = set(describe_placeholders(src))
    missing: set[str] = set()
    used: set[str] = set()
    result = edit_text(ctx, src, text.fill_matcher(p.data, missing, used))
    if p.strict:
        unused = sorted(set(p.data) - used)
        if missing or unused:
            found = sorted(present) or ["(none)"]
            bits = []
            if missing:
                bits.append(f"no value for {', '.join('{{' + m + '}}' for m in sorted(missing))}")
            if unused:
                bits.append(f"no placeholder for {', '.join(unused)}")
            raise OpError(f"{'; '.join(bits)}. The file has: {', '.join(found)}")
    return result


def describe_placeholders(src: Artifact) -> list[str]:
    """Every placeholder name in the file (for error messages)."""
    found: list[str] = []
    if src.media is MediaType.DOCX:
        for t in word.paragraph_texts(word.open_docx(src.path)):
            found += text.placeholders_in(t)
    elif src.media is MediaType.PPTX:
        for lines in slides.slide_texts(slides.open_pptx(src.path)):
            for line in lines:
                found += text.placeholders_in(line)
    else:
        found += list(sheet.describe(src.path)["placeholders"])
    return found


# -- replace -----------------------------------------------------------------------------------


class ReplaceParams(Params):
    replacements: dict[str, str] = Field(
        min_length=1, max_length=MAX_PAIRS, description="text to find → text to put instead"
    )
    match_case: bool = Field(False, description="case-sensitive search")
    whole_word: bool = Field(False, description="only match whole words")
    require_match: bool = Field(True, description="fail if something to find is not there")

    @field_validator("replacements")
    @classmethod
    def _nonempty(cls, v: dict[str, str]) -> dict[str, str]:
        if any(not k for k in v):
            raise ValueError("a text to find cannot be empty")
        return v


@doc_op(
    name="doc.replace",
    params=ReplaceParams,
    description="find and replace text in a Word, PowerPoint or Excel file, keeping formatting",
)
def replace(ctx: OpContext, inputs: Sequence[Artifact], p: ReplaceParams) -> Artifact:
    """Replace each key of `replacements` by its value everywhere in the file. Matches may span
    formatting changes; the replacement takes the formatting of where the match started. Use
    `doc.fill` for `{{placeholders}}`."""
    counts = dict.fromkeys(p.replacements, 0)
    matcher = text.replace_matcher(
        list(p.replacements.items()),
        match_case=p.match_case,
        whole_word=p.whole_word,
        counts=counts,
    )
    result = edit_text(ctx, inputs[0], matcher)
    absent = [k for k, n in counts.items() if n == 0]
    if p.require_match and absent:
        raise OpError(
            f"not found in the file: {', '.join(repr(a) for a in absent)}"
            + (" (the search ignores case; try whole_word=false)" if p.whole_word else "")
        )
    return result


# -- table -------------------------------------------------------------------------------------

MAX_ROWS = 500
MAX_COLS = 30
TableStyle = Literal["plain", "grid", "striped"]
Cell = str | int | float | bool | None


class TableParams(Params):
    at: str = Field(
        min_length=1, description="text of the paragraph or box the table replaces, e.g. {{items}}"
    )
    rows: list[list[Cell]] = Field(min_length=1, description="rows of cells; the first is a header")
    header: bool = Field(True, description="make the first row a bold header")
    style: TableStyle = Field("grid", description="plain | grid (borders) | striped (shaded rows)")
    occurrence: int = Field(1, ge=1, description="which match of `at`, if it appears several times")

    @field_validator("rows")
    @classmethod
    def _rectangular(cls, v: list[list[Cell]]) -> list[list[Cell]]:
        if len(v) > MAX_ROWS:
            raise ValueError(f"at most {MAX_ROWS} rows")
        widths = {len(r) for r in v}
        if 0 in widths or max(widths) > MAX_COLS:
            raise ValueError(f"every row needs 1 to {MAX_COLS} cells")
        if len(widths) > 1:
            raise ValueError(f"every row needs the same number of cells (got {sorted(widths)})")
        return v


def _cell_text(value: Cell) -> str:
    return text.render(value)


def _anchor(matches: list[Any], at: str, n: int, where: str) -> Any:
    if not matches:
        raise OpError(f"{at!r} does not appear in the {where}; it must be text the file contains")
    if n > len(matches):
        raise OpError(f"{at!r} appears {len(matches)} time(s) in the {where}, not {n}")
    return matches[n - 1]


@doc_op(
    name="doc.table",
    params=TableParams,
    description="put a table where a marker text is, in a Word or PowerPoint file",
    inputs=(AnyOf(frozenset({MediaType.DOCX, MediaType.PPTX})),),
)
def table(ctx: OpContext, inputs: Sequence[Artifact], p: TableParams) -> Artifact:
    """Replace the paragraph (Word) or text box (PowerPoint) whose text contains `at` with a
    table of `rows`. Numbers are right-aligned; with `header` the first row is bold. Put a
    marker like {{items_table}} in the template where the table goes. Use `doc.fill` for single
    values."""
    src = inputs[0]
    out = ctx.output_path(src.ext)
    if src.media is MediaType.DOCX:
        doc = word.open_docx(src.path)
        word.insert_table(doc, p)
        word.save(doc, out)
    else:
        prs = slides.open_pptx(src.path)
        slides.insert_table(prs, p)
        slides.save(prs, out)
    return Artifact(out, src.media, src.ext, describe(out, src.media))


# -- image -------------------------------------------------------------------------------------


class PictureParams(Params):
    at: str = Field(
        min_length=1, description="text of the paragraph or box the picture replaces, e.g. {{logo}}"
    )
    width_pt: float | None = Field(
        None, gt=10, le=2000, description="picture width in points; omit to fill the box/text width"
    )
    occurrence: int = Field(1, ge=1, description="which match of `at`, if it appears several times")


@doc_op(
    name="doc.image",
    params=PictureParams,
    description="put a picture where a marker text is, in a Word or PowerPoint file",
    inputs=(AnyOf(frozenset({MediaType.DOCX, MediaType.PPTX})), MediaType.IMAGE),
)
def picture(ctx: OpContext, inputs: Sequence[Artifact], p: PictureParams) -> Artifact:
    """Replace the paragraph (Word) or box (PowerPoint) whose text contains `at` with the
    second input, an image. The picture keeps its shape: in PowerPoint it is fitted inside the
    box that held the marker, in Word it is `width_pt` wide (default: the page's text width)."""
    from atelier.ops.image._io import load

    src, image = inputs
    im = load(image)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = ctx.output_path(src.ext)
    if src.media is MediaType.DOCX:
        doc = word.open_docx(src.path)
        word.insert_picture(doc, p, buf.getvalue(), im.size)
        word.save(doc, out)
    else:
        prs = slides.open_pptx(src.path)
        slides.insert_picture(prs, p, buf.getvalue(), im.size)
        slides.save(prs, out)
    return Artifact(out, src.media, src.ext, describe(out, src.media))
