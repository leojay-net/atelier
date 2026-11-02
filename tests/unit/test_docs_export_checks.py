"""Exporting documents with presets, and the document checks."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pikepdf
import pytest
from pptx import Presentation
from pptx.util import Inches

from atelier.checks import Status
from atelier.checks.docs import (
    fonts_embedded,
    formula_errors,
    no_placeholders,
    pdf_valid,
    redaction_complete,
    text_overflow,
)
from atelier.docs import pdfio
from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, RunLimits
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

DOCS = FIXTURES / "docs"


def put(engine: Engine, name: str) -> str:
    return engine.store.put_file(DOCS / name).handle


def exported(engine: Engine, handle: str, *ops: Any, checks: str = "default") -> JobResult:
    spec = {"in": handle, "ops": [list(o) for o in ops], "checks": checks}
    return engine.run(spec)


def out_path(engine: Engine, result: JobResult) -> Path:
    return engine.store.path(next(iter(result.outputs.values())).hash)


def failures(result: JobResult) -> list[Any]:
    return [c for cs in result.checks.values() for c in cs if c.status is Status.FAIL]


# -- export: Office ---------------------------------------------------------------------------


def test_office_export_normalizes_and_can_set_a_title(engine: Engine) -> None:
    result = exported(
        engine, put(engine, "letter.docx"),
        ["doc.fill", {"data": {"company": "A", "name": "B", "order": {"id": "1"}, "date": "d",
                               "total": "t", "items_table": "-", "logo": "-"}}],
        ["export", {"preset": "docx_clean", "overrides": {"title": "Order letter"}}],
    )  # fmt: skip
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    rep = next(iter(result.outputs.values()))
    assert rep.summary.endswith("docx_clean v1") and rep.meta["export"]["preset"] == "docx_clean"
    with zipfile.ZipFile(out_path(engine, result)) as z:
        core = z.read("docProps/core.xml").decode()
    assert "Order letter" in core and "2000-01-01T00:00:00Z" in core


def test_a_macro_free_office_file_exports_as_is_every_time(engine: Engine, tmp_path: Path) -> None:
    first = out_path(
        engine, exported(engine, put(engine, "deck.pptx"), ["export", {"preset": "pptx_clean"}])
    )
    fresh = Engine.open(tmp_path / "other", run_limits=RunLimits(wall_seconds=60))
    second = out_path(
        fresh, exported(fresh, put(fresh, "deck.pptx"), ["export", {"preset": "pptx_clean"}])
    )
    assert first.read_bytes() == second.read_bytes()


def test_a_preset_for_another_kind_of_file_says_which_fit(engine: Engine) -> None:
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(
            {"in": put(engine, "letter.docx"), "ops": [["export", {"preset": "pdf_screen"}]]}
        )
    line = exc.value.errors[0].line()
    assert "'pdf_screen' encodes pdf, but the input is docx" in line and "docx_clean" in line
    assert "web_jpeg" not in line and "pdf_print" not in line


# -- export: PDF ------------------------------------------------------------------------------


def test_pdf_screen_compresses_and_linearizes(engine: Engine, tmp_path: Path) -> None:
    from test_docs_pdf import photo_pdf

    src = photo_pdf(tmp_path / "photo.pdf")
    handle = engine.store.put_file(src).handle
    result = exported(engine, handle, ["export", {"preset": "pdf_screen"}])
    out = out_path(engine, result)
    assert out.stat().st_size < src.stat().st_size / 2
    with pikepdf.Pdf.open(out) as pdf:
        assert pdf.is_linearized and "/CreationDate" not in pdf.docinfo
    assert next(iter(result.outputs.values())).meta["images_recompressed"] == 1


def test_pdf_print_wants_every_font_embedded_and_says_so(engine: Engine) -> None:
    result = exported(engine, put(engine, "report.pdf"), ["export", {"preset": "pdf_print"}])
    (fail,) = failures(result)
    assert (
        fail.name == "fonts_embedded"
        and "Helvetica" in fail.message
        and "doc.convert" in (fail.hint or "")
    )
    screen = exported(engine, put(engine, "report.pdf"), ["export", {"preset": "pdf_screen"}])
    assert screen.passed  # the screen preset does not ask for embedded fonts


def test_export_removes_active_content_and_can_flatten_a_form(engine: Engine) -> None:
    result = exported(
        engine, put(engine, "form.pdf"),
        ["pdf.form", {"fields": {"full_name": "Jane Roe"}}],
        ["export", {"preset": "pdf_screen", "overrides": {"flatten_forms": True,
                                                          "title": "Filed"}}],
    )  # fmt: skip
    meta = next(iter(result.outputs.values())).meta
    assert meta["fields"] == [] and meta["title"] == "Filed" and meta["javascript"] is False


# -- the checks -------------------------------------------------------------------------------


def test_unfilled_placeholders_fail_with_a_hint(engine: Engine) -> None:
    result = exported(
        engine, put(engine, "letter.docx"),
        ["doc.fill", {"data": {"name": "Ada"}, "strict": False}],
        ["export", {"preset": "docx_clean"}],
    )  # fmt: skip
    (fail,) = failures(result)
    assert fail.name == "no_placeholders"
    assert (
        "6 placeholder(s) still in the file: "
        "{{company}}, {{date}}, {{items_table}}, {{logo}} and 2 more" in fail.message
    )
    assert "doc.fill" in (fail.hint or "")
    assert not result.passed


def test_the_placeholder_check_applies_to_every_office_kind_and_passes_when_clean(
    engine: Engine,
) -> None:
    ok = exported(engine, put(engine, "report.pdf"), ["export", {"preset": "pdf_screen"}])
    assert all(c.name != "no_placeholders" for cs in ok.checks.values() for c in cs)
    deck = exported(engine, put(engine, "deck.pptx"), ["export", {"preset": "pptx_clean"}])
    assert [c.name for cs in deck.checks.values() for c in cs if c.status is Status.FAIL] == [
        "no_placeholders"
    ]
    book = exported(engine, put(engine, "book.xlsx"), ["export", {"preset": "xlsx_clean"}])
    assert [c.name for cs in book.checks.values() for c in cs if c.status is Status.FAIL] == [
        "no_placeholders"
    ]


def test_a_redaction_that_worked_passes_the_completeness_check(engine: Engine) -> None:
    result = exported(
        engine, put(engine, "secret.pdf"),
        ["pdf.redact", {"terms": ["Jane Roe"]}], ["export", {"preset": "pdf_screen"}],
    )  # fmt: skip
    assert result.passed
    # the export step is a different artifact: the record travels only with the redact step
    redact = exported(engine, put(engine, "secret.pdf"), ["pdf.redact", {"terms": ["Jane Roe"]}])
    names = [c.name for cs in redact.checks.values() for c in cs]
    assert "redaction_complete" in names and redact.passed


def test_check_functions_judge_metadata_only() -> None:
    assert no_placeholders({}) is None and formula_errors({}) is None and pdf_valid({}) is None
    bad = formula_errors(
        {
            "errors": [
                {"sheet": "S", "cell": "B7", "value": "#DIV/0!"},
                {"sheet": "S", "cell": "C1", "value": "#REF!"},
            ]
        }
    )
    assert (
        bad is not None
        and bad.status is Status.FAIL
        and "S!B7 is #DIV/0!, S!C1 is #REF!" in bad.message
    )
    assert formula_errors({"errors": []}).status is Status.PASS  # type: ignore[union-attr]
    leaked = redaction_complete({"redaction": {"leaked": ["Jane"]}})
    assert (
        leaked is not None and leaked.status is Status.FAIL and "still be found" in leaked.message
    )
    assert redaction_complete({"redaction": {"leaked": []}}).status is Status.PASS  # type: ignore[union-attr]
    assert redaction_complete({}) is None
    broken = pdf_valid({"problems": ["xref table is damaged"]})
    assert broken is not None and "xref table is damaged" in broken.message
    loose = fonts_embedded(
        {"fonts": [{"name": "Arial", "embedded": False}, {"name": "Inter", "embedded": True}]}
    )
    assert loose is not None and loose.status is Status.FAIL and "Arial" in loose.message


def test_a_box_that_is_too_small_for_its_text_is_a_warning(engine: Engine, tmp_path: Path) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(0.5))
    box.text_frame.word_wrap = True
    box.text_frame.text = (
        "This sentence is far too long to fit in a box only half an inch tall " * 3
    )
    ok = slide.shapes.add_textbox(Inches(1), Inches(4), Inches(6), Inches(2))
    ok.text_frame.word_wrap = True
    ok.text_frame.text = "Short."
    raw = tmp_path / "over.pptx"
    prs.save(str(raw))
    handle = engine.store.put_file(raw).handle
    result = exported(engine, handle, ["export", {"preset": "pptx_clean"}])
    warn = [c for cs in result.checks.values() for c in cs if c.name == "text_overflow"]
    assert warn and warn[0].status is Status.WARN and "slide 1" in warn[0].message
    assert "shorten the text" in (warn[0].hint or "") and result.passed  # a warning, not a failure
    assert text_overflow({"overflow": []}).status is Status.PASS  # type: ignore[union-attr]
    fine = exported(engine, put(engine, "deck.pptx"), ["export", {"preset": "pptx_clean"}])
    assert all(c.status is not Status.WARN for cs in fine.checks.values() for c in cs)


def test_inspect_media_describes_documents_in_the_words_the_ops_use(engine: Engine) -> None:
    def line(name: str, full: bool = False) -> str:
        return engine.inspect(engine.store.resolve(put(engine, name)), full=full)

    letter = line("letter.docx")
    assert "docx · 5 paragraphs · 1 tables" in letter
    assert "placeholders: {{company}}, {{date}}, {{items_table}}, {{logo}}, {{name}}" in letter
    assert "form fields: full_name, city, agree" in line("form.pdf")
    assert "3 pages · 612x792pt · 173 chars of text" in line("report.pdf")
    assert "sheets: Report (7x2) · 2 formulas" in line("book.xlsx")
    assert "3 slides · layouts: Title Slide" in line("deck.pptx")


def test_document_probe_of_a_broken_file_is_an_error_not_a_crash(
    engine: Engine, tmp_path: Path
) -> None:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("word/document.xml", "<not really>")
    bad = tmp_path / "bad.docx"
    bad.write_bytes(raw.getvalue())
    info = engine.store.put_file(bad)
    assert "error" in engine.probe(info) or engine.probe(info) == {}
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.4\nnot a pdf")
    assert "error" in engine.probe(engine.store.put_file(pdf))
    assert pdfio.PRODUCER == "Atelier"
