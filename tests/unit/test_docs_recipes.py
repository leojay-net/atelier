"""The document recipes: one call each."""

from __future__ import annotations

import shutil
from typing import Any

import pytest
from pptx import Presentation

from atelier.docs import pdfdoc
from atelier.engine.engine import Engine
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

DOCS = FIXTURES / "docs"
needs_soffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="needs LibreOffice")


def put(engine: Engine, name: str) -> str:
    return engine.store.put_file(DOCS / name).handle


def test_redact_and_stamp_in_one_call(engine: Engine) -> None:
    result = engine.run(
        {
            "recipe": "contract_redact_and_stamp",
            "params": {
                "in": put(engine, "secret.pdf"),
                "terms": ["Jane Roe", "4111-2222-3333-4444"],
                "label": "ACME {bates}",
            },
        }
    )
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    out = next(iter(result.outputs.values()))
    text = pdfdoc.all_text(engine.store.path(out.hash))
    assert "Jane" not in text and "4111" not in text and "ACME 000001" in text
    assert result.spec.origin == {"recipe": "contract_redact_and_stamp", "version": 1}


def test_slides_from_an_outline_in_one_call(engine: Engine) -> None:
    items = [
        {"layout": "Title and Content", "title": "Goals", "body": ["Grow", "Ship"]},
        {"layout": "Title Only", "title": "Thanks"},
    ]
    result = engine.run(
        {
            "recipe": "slides_from_outline",
            "params": {"in": put(engine, "deck.pptx"), "items": items},
        }
    )
    prs = Presentation(str(engine.store.path(next(iter(result.outputs.values())).hash)))
    assert [s.shapes.title.text for s in prs.slides][-2:] == ["Goals", "Thanks"]


def test_structured_recipe_params_are_checked() -> None:
    from atelier.recipes import expand

    with pytest.raises(SpecInvalid) as exc:
        expand({"recipe": "slides_from_outline", "params": {"in": "asset:x", "items": {"a": 1}}})
    assert "params.items: {'a': 1} invalid: expected a list" in exc.value.errors[0].line()
    with pytest.raises(SpecInvalid) as exc:
        expand({"recipe": "invoice_from_template", "params": {"in": "asset:x", "data": ["a"]}})
    assert "expected an object" in exc.value.errors[0].line()
    with pytest.raises(SpecInvalid) as exc:
        expand(
            {"recipe": "contract_redact_and_stamp",
             "params": {"in": "asset:x", "terms": ["x" * 70_000]}}
        )  # fmt: skip
    assert "too large" in exc.value.errors[0].line()


def test_the_invoice_recipe_expands_to_fill_convert_export(engine: Engine) -> None:
    spec = engine.validate(
        {
            "recipe": "invoice_from_template",
            "params": {"in": put(engine, "letter.docx"), "data": {"name": "Ada"}},
        }
    )
    assert [s.op.name for s in spec.steps] == ["doc.fill", "doc.convert", "export"]
    assert (
        spec.steps[0].args["data"] == {"name": "Ada"}
        and spec.steps[2].args["preset"] == "pdf_print"
    )


@needs_soffice
def test_an_invoice_from_a_template_is_a_print_ready_pdf(engine: Engine) -> None:
    data: dict[str, Any] = {
        "company": "Acme", "name": "Ada", "order": {"id": "A-1"}, "date": "Monday",
        "total": "$5", "items_table": "-", "logo": "-",
    }  # fmt: skip
    result = engine.run(
        {
            "recipe": "invoice_from_template",
            "params": {"in": put(engine, "letter.docx"), "data": data},
        }
    )
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    assert "Dear Ada," in pdfdoc.all_text(
        engine.store.path(next(iter(result.outputs.values())).hash)
    )


@needs_soffice
def test_a_report_gets_page_numbers_unless_told_not_to(engine: Engine) -> None:
    filled = engine.run(
        {
            "in": put(engine, "letter.docx"),
            "ops": [["doc.fill", {"data": {"company": "A", "name": "B", "order": {"id": "1"},
                                           "date": "d", "total": "t", "items_table": "-",
                                           "logo": "-"}}]],
        }
    )  # fmt: skip
    handle = engine.store.resolve(next(iter(filled.outputs.values())).handle).handle
    numbered = engine.run(
        {"recipe": "report_docx_to_pdf", "params": {"in": handle, "preset": "pdf_screen"}}
    )
    assert "1 / 1" in pdfdoc.all_text(engine.store.path(next(iter(numbered.outputs.values())).hash))
    plain = engine.run(
        {"recipe": "report_docx_to_pdf",
         "params": {"in": handle, "page_numbers": False, "preset": "pdf_screen"}}
    )  # fmt: skip
    assert "1 / 1" not in pdfdoc.all_text(
        engine.store.path(next(iter(plain.outputs.values())).hash)
    )
