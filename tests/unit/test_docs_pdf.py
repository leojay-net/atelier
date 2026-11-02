"""PDF ops on real files: merge, pages, rotate, stamp, form, redact, compress."""

from __future__ import annotations

import io
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pikepdf
import pytest
from PIL import ImageChops

from atelier.docs import pdfdoc, pdfio
from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits, StepFailed
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES

DOCS = FIXTURES / "docs"


def put(engine: Engine, name: str) -> str:
    return engine.store.put_file(DOCS / name).handle


def step(engine: Engine, handles: dict[str, str], op: str, ins: list[str], **args: Any) -> Path:
    spec = {
        "version": 1,
        "inputs": handles,
        "steps": [{"id": "s", "op": op, "in": ins, "args": args}],
        "outputs": ["s"],
    }
    return engine.store.path(engine.run(spec).outputs["s"].hash)


def one(engine: Engine, name: str, op: str, **args: Any) -> Path:
    return step(engine, {"f": put(engine, name)}, op, ["f"], **args)


def texts(path: Path) -> list[str]:
    doc = pdfdoc.open_render(path)
    try:
        return [pdfdoc.page_text(doc, i).replace("\r\n", "\n") for i in range(len(doc))]
    finally:
        doc.close()


def page_count(path: Path) -> int:
    with pikepdf.Pdf.open(path) as pdf:
        return len(pdf.pages)


# -- merge, pages, rotate ---------------------------------------------------------------------


def test_merge_puts_the_files_in_order(engine: Engine) -> None:
    handles = {"a": put(engine, "report.pdf"), "b": put(engine, "secret.pdf")}
    out = step(engine, handles, "pdf.merge", ["b", "a"])
    pages = texts(out)
    assert len(pages) == 4 and pages[0].startswith("Client file") and pages[1].startswith("Page 1")


def test_merge_needs_at_least_two_files(engine: Engine) -> None:
    with pytest.raises(SpecInvalid, match=r"pdf.merge takes 2\+ input\(s\), got 1"):
        engine.validate({"in": put(engine, "report.pdf"), "ops": [["pdf.merge", {}]]})


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("2-", ["Page 2", "Page 3"]), ("3,1", ["Page 3", "Page 1"]), ("odd", ["Page 1", "Page 3"]),
     ("even", ["Page 2"]), ("last", ["Page 3"]), ("1-2,3", ["Page 1", "Page 2", "Page 3"]),
     ("2,2", ["Page 2", "Page 2"])],
)  # fmt: skip
def test_pages_selects_reorders_and_repeats(engine: Engine, spec: str, expected: list[str]) -> None:
    out = one(engine, "report.pdf", "pdf.pages", pages=spec)
    assert [t.split(" of")[0] for t in texts(out)] == expected


def test_pages_errors_say_what_to_change(engine: Engine) -> None:
    with pytest.raises(StepFailed, match=r"page 9 is past the end: the file has 3 pages"):
        one(engine, "report.pdf", "pdf.pages", pages="1,9")
    with pytest.raises(StepFailed, match="pages are numbered from 1"):
        one(engine, "report.pdf", "pdf.pages", pages="0")
    with pytest.raises(StepFailed, match=r"the range 3-1 goes backwards"):
        one(engine, "report.pdf", "pdf.pages", pages="3-1")
    with pytest.raises(SpecInvalid, match="is not a page list"):
        engine.validate({"in": put(engine, "report.pdf"), "ops": [["pdf.pages", {"pages": "x"}]]})


def test_rotate_turns_the_chosen_pages_on_top_of_existing_rotation(engine: Engine) -> None:
    once = one(engine, "report.pdf", "pdf.rotate", degrees=90, pages="1")
    with pikepdf.Pdf.open(once) as pdf:
        assert [int(p.get("/Rotate", 0)) for p in pdf.pages] == [90, 0, 0]
    handle = engine.store.put_file(once).handle
    twice = step(engine, {"f": handle}, "pdf.rotate", ["f"], degrees=270, pages="1,2")
    with pikepdf.Pdf.open(twice) as pdf:
        assert [int(p.get("/Rotate", 0)) % 360 for p in pdf.pages] == [0, 270, 0]


