"""Run-aware text editing and the fill/replace ops on Word, PowerPoint and Excel files."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import docx
import openpyxl
import pytest
from lxml import etree
from pptx import Presentation

from atelier.docs import package, text
from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits, StepFailed
from atelier.ops.registry import OpError
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

DOCS = FIXTURES / "docs"


def nodes(*parts: str) -> list[Any]:
    """Text elements standing in for runs."""
    root = etree.Element("p")
    out = []
    for part in parts:
        t = etree.SubElement(root, f"{{{text.W}}}t")
        t.text = part
        out.append(t)
    return out


def joined(ns: list[Any]) -> str:
    return "".join(n.text or "" for n in ns)


# -- the run-splitting engine: pure -------------------------------------------------------------


def test_a_match_inside_one_run_keeps_its_neighbours() -> None:
    ns = nodes("Hello ", "{{name}}", "!")
    assert text.apply(ns, text.fill_matcher({"name": "Ada"}, set(), set())) == 1
    assert [n.text for n in ns] == ["Hello ", "Ada", "!"]


def test_a_match_split_across_runs_goes_into_the_first_run_it_starts_in() -> None:
    ns = nodes("Dear ", "{{na", "me}},")
    text.apply(ns, text.fill_matcher({"name": "Ada"}, set(), set()))
    assert [n.text for n in ns] == ["Dear ", "Ada", ","]  # the middle run's formatting wins


def test_a_match_spread_over_three_runs_and_two_matches_in_one_paragraph() -> None:
    ns = nodes("{{", "a", "}} and {{b", "}}")
    n = text.apply(ns, text.fill_matcher({"a": "1", "b": "2"}, set(), set()))
    assert n == 2 and joined(ns) == "1 and 2"


def test_missing_keys_are_reported_and_left_alone() -> None:
    ns = nodes("{{known}} {{unknown}}")
    missing: set[str] = set()
    text.apply(ns, text.fill_matcher({"known": "x"}, missing, set()))
    assert joined(ns) == "x {{unknown}}" and missing == {"unknown"}


def test_replacement_text_that_looks_like_a_placeholder_is_not_expanded_again() -> None:
    ns = nodes("{{a}}")
    text.apply(ns, text.fill_matcher({"a": "{{b}}", "b": "no"}, set(), set()))
    assert joined(ns) == "{{b}}"


def test_values_read_the_way_a_document_would() -> None:
    ns = nodes("{{a}}|{{b}}|{{c}}|{{d}}|{{e}}")
    data = {"a": True, "b": 0.1 + 0.2, "c": None, "d": 3, "e": "x"}
    text.apply(ns, text.fill_matcher(data, set(), set()))
    assert joined(ns) == "Yes|0.3||3|x"


def test_dotted_names_reach_into_nested_data() -> None:
    ns = nodes("{{customer.city}}")
    text.apply(ns, text.fill_matcher({"customer": {"city": "Lagos"}}, set(), set()))
    assert joined(ns) == "Lagos"


def test_edited_text_with_edge_spaces_is_marked_to_keep_them() -> None:
    ns = nodes("{{a}}")
    text.apply(ns, text.fill_matcher({"a": " padded "}, set(), set()))
    assert ns[0].get(text.XML_SPACE) == "preserve"


@pytest.mark.parametrize(
    ("parts", "find", "repl", "kw", "expected"),
    [
        (("Cat cat CAT",), "cat", "dog", {}, "dog dog dog"),
        (("Cat cat CAT",), "cat", "dog", {"match_case": True}, "Cat dog CAT"),
        (("cat concat",), "cat", "dog", {"whole_word": True}, "dog concat"),
        (("a.b (x)",), "(x)", "[y]", {}, "a.b [y]"),  # regex characters are literal
        (("ab", "cd"), "bc", "X", {}, "aXd"),
    ],
)
def test_replace_options(
    parts: tuple[str, ...], find: str, repl: str, kw: dict[str, bool], expected: str
) -> None:
    ns = nodes(*parts)
    text.apply(
        ns,
        text.replace_matcher(
            [(find, repl)],
            match_case=kw.get("match_case", False),
            whole_word=kw.get("whole_word", False),
        ),
    )
    assert joined(ns) == expected


def test_overlapping_finds_do_not_corrupt_the_text() -> None:
    ns = nodes("abcd")
    text.apply(
        ns, text.replace_matcher([("abc", "1"), ("bcd", "2")], match_case=True, whole_word=False)
    )
    assert joined(ns) == "1d"  # the first find wins the overlap


# -- fill on real files -------------------------------------------------------------------------


@pytest.fixture
def letter(engine: Engine) -> str:
    return engine.store.put_file(DOCS / "letter.docx").handle


def run_one(engine: Engine, handle: str, op: str, args: dict[str, Any]) -> Path:
    spec = {"in": handle, "ops": [[op, args]]}
    result = engine.run(spec)
    return engine.store.path(next(iter(result.outputs.values())).hash)


FILL = {
    "company": "Acme",
    "name": "Ada",
    "order": {"id": "A-17"},
    "date": "Monday",
    "total": "$42",
    "items_table": "(table)",
    "logo": "(logo)",
}


def test_fill_replaces_placeholders_everywhere_and_keeps_formatting(
    engine: Engine, letter: str
) -> None:
    out = run_one(engine, letter, "doc.fill", {"data": FILL})
    d = docx.Document(str(out))
    assert d.sections[0].header.paragraphs[0].text == "Acme confidential"
    greeting = d.paragraphs[0]
    assert greeting.text == "Dear Ada,"
    assert [r.bold for r in greeting.runs if r.text == "Dear "] == [True]
    assert (
        greeting.runs[1].text == "Ada" and greeting.runs[2].italic
    )  # the split run kept its style
    assert d.paragraphs[1].text == "Your order A-17 ships on Monday."
    assert d.tables[0].cell(1, 1).text == "$42"
    monday = next(r for r in d.paragraphs[1].runs if r.text == "Monday")
    assert monday.font.size is not None and monday.font.size.pt == 14  # the run's own size


def test_the_result_records_what_is_left_and_summarizes_it(engine: Engine, letter: str) -> None:
    result = engine.run({"in": letter, "ops": [["doc.fill", {"data": {**FILL, "total": "$1"}}]]})
    rep = next(iter(result.outputs.values()))
    assert rep.meta["placeholders"] == [] and rep.meta["edits"] == 7
    assert rep.summary.startswith("docx · 5 paragraphs · 1 tables · 7 edits")
    partial = engine.run(
        {"in": letter, "ops": [["doc.fill", {"data": {"name": "Ada"}, "strict": False}]]}
    )
    assert next(iter(partial.outputs.values())).meta["placeholders"] == [
        "company", "date", "items_table", "logo", "order.id", "total",
    ]  # fmt: skip


def test_strict_fill_names_what_is_missing_and_what_the_file_has(
    engine: Engine, letter: str
) -> None:
    with pytest.raises(StepFailed) as exc:
        engine.run({"in": letter, "ops": [["doc.fill", {"data": {"name": "Ada", "nope": "x"}}]]})
    msg = str(exc.value)
    assert "no value for {{company}}" in msg or "no value for" in msg
    assert "no placeholder for nope" in msg
    assert "company, date, items_table, logo, name, order.id, total" in msg  # the file's own list


def test_fill_rejects_lists_and_empty_data(engine: Engine, letter: str) -> None:
    with pytest.raises(SpecInvalid, match="not a list"):
        engine.validate({"in": letter, "ops": [["doc.fill", {"data": {"rows": [1, 2]}}]]})
    with pytest.raises(SpecInvalid):
        engine.validate({"in": letter, "ops": [["doc.fill", {"data": {}}]]})


def test_fill_on_a_deck_and_a_workbook(engine: Engine) -> None:
    deck = engine.store.put_file(DOCS / "deck.pptx").handle
    out = run_one(
        engine,
        deck,
        "doc.fill",
        {"data": {"name": "Ada", "revenue": "$5M", "figures": "n/a", "chart": "n/a"}},
    )
    prs = Presentation(str(out))
    assert prs.slides[0].shapes.title.text == "Welcome Ada"
    assert prs.slides[1].placeholders[1].text_frame.text == "Revenue: $5M"
    book = engine.store.put_file(DOCS / "book.xlsx").handle
    out = run_one(engine, book, "doc.fill", {"data": {"client": "Acme"}})
    with out.open("rb") as handle:
        ws = openpyxl.load_workbook(handle).active
    assert ws["A1"].value == "Report for Acme" and ws["B6"].value == "=SUM(B3:B5)"  # formulas kept


def test_a_pdf_is_not_a_document_for_fill(engine: Engine) -> None:
    pdf = engine.store.put_file(DOCS / "report.pdf").handle
    with pytest.raises(SpecInvalid, match="must be docx"):
        engine.validate({"in": pdf, "ops": [["doc.fill", {"data": {"a": "b"}}]]})


# -- replace ------------------------------------------------------------------------------------


def test_replace_counts_and_keeps_formatting(engine: Engine, letter: str) -> None:
    out = run_one(
        engine, letter, "doc.replace", {"replacements": {"regards": "Best wishes", "TOTAL": "Sum"}}
    )
    d = docx.Document(str(out))
    assert d.paragraphs[-1].text == "Best wishes, the team"
    assert d.tables[0].cell(1, 0).text == "Sum"


def test_replace_says_which_texts_were_not_found(engine: Engine, letter: str) -> None:
    with pytest.raises(StepFailed, match=r"not found in the file: 'Missing', 'gone'"):
        engine.run(
            {
                "in": letter,
                "ops": [
                    ["doc.replace", {"replacements": {"Missing": "x", "gone": "y", "team": "crew"}}]
                ],
            }
        )
    ok = engine.run(
        {
            "in": letter,
            "ops": [["doc.replace", {"replacements": {"Missing": "x"}, "require_match": False}]],
            "checks": "none",  # the letter still has placeholders, which the default checks flag
        }
    )
    assert ok.passed


def test_replace_validation(engine: Engine, letter: str) -> None:
    with pytest.raises(SpecInvalid, match="cannot be empty"):
        engine.validate({"in": letter, "ops": [["doc.replace", {"replacements": {"": "x"}}]]})


# -- determinism and the package ----------------------------------------------------------------


def test_the_same_edit_gives_the_same_bytes_on_a_fresh_engine(
    engine: Engine, letter: str, tmp_path: Path
) -> None:
    first = run_one(engine, letter, "doc.fill", {"data": FILL}).read_bytes()
    fresh = Engine.open(tmp_path / "other", run_limits=RunLimits(wall_seconds=60))
    handle = fresh.store.put_file(DOCS / "letter.docx").handle
    assert run_one(fresh, handle, "doc.fill", {"data": FILL}).read_bytes() == first


def test_the_output_package_is_sorted_dated_and_stamped_the_same_way_every_time(
    engine: Engine, letter: str
) -> None:
    out = run_one(engine, letter, "doc.fill", {"data": FILL})
    with zipfile.ZipFile(out) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        assert names[0] == "[Content_Types].xml" and names[1:] == sorted(names[1:])
        assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}
        core = z.read("docProps/core.xml").decode()
    assert "2000-01-01T00:00:00Z" in core and "lastModifiedBy></cp:lastModifiedBy" in core.replace(
        " ", ""
    )


# -- untrusted packages -------------------------------------------------------------------------


def make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def base_docx(tmp: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(DOCS / "letter.docx") as z:
        return {n: z.read(n) for n in z.namelist()}


def test_a_package_with_a_macro_project_is_refused(tmp_path: Path) -> None:
    entries = {**base_docx(tmp_path), "word/vbaProject.bin": b"\x00" * 10}
    with pytest.raises(OpError, match="macro project"):
        package.check(make_zip(tmp_path / "m.docx", entries))


def test_a_package_that_links_outside_itself_is_refused(tmp_path: Path) -> None:
    entries = base_docx(tmp_path)
    ns = "http://schemas.openxmlformats.org"
    rels = (
        f'<?xml version="1.0"?><Relationships xmlns="{ns}/package/2006/relationships">'
        f'<Relationship Id="r9" Type="{ns}/officeDocument/2006/relationships/attachedTemplate" '
        'Target="http://evil.example/t.dotm" TargetMode="External"/></Relationships>'
    ).encode()
    entries["word/_rels/settings.xml.rels"] = rels
    with pytest.raises(OpError, match="links to content outside the file"):
        package.check(make_zip(tmp_path / "x.docx", entries))


def test_a_linked_picture_is_refused_but_a_hyperlink_is_fine(tmp_path: Path) -> None:
    ns = "http://schemas.openxmlformats.org"

    def rels(kind: str) -> bytes:
        return (
            f'<?xml version="1.0"?><Relationships xmlns="{ns}/package/2006/relationships">'
            f'<Relationship Id="r9" Type="{ns}/officeDocument/2006/relationships/{kind}" '
            'Target="http://example.com/x" TargetMode="External"/></Relationships>'
        ).encode()

    entries = base_docx(tmp_path)
    entries["word/_rels/extra.xml.rels"] = rels("image")
    with pytest.raises(OpError, match="links to content outside the file"):
        package.check(make_zip(tmp_path / "img.docx", entries))
    entries["word/_rels/extra.xml.rels"] = rels("hyperlink")
    package.check(make_zip(tmp_path / "link.docx", entries))


def test_a_zip_bomb_is_refused(tmp_path: Path) -> None:
    entries = {**base_docx(tmp_path), "word/bomb.xml": b"0" * (20 * 1024 * 1024)}
    with pytest.raises(OpError, match="compressed suspiciously well"):
        package.check(make_zip(tmp_path / "b.docx", entries))


def test_a_damaged_container_and_a_normal_link_are_told_apart(tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(OpError, match="damaged"):
        package.check(bad)
    package.check(DOCS / "letter.docx")  # an ordinary file is fine


def test_a_document_with_a_macro_is_refused_through_the_engine(
    engine: Engine, tmp_path: Path
) -> None:
    entries = {**base_docx(tmp_path), "word/vbaProject.bin": b"\x00" * 10}
    handle = engine.store.put_file(make_zip(tmp_path / "m.docx", entries)).handle
    with pytest.raises(StepFailed, match="macro project"):
        engine.run({"in": handle, "ops": [["doc.fill", {"data": {"a": "b"}, "strict": False}]]})
