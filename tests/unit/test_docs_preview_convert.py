"""Document previews (host), and LibreOffice conversion and recalculation (reference image)."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any

import openpyxl
import pikepdf
import pytest

from atelier.agent.budget import Budget, BudgetLimits
from atelier.agent.tools import Toolbox
from atelier.checks import Status
from atelier.docs import pdfdoc
from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, RunLimits, StepFailed
from conftest import FIXTURES

DOCS = FIXTURES / "docs"
needs_soffice = pytest.mark.skipif(
    shutil.which("soffice") is None, reason="LibreOffice is not installed (reference image only)"
)


def put(engine: Engine, name: str) -> str:
    return engine.store.put_file(DOCS / name).handle


def rep(result: JobResult, step: str | None = None) -> Any:
    return result.outputs[step or next(iter(result.outputs))]


# -- previews: PDF (host) ---------------------------------------------------------------------


def test_a_contact_sheet_of_pages_is_one_labelled_image(engine: Engine) -> None:
    result = engine.preview("sheet", [put(engine, "report.pdf")], {"count": 3})
    out = rep(result, "preview")
    assert out.media.value == "image" and out.summary.startswith("preview ")
    im = engine.store.path(out.hash)
    from PIL import Image

    with Image.open(im) as sheet:
        assert sheet.width > 256 and sheet.height > 200  # several pages in one grid


def test_a_thumb_is_one_larger_page_and_page_numbers_are_checked(engine: Engine) -> None:
    from PIL import Image

    one = engine.preview("thumb", [put(engine, "report.pdf")], {"page": 2})
    with Image.open(engine.store.path(rep(one, "preview").hash)) as page:
        assert page.width > 500 and page.height > page.width  # a portrait page at reading size
    with pytest.raises(StepFailed, match=r"page 9 does not exist: the document has 3 page"):
        engine.preview("thumb", [put(engine, "report.pdf")], {"page": 9})


def test_previews_of_documents_are_cached_and_other_kinds_are_refused(engine: Engine) -> None:
    engine.preview("sheet", [put(engine, "report.pdf")], {"count": 2})
    assert engine.preview("sheet", [put(engine, "report.pdf")], {"count": 2}).reused == 1
    with pytest.raises(ValueError, match=r"thumb \(one page\) or sheet"):
        engine.preview("crop", [put(engine, "report.pdf")], {})


def test_the_agents_preview_tool_shows_pages(engine: Engine) -> None:
    box = Toolbox(engine, Budget(BudgetLimits(max_images=3)))
    sheet = box.dispatch(
        "preview", {"assets": [put(engine, "report.pdf")], "kind": "contact_sheet"}
    )
    assert not sheet.is_error and isinstance(sheet.content, list) and box.budget.images == 1
    page = box.dispatch(
        "preview", {"assets": [put(engine, "report.pdf")], "kind": "thumb", "page": 3}
    )
    assert not page.is_error and box.budget.images == 2


@pytest.mark.skipif(
    shutil.which("soffice") is not None, reason="only meaningful without LibreOffice"
)
def test_an_office_preview_without_libreoffice_says_what_is_missing(engine: Engine) -> None:
    with pytest.raises(StepFailed, match="LibreOffice is not installed here"):
        engine.preview("sheet", [put(engine, "letter.docx")], {})


# -- conversion (reference image) -------------------------------------------------------------


def filled_letter(engine: Engine) -> str:
    result = engine.run(
        {
            "in": put(engine, "letter.docx"),
            "ops": [
                ["doc.fill", {"data": {"company": "Acme", "name": "Ada", "order": {"id": "A-1"},
                                       "date": "Monday", "total": "$5", "items_table": "-",
                                       "logo": "-"}}],
            ],
        }
    )  # fmt: skip
    return engine.store.resolve(rep(result).handle).handle


@needs_soffice
def test_a_word_file_becomes_a_pdf_with_embedded_fonts(engine: Engine) -> None:
    handle = filled_letter(engine)
    result = engine.run(
        {"in": handle, "ops": [["doc.convert", {}], ["export", {"preset": "pdf_print"}]]}
    )
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]  # fonts embedded
    out = rep(result)
    assert out.meta["pages"] >= 1 and "Dear Ada," in pdfdoc.all_text(engine.store.path(out.hash))
    with pikepdf.Pdf.open(engine.store.path(out.hash)) as pdf:
        assert "/CreationDate" not in pdf.docinfo


@needs_soffice
def test_conversion_is_byte_identical_on_a_fresh_engine(engine: Engine, tmp_path: Path) -> None:
    def make(e: Engine) -> bytes:
        handle = e.store.put_file(DOCS / "deck.pptx").handle
        r = e.run({"in": handle, "ops": [["doc.convert", {}]], "checks": "none"})
        return e.store.path(rep(r).hash).read_bytes()

    first = make(engine)
    fresh = Engine.open(tmp_path / "other", run_limits=RunLimits(wall_seconds=120))
    assert make(fresh) == first


@needs_soffice
def test_a_deck_and_a_workbook_convert_too(engine: Engine) -> None:
    deck = engine.run(
        {"in": put(engine, "deck.pptx"), "ops": [["doc.convert", {}]], "checks": "none"}
    )
    assert rep(deck).meta["pages"] == 3
    book = engine.run(
        {"in": put(engine, "book.xlsx"), "ops": [["doc.convert", {}]], "checks": "none"}
    )
    assert rep(book).meta["pages"] >= 1


@needs_soffice
def test_an_office_preview_prints_it_first(engine: Engine) -> None:
    result = engine.preview("sheet", [put(engine, "deck.pptx")], {"count": 3})
    assert rep(result, "preview").media.value == "image"
    assert len(result.steps) == 2  # printed, then previewed


@needs_soffice
def test_conversion_refuses_what_it_should_not_fetch(engine: Engine, tmp_path: Path) -> None:
    ns = "http://schemas.openxmlformats.org"
    entries = {}
    with zipfile.ZipFile(DOCS / "letter.docx") as z:
        entries = {n: z.read(n) for n in z.namelist()}
    entries["word/_rels/document.xml.rels"] = entries["word/_rels/document.xml.rels"].replace(
        b"</Relationships>",
        (
            f'<Relationship Id="rX" Type="{ns}/officeDocument/2006/relationships/image" '
            'Target="http://169.254.169.254/latest/meta-data" TargetMode="External"/>'
            "</Relationships>"
        ).encode(),
    )
    bad = tmp_path / "linked.docx"
    with zipfile.ZipFile(bad, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    handle = engine.store.put_file(bad).handle
    with pytest.raises(StepFailed, match="links to content outside the file"):
        engine.run({"in": handle, "ops": [["doc.convert", {}]], "checks": "none"})


# -- recalculation (reference image) ----------------------------------------------------------


def cached(path: Path) -> Any:
    with path.open("rb") as handle:
        return openpyxl.load_workbook(handle, data_only=True).active


@needs_soffice
def test_recalc_stores_results_and_reports_errors(engine: Engine) -> None:
    result = engine.run({"in": put(engine, "book.xlsx"), "ops": [["xlsx.recalc", {}]]})
    ws = cached(engine.store.path(rep(result).hash))
    assert ws["B6"].value == 60  # the SUM, calculated
    assert ws["B7"].value == "#DIV/0!"
    assert rep(result).meta["errors"] == [{"sheet": "Report", "cell": "B7", "value": "#DIV/0!"}]
    fail = [c for cs in result.checks.values() for c in cs if c.status is Status.FAIL]
    assert [c.name for c in fail] == ["formula_errors", "no_placeholders"] or "formula_errors" in [
        c.name for c in fail
    ]
    assert "Report!B7 is #DIV/0!" in next(c for c in fail if c.name == "formula_errors").message


@needs_soffice
def test_recalc_keeps_formulas_and_is_repeatable(engine: Engine, tmp_path: Path) -> None:
    def make(e: Engine) -> Path:
        handle = e.store.put_file(DOCS / "book.xlsx").handle
        r = e.run({"in": handle, "ops": [["xlsx.recalc", {}]], "checks": "none"})
        return e.store.path(rep(r).hash)

    first = make(engine)
    with first.open("rb") as h:
        assert openpyxl.load_workbook(h).active["B6"].value == "=SUM(B3:B5)"
    fresh = Engine.open(tmp_path / "other", run_limits=RunLimits(wall_seconds=120))
    assert make(fresh).read_bytes() == first.read_bytes()


@needs_soffice
def test_a_workbook_written_then_recalculated_in_one_pipeline_is_clean(engine: Engine) -> None:
    result = engine.run(
        {
            "in": put(engine, "book.xlsx"),
            "ops": [
                ["doc.fill", {"data": {"client": "Acme"}}],
                ["xlsx.write", {"cells": {"B7": "=B6/2", "C1": "Half"}}],
                ["xlsx.recalc", {}],
                ["export", {"preset": "xlsx_clean"}],
            ],
        }
    )
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    assert cached(engine.store.path(rep(result).hash))["B7"].value == 30