def test_active_content_is_removed_from_every_result(engine: Engine, tmp_path: Path) -> None:
    pdf = pdfio.build_text_pdf([["hello"]])
    js = pdf.make_indirect(
        pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert(1)"))
    )
    pdf.Root.OpenAction = js
    src = tmp_path / "js.pdf"
    pdf.save(src)
    assert pdfdoc.describe(src)["javascript"] is True
    handle = engine.store.put_file(src).handle
    out = step(engine, {"f": handle}, "pdf.pages", ["f"], pages="all")
    assert pdfdoc.describe(out)["javascript"] is False


def test_a_password_protected_pdf_is_refused_with_a_way_out(engine: Engine, tmp_path: Path) -> None:
    pdf = pdfio.build_text_pdf([["x"]])
    src = tmp_path / "locked.pdf"
    pdf.save(src, encryption=pikepdf.Encryption(user="pw", owner="pw"))
    handle = engine.store.put_file(src).handle
    with pytest.raises(StepFailed, match="password protected"):
        step(engine, {"f": handle}, "pdf.pages", ["f"], pages="all")


def test_a_non_pdf_is_refused_by_media(engine: Engine) -> None:
    with pytest.raises(SpecInvalid, match="must be pdf"):
        engine.validate(
            {"in": put(engine, "letter.docx"), "ops": [["pdf.rotate", {"degrees": 90}]]}
        )


# -- stamp ------------------------------------------------------------------------------------


def stamp_box(path: Path, page: int, needle: str) -> tuple[float, float, float, float]:
    import re

    doc = pdfdoc.open_render(path)
    try:
        boxes = pdfdoc.find_boxes(doc, page, re.compile(re.escape(needle)))
        assert boxes, f"{needle!r} not found on page {page + 1}"
        return boxes[0]
    finally:
        doc.close()


def test_page_numbers_land_bottom_center_on_every_page(engine: Engine) -> None:
    out = one(engine, "report.pdf", "pdf.stamp", text="p. {page}/{pages}", size=10)
    for i, t in enumerate(texts(out), start=1):
        assert f"p. {i}/3" in t
    left, bottom, right, _ = stamp_box(out, 0, "p. 1/3")
    assert bottom < 60 and abs((left + right) / 2 - 306) < 6  # low on the page, centered on 612pt


@pytest.mark.parametrize(
    ("position", "check"),
    [
        ("top_left", lambda b: b[0] < 60 and b[3] > 700),
        ("top_right", lambda b: b[2] > 550 and b[3] > 700),
        ("bottom_left", lambda b: b[0] < 60 and b[1] < 60),
        ("bottom_right", lambda b: b[2] > 550 and b[1] < 60),
        ("center", lambda b: 350 < b[1] < 430 and abs((b[0] + b[2]) / 2 - 306) < 6),
    ],
)
def test_stamp_positions(engine: Engine, position: str, check: Any) -> None:
    out = one(engine, "report.pdf", "pdf.stamp", text="STAMPED", position=position, pages="1")
    box = stamp_box(out, 0, "STAMPED")
    assert check(box), (position, box)
    assert "STAMPED" not in texts(out)[1]  # only the chosen page


def test_bates_numbers_run_across_the_document(engine: Engine) -> None:
    out = one(
        engine, "report.pdf", "pdf.stamp", text="ACME-{bates}", bates_start=100, bates_digits=6
    )
    for number, page_text in zip((100, 101, 102), texts(out), strict=True):
        assert f"ACME-{number:06d}" in page_text
    partial = one(engine, "report.pdf", "pdf.stamp", text="B{bates}", pages="2-", bates_start=1)
    assert "B000002" in texts(partial)[1]  # numbers follow the page, not the count stamped


