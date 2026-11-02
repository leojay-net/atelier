"""Document checks (docs/06 §6.1). They judge the metadata a document op recorded.

Like every check they are pure functions of metadata: the ops read the file, these decide. A
check returns None when it does not apply (a PDF made without redaction has nothing to say about
redaction).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from atelier.checks import CheckResult, Status, check
from atelier.media import MediaType

OFFICE = (MediaType.DOCX, MediaType.PPTX, MediaType.XLSX)
PDF = MediaType.PDF
SHOWN = 4  # how many names a message lists before saying "and N more"


def _ok(name: str) -> CheckResult:
    return CheckResult(name, Status.PASS)


def _list(items: list[str]) -> str:
    head = ", ".join(items[:SHOWN])
    return head + (f" and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


@check("no_placeholders", OFFICE, "no {{placeholder}} is left unfilled", default=True)
def no_placeholders(meta: Mapping[str, Any]) -> CheckResult | None:
    left = meta.get("placeholders")
    if left is None:
        return None
    if not left:
        return _ok("no_placeholders")
    return CheckResult(
        "no_placeholders",
        Status.FAIL,
        f"{len(left)} placeholder(s) still in the file: "
        + _list(["{{" + name + "}}" for name in left]),
        "give them values with doc.fill, or take them out with doc.replace",
    )


@check("formula_errors", MediaType.XLSX, "no formula shows an error like #DIV/0!", default=True)
def formula_errors(meta: Mapping[str, Any]) -> CheckResult | None:
    errors = meta.get("errors")
    if errors is None:
        return None
    if not errors:
        return _ok("formula_errors")
    where = [f"{e['sheet']}!{e['cell']} is {e['value']}" for e in errors]
    return CheckResult(
        "formula_errors",
        Status.FAIL,
        _list(where),
        "fix the formulas or the cells they read with xlsx.write, then xlsx.recalc again",
    )


@check(
    "text_overflow",
    MediaType.PPTX,
    "text fits inside its box (an estimate)",
    default=True,
)
def text_overflow(meta: Mapping[str, Any]) -> CheckResult | None:
    over = meta.get("overflow")
    if over is None:
        return None
    if not over:
        return _ok("text_overflow")
    first = over[0]
    return CheckResult(
        "text_overflow",
        Status.WARN,
        f"slide {first['slide']} {first['shape']!r} needs about {first['need_pt']}pt of height "
        f"but has {first['have_pt']}pt" + (f" (and {len(over) - 1} more)" if len(over) > 1 else ""),
        "shorten the text, split it over two slides, or make the box taller",
    )


@check("fonts_embedded", PDF, "every font is embedded in the PDF (print and archive)")
def fonts_embedded(meta: Mapping[str, Any]) -> CheckResult | None:
    fonts = meta.get("fonts")
    if fonts is None:
        return None
    loose = [f["name"] for f in fonts if not f["embedded"]]
    if not loose:
        return _ok("fonts_embedded")
    return CheckResult(
        "fonts_embedded",
        Status.FAIL,
        f"{len(loose)} font(s) are not embedded: {_list(loose)}",
        "make the PDF from the source file with doc.convert; stamps and forms use standard "
        "fonts that are not embedded",
    )


@check("redaction_complete", PDF, "redacted text is really gone from the file", default=True)
def redaction_complete(meta: Mapping[str, Any]) -> CheckResult | None:
    done = meta.get("redaction")
    if not done:
        return None
    leaked = done.get("leaked") or []
    if leaked:
        return CheckResult(
            "redaction_complete",
            Status.FAIL,
            f"{len(leaked)} redacted text(s) can still be found in the file: {_list(leaked)}",
            "redact again with a term that covers it, or check for text in bookmarks and forms",
        )
    return _ok("redaction_complete")


@check("pdf_valid", PDF, "the PDF is structurally sound", default=True)
def pdf_valid(meta: Mapping[str, Any]) -> CheckResult | None:
    problems = meta.get("problems")
    if problems is None:
        return None
    if not problems:
        return _ok("pdf_valid")
    return CheckResult(
        "pdf_valid", Status.FAIL, f"{len(problems)} structural problem(s): {problems[0]}",
        "rebuild the file with pdf.pages (all pages) or from its source document",
    )  # fmt: skip