def test_a_watermark_is_faint_and_diagonal(engine: Engine, tmp_path: Path) -> None:
    plain = engine.store.path(engine.store.put_file(DOCS / "report.pdf").hash)
    out = one(engine, "report.pdf", "pdf.stamp", text="DRAFT", position="center", size=90,
              opacity=0.2, angle=45, color="#ff0000")  # fmt: skip
    a = pdfdoc.render_page(pdfdoc.open_render(plain), 0, 50)
    b = pdfdoc.render_page(pdfdoc.open_render(out), 0, 50)
    diff = ImageChops.difference(a, b)
    box = diff.getbbox()
    assert box is not None
    w, h = a.size
    assert box[0] < w * 0.35 and box[2] > w * 0.65 and box[1] < h * 0.45 and box[3] > h * 0.55
    red, green, _ = (float(np.asarray(band).mean()) for band in diff.split())
    assert green > red >= 0  # faint red over white: green and blue drop, red barely moves
    assert "DRAFT" in texts(out)[0]  # still text, still selectable


def test_a_stamp_on_a_rotated_page_lands_at_the_visible_bottom(engine: Engine) -> None:
    rotated = engine.store.put_file(
        one(engine, "report.pdf", "pdf.rotate", degrees=90, pages="1")
    ).handle
    out = step(engine, {"f": rotated}, "pdf.stamp", ["f"], text="LOOK", pages="1", size=24)
    plain = engine.store.path(engine.store.resolve(rotated).hash)
    a = pdfdoc.render_page(pdfdoc.open_render(plain), 0, 50)
    b = pdfdoc.render_page(pdfdoc.open_render(out), 0, 50)
    box = ImageChops.difference(a, b).getbbox()
    assert box is not None and a.size[0] > a.size[1]  # visible page is landscape now
    assert box[1] > a.size[1] * 0.8  # the stamp is at the visible bottom
    assert abs((box[0] + box[2]) / 2 - a.size[0] / 2) < a.size[0] * 0.05  # and centered


def test_stamp_text_is_an_embedded_subset_font_and_stays_selectable(engine: Engine) -> None:
    out = one(engine, "report.pdf", "pdf.stamp", text="Café № {page}", size=11)
    fonts = {f["name"]: f["embedded"] for f in pdfdoc.describe(out)["fonts"]}
    assert fonts["Inter-Regular"] is True  # the stamp's own font is in the file
    assert fonts["Helvetica"] is False  # the fixture's page text is not: it is not ours to change
    assert "Café" in texts(out)[0]
    with pikepdf.Pdf.open(out) as pdf:
        font = next(
            f for _, f in pdf.pages[0].Resources.XObject["/AtelierStamp0"].Resources.Font.items()
        )
        file = font.DescendantFonts[0].FontDescriptor.FontFile2.read_bytes()
    assert len(file) < 20_000  # a subset of a few characters, not the whole family


def test_a_stamp_the_font_cannot_draw_says_which_characters(engine: Engine) -> None:
    with pytest.raises(StepFailed, match=r"cannot draw '你'"):
        one(engine, "report.pdf", "pdf.stamp", text="你 {page}")


def test_a_stamp_wider_than_the_page_is_refused(engine: Engine) -> None:
    with pytest.raises(StepFailed, match=r"the stamp is \d+pt wide but page 1 is only 612pt"):
        one(engine, "report.pdf", "pdf.stamp", text="W" * 80, size=30)


# -- form -------------------------------------------------------------------------------------


def field_values(path: Path) -> dict[str, Any]:
    with pikepdf.Pdf.open(path) as pdf:
        return {
            f.fully_qualified_name: f.is_checked if f.is_checkbox else f.value_as_string
            for f in pdf.acroform.fields
        }


def test_form_fields_are_filled_by_name(engine: Engine) -> None:
    out = one(engine, "form.pdf", "pdf.form",
              fields={"full_name": "Jane Roe", "city": "Lagos", "agree": True})  # fmt: skip
    values = field_values(out)
    assert values["full_name"] == "Jane Roe" and values["city"] == "Lagos"
    assert values["agree"] is True
    fields = {f["name"]: f["value"] for f in pdfdoc.describe(out)["fields"]}
    assert fields == {"full_name": "Jane Roe", "city": "Lagos", "agree": "on"}
    off = one(engine, "form.pdf", "pdf.form", fields={"agree": False})
    assert field_values(off)["agree"] is False


def test_flatten_makes_the_values_part_of_the_page(engine: Engine) -> None:
    out = one(engine, "form.pdf", "pdf.form", fields={"full_name": "Jane Roe"}, flatten=True)
    info = pdfdoc.describe(out)
    assert info["fields"] == [] and "Jane Roe" in texts(out)[0]


def test_form_errors_list_what_exists(engine: Engine) -> None:
    with pytest.raises(
        StepFailed, match=r"no such field: 'nope'. The form has: agree, city, full_name"
    ):
        one(engine, "form.pdf", "pdf.form", fields={"nope": "x"})
    with pytest.raises(StepFailed, match="'agree' is a checkbox: give true or false"):
        one(engine, "form.pdf", "pdf.form", fields={"agree": "yes"})
    with pytest.raises(StepFailed, match="has no form fields"):
        one(engine, "report.pdf", "pdf.form", fields={"a": "b"})


# -- redact -----------------------------------------------------------------------------------

ACCOUNT = r"\d{4}-\d{4}-\d{4}-\d{4}"


def test_redaction_removes_the_text_not_just_covers_it(engine: Engine) -> None:
    out = one(engine, "secret.pdf", "pdf.redact", terms=["Jane Roe"], patterns=[ACCOUNT])
    body = "".join(texts(out))
    assert "Jane" not in body and "4111" not in body and "Roe" not in body
    info = pdfdoc.describe(out)
    assert info["text_chars"] == 0  # the page is a picture now: there is nothing to extract
    with pikepdf.Pdf.open(out) as pdf:  # and no text operators or fonts were left behind
        assert b"Tj" not in pdf.pages[0].Contents.read_bytes()
        assert "/Font" not in pdf.pages[0].Resources


def test_the_redacted_places_are_black_and_the_rest_is_still_visible(engine: Engine) -> None:
    import re

    src = engine.store.path(engine.store.put_file(DOCS / "secret.pdf").hash)
    out = one(engine, "secret.pdf", "pdf.redact", terms=["Jane Roe"])
    doc = pdfdoc.open_render(src)
    (left, bottom, right, top), *_ = pdfdoc.find_boxes(doc, 0, re.compile("Jane Roe"))
    (pl, pb, pr, pt), *_ = pdfdoc.find_boxes(doc, 0, re.compile("This line is public"))
    before = np.asarray(pdfdoc.render_page(doc, 0, 72).convert("L"))
    after = np.asarray(pdfdoc.render_page(pdfdoc.open_render(out), 0, 72).convert("L"))
    assert after.shape == before.shape
    h = before.shape[0]

    def region(im: np.ndarray, l: float, b: float, r: float, t: float) -> np.ndarray:  # noqa: E741
        return im[int(h - t) + 1 : int(h - b) - 1, int(l) + 1 : int(r) - 1]

    assert region(after, left, bottom, right, top).max() < 30  # solid black over the name
    kept = region(after, pl, pb, pr, pt)
    # the picture still shows the public line (a JPEG changes pixels a little, not their layout)
    assert kept.min() < 100
    assert np.abs(region(before, pl, pb, pr, pt).astype(int) - kept.astype(int)).mean() < 40


def test_only_pages_with_a_match_are_turned_into_pictures(engine: Engine) -> None:
    handles = {"a": put(engine, "report.pdf"), "b": put(engine, "secret.pdf")}
    merged = engine.store.put_file(step(engine, handles, "pdf.merge", ["a", "b"])).handle
    out = step(engine, {"f": merged}, "pdf.redact", ["f"], terms=["Jane Roe"])
    pages = texts(out)
    assert pages[0].startswith("Page 1") and pages[2].startswith("Page 3")  # untouched
    assert pages[3] == ""  # only the last one was redrawn
    meta = engine.run({"in": merged, "ops": [["pdf.redact", {"terms": ["Jane Roe"]}]]}).outputs
    redaction = next(iter(meta.values())).meta["redaction"]
    assert redaction["pages"] == [4] and redaction["hits"] == 1 and redaction["leaked"] == []


def test_titles_and_bookmarks_that_repeat_the_secret_are_dropped(
    engine: Engine, tmp_path: Path
) -> None:
    pdf = pdfio.open_pdf(DOCS / "secret.pdf")
    pdf.docinfo["/Title"] = "Jane Roe - client file"
    pdf.docinfo["/Author"] = "Front desk"
    with pdf.open_outline() as outline:
        outline.root.append(pikepdf.OutlineItem("Jane Roe", 0))
    src = tmp_path / "titled.pdf"
    pdf.save(src)
    handle = engine.store.put_file(src).handle
    out = step(engine, {"f": handle}, "pdf.redact", ["f"], terms=["Jane Roe"])
    with pikepdf.Pdf.open(out) as result:
        assert "/Title" not in result.docinfo and str(result.docinfo["/Author"]) == "Front desk"
        with result.open_outline() as outline:
            assert len(outline.root) == 0


def test_redaction_asks_for_something_to_redact_and_reports_a_miss(engine: Engine) -> None:
    with pytest.raises(StepFailed, match="give at least one term or pattern"):
        one(engine, "secret.pdf", "pdf.redact")
    with pytest.raises(StepFailed, match="nothing matched"):
        one(engine, "secret.pdf", "pdf.redact", terms=["not on the page"])
    ok = engine.run(
        {"in": put(engine, "secret.pdf"),
         "ops": [["pdf.redact", {"terms": ["absent"], "require_match": False}]]}
    )  # fmt: skip
    assert ok.passed
    with pytest.raises(SpecInvalid, match="not a valid pattern"):
        engine.validate(
            {"in": put(engine, "secret.pdf"), "ops": [["pdf.redact", {"patterns": ["("]}]]}
        )
    with pytest.raises(SpecInvalid, match="cannot be empty"):
        engine.validate(
            {"in": put(engine, "secret.pdf"), "ops": [["pdf.redact", {"terms": [" "]}]]}
        )


# -- compress ---------------------------------------------------------------------------------


def photo_pdf(path: Path) -> Path:
    import numpy as np

    rng = np.random.default_rng(3)
    grad = np.linspace(0, 255, 1600)[None, :, None] * np.ones((1200, 1, 3))
    pixels = np.clip(grad + rng.normal(0, 25, (1200, 1600, 3)), 0, 255).astype("uint8")
    pdf = pikepdf.Pdf.new()
    xo = pdf.make_stream(
        zlib.compress(pixels.tobytes()),
        Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=1600, Height=1200,
        ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8, Filter=pikepdf.Name.FlateDecode,
    )  # fmt: skip
    content = pdf.make_stream(b"q 400 0 0 300 100 400 cm /Im0 Do Q")
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792], Contents=content,
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xo)),
    )  # fmt: skip
    pdf.pages.append(pikepdf.Page(page))
    pdfio.save(pdf, path)
    return path


def test_compress_shrinks_pictures_and_keeps_the_page(engine: Engine, tmp_path: Path) -> None:
    src = photo_pdf(tmp_path / "photo.pdf")
    handle = engine.store.put_file(src).handle
    result = engine.run({"in": handle, "ops": [["pdf.compress", {"quality": 40, "max_side": 800}]]})
    rep = next(iter(result.outputs.values()))
    out = engine.store.path(rep.hash)
    assert out.stat().st_size < src.stat().st_size / 3
    assert rep.meta["images_recompressed"] == 1 and rep.meta["pages"] == 1
    assert pdfdoc.render_page(pdfdoc.open_render(out), 0, 40).getbbox() is not None


def test_compress_never_returns_a_bigger_file(engine: Engine) -> None:
    src = DOCS / "report.pdf"  # text only: nothing to recompress
    out = one(engine, "report.pdf", "pdf.compress")
    assert out.stat().st_size <= src.stat().st_size + 64


# -- determinism ------------------------------------------------------------------------------


def test_results_are_byte_identical_on_a_fresh_engine(engine: Engine, tmp_path: Path) -> None:
    args = {"text": "p. {page}/{pages}"}
    first = one(engine, "report.pdf", "pdf.stamp", **args).read_bytes()
    fresh = Engine.open(tmp_path / "other", run_limits=RunLimits(wall_seconds=60))
    handle = fresh.store.put_file(DOCS / "report.pdf").handle
    second = step(fresh, {"f": handle}, "pdf.stamp", ["f"], **args).read_bytes()
    assert first == second
    with pikepdf.Pdf.open(io.BytesIO(first)) as pdf:
        assert "/CreationDate" not in pdf.docinfo and str(pdf.docinfo["/Producer"]) == "Atelier"
