"""PDF ops (docs/03 §3.4): merge, pick pages, rotate, stamp, fill forms, redact, compress.

Structure is edited with pikepdf (qpdf), text is found and pages are rendered with pypdfium2.
Every result is saved deterministically and stripped of JavaScript, launch actions and embedded
files. Redaction does not draw boxes over text: pages that contain a match are re-drawn as a
picture with the match blacked out, so there is nothing underneath to select or extract.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal, cast

import pikepdf
from PIL import Image
from pydantic import Field, field_validator

from atelier.docs import embed, pdfdoc, pdfio
from atelier.media import MediaType
from atelier.ops.registry import (
    AnyOf,
    Artifact,
    Determinism,
    OpContext,
    OpError,
    Params,
    op,
)
from atelier.spec.types import Color, Fraction, rgba

PDF = MediaType.PDF
RESOURCES = ("pdf",)
MAX_MERGE = 50
MAX_TERMS = 100
MAX_PATTERN = 200

_ITEM = r"(?:all|odd|even|last|\d+(?:-(?:\d+|last)?)?)"
_PAGES = re.compile(rf"^\s*{_ITEM}\s*(?:,\s*{_ITEM}\s*)*$")


def pdf_summary(result: Artifact) -> str:
    m = result.meta
    size = m["sizes_pt"][0] if m.get("sizes_pt") else None
    parts = ["pdf", f"{m['pages']} page{'s' * (m['pages'] != 1)}"]
    if size:
        parts.append(f"{size[0]:g}x{size[1]:g}pt")
    parts.append(f"{result.path.stat().st_size / 1024:.0f}KB")
    return " · ".join(parts)


def pdf_op(
    *,
    name: str,
    params: type[Params],
    description: str,
    inputs: Sequence[MediaType] = (PDF,),
    variadic: bool = False,
    check_inputs: Callable[[Any, int], str | None] | None = None,
    determinism: Determinism = Determinism.BIT_EXACT,
    also: tuple[str, ...] = (),
) -> Callable[[Callable[..., Artifact]], Callable[..., Artifact]]:
    return op(
        name=name,
        version="1.0.0",
        inputs=list(inputs),
        output=PDF,
        determinism=determinism,
        params=params,
        summary=pdf_summary,
        description=description,
        variadic=variadic,
        check_inputs=check_inputs,
        resources=(*RESOURCES, *also),
    )


def finish(ctx: OpContext, pdf: pikepdf.Pdf, **extra: Any) -> Artifact:
    """Strip active content, save deterministically and describe the result."""
    pdfio.strip_active_content(pdf)
    out = ctx.output_path(".pdf")
    pdfio.save(pdf, out)
    pdf.close()
    return Artifact(out, PDF, ".pdf", {**pdfdoc.describe(out), **extra})


# -- page selection ----------------------------------------------------------------------------


def check_pages(spec: str) -> str:
    if not _PAGES.match(spec):
        raise ValueError(
            f"{spec!r} is not a page list: use 1-based pages like '1-3,5,8-' or 'odd', 'even', "
            "'last', 'all'"
        )
    return spec


def expand_pages(spec: str, count: int) -> list[int]:
    """0-based page indexes for a page list, in the order written."""

    def number(word: str) -> int:
        return count if word == "last" else int(word)

    out: list[int] = []
    for item in spec.replace(" ", "").split(","):
        if item == "all":
            out += range(count)
        elif item == "odd":
            out += range(0, count, 2)
        elif item == "even":
            out += range(1, count, 2)
        else:
            first, dash, second = item.partition("-")
            lo = number(first)
            hi = (number(second) if second else count) if dash else lo
            if lo < 1 or hi < 1:
                raise OpError("pages are numbered from 1")
            if hi < lo:
                raise OpError(f"the range {item} goes backwards")
            for n in (lo, hi):
                if n > count:
                    raise OpError(
                        f"page {n} is past the end: the file has {count} page{'s' * (count != 1)}"
                    )
            out += range(lo - 1, hi)
    return out


class PageList(Params):
    pages: str = Field(description="1-based pages, like '1-3,5,8-', 'odd', 'even', 'last'")

    @field_validator("pages")
    @classmethod
    def _valid(cls, v: str) -> str:
        return check_pages(v)


# -- merge -------------------------------------------------------------------------------------


class MergeParams(Params):
    pass


def _merge_inputs(p: MergeParams, n: int) -> str | None:
    return f"pdf.merge takes at most {MAX_MERGE} files, got {n}" if n > MAX_MERGE else None


@pdf_op(
    name="pdf.merge",
    params=MergeParams,
    description="join PDFs one after another (2 to 50)",
    inputs=(PDF, PDF),
    variadic=True,
    check_inputs=_merge_inputs,
)
def merge(ctx: OpContext, inputs: Sequence[Artifact], p: MergeParams) -> Artifact:
    """Put the pages of the inputs one after another, in the order given. Form fields are not
    carried over (fill and flatten them first with `pdf.form`)."""
    out = pikepdf.Pdf.new()
    sources = [pdfio.open_pdf(a.path) for a in inputs]
    for src in sources:
        out.pages.extend(src.pages)
    result = finish(ctx, out)
    for src in sources:
        src.close()
    return result


# -- pages, rotate -----------------------------------------------------------------------------


@pdf_op(
    name="pdf.pages",
    params=PageList,
    description="keep, reorder or split out pages of a PDF",
)
def pages(ctx: OpContext, inputs: Sequence[Artifact], p: PageList) -> Artifact:
    """Make a PDF of just these pages, in the order written ('3,1' swaps; '2-' drops the first
    page; 'odd' keeps every other one). To split a file into parts, use one step per part."""
    src = pdfio.open_pdf(inputs[0].path)
    keep = expand_pages(p.pages, len(src.pages))
    if not keep:
        raise OpError("no pages were selected")
    out = pikepdf.Pdf.new()
    for i in keep:
        out.pages.append(src.pages[i])
    result = finish(ctx, out)
    src.close()
    return result


class RotateParams(Params):
    degrees: Literal[90, 180, 270] = Field(description="clockwise")
    pages: str = Field("all", description="which pages; default all")

    @field_validator("pages")
    @classmethod
    def _valid(cls, v: str) -> str:
        return check_pages(v)


@pdf_op(name="pdf.rotate", params=RotateParams, description="rotate pages of a PDF clockwise")
def rotate(ctx: OpContext, inputs: Sequence[Artifact], p: RotateParams) -> Artifact:
    """Turn the chosen pages clockwise by 90, 180 or 270 degrees (on top of any rotation they
    already have)."""
    pdf = pdfio.open_pdf(inputs[0].path)
    for i in expand_pages(p.pages, len(pdf.pages)):
        pdf.pages[i].rotate(p.degrees, relative=True)
    return finish(ctx, pdf)


# -- stamp -------------------------------------------------------------------------------------

Position = Literal[
    "top_left", "top_center", "top_right", "center",
    "bottom_left", "bottom_center", "bottom_right",
]  # fmt: skip


class StampParams(Params):
    text: str = Field(
        min_length=1,
        max_length=200,
        description="text; {page}, {pages} and {bates} are replaced on each page",
    )
    position: Position = Field("bottom_center", description="where on the page")
    size: float = Field(10.0, ge=4, le=200, description="font size in points")
    color: Color = Field("#000000ff", description="text color")
    opacity: Fraction = Field(1.0, gt=0, le=1, description="1 is solid; a watermark uses ~0.2")
    angle: float = Field(
        0.0, ge=-90, le=90, description="degrees counter-clockwise (watermark: 45)"
    )
    margin: float = Field(36.0, ge=0, le=200, description="distance from the page edge in points")
    pages: str = Field("all", description="which pages to stamp; default all")
    bates_start: int = Field(1, ge=0, le=10**9, description="number for {bates} on page 1")
    bates_digits: int = Field(6, ge=1, le=12, description="{bates} is zero-padded to this width")

    @field_validator("pages")
    @classmethod
    def _valid(cls, v: str) -> str:
        return check_pages(v)


def _visible_size(page: pikepdf.Page) -> tuple[float, float]:
    x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    w, h = abs(x1 - x0), abs(y1 - y0)
    return (h, w) if int(page.get("/Rotate", 0)) % 180 else (w, h)


def _place(
    pdf: pikepdf.Pdf, page: pikepdf.Page, content: bytes, alpha: float, font: pikepdf.Object
) -> None:
    """Draw `content` (written in visible-page coordinates) on top of `page`.

    The stamp is a form XObject with a fixed, counted name: qpdf's own overlay helper invents a
    random name every time, which would make the output differ from run to run."""
    x0, y0, x1, y1 = (float(v) for v in page.mediabox)
    width, height = abs(x1 - x0), abs(y1 - y0)
    rotation = int(page.get("/Rotate", 0)) % 360
    matrix, (vw, vh) = {
        0: ((1, 0, 0, 1, x0, y0), (width, height)),
        90: ((0, 1, -1, 0, x1, y0), (height, width)),
        180: ((-1, 0, 0, -1, x1, y1), (width, height)),
        270: ((0, -1, 1, 0, x0, y1), (height, width)),
    }[rotation]
    form = pdf.make_stream(
        content,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=[0, 0, vw, vh],
        Resources=pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=font),
            ExtGState=pikepdf.Dictionary(
                GS0=pikepdf.Dictionary(ca=alpha, CA=alpha, Type=pikepdf.Name.ExtGState)
            ),
        ),
    )
    # The page's own copy of its resources: they may be inherited and shared with other pages,
    # and each page gets a different stamp.
    resources = pikepdf.Dictionary({k: v for k, v in page.resources.items()})
    xobjects = pikepdf.Dictionary({k: v for k, v in resources.get("/XObject", {}).items()})
    resources["/XObject"] = xobjects
    page.obj["/Resources"] = resources
    n = 0
    while f"/AtelierStamp{n}" in xobjects:
        n += 1
    name = f"/AtelierStamp{n}"
    xobjects[name] = form
    cm = " ".join(f"{v:g}" for v in matrix)
    page.contents_add(pdf.make_stream(b"q\n"), prepend=True)  # isolate the page's own state
    page.contents_add(pdf.make_stream(b"Q\n"))
    page.contents_add(pdf.make_stream(f"q {cm} cm {name} Do Q\n".encode()))


def _stamp_content(p: StampParams, font: embed.Embedded, text: str, w: float, h: float) -> bytes:
    r, g, b, _ = rgba(p.color)
    tw = font.width(text, p.size)
    across, down = (p.position.rpartition("_")[2], p.position.partition("_")[0])
    x = {"left": p.margin, "center": (w - tw) / 2, "right": w - p.margin - tw}[
        across if "_" in p.position else "center"
    ]
    y = {"top": h - p.margin - p.size, "center": (h - p.size * 0.7) / 2, "bottom": p.margin}[
        down if "_" in p.position else "center"
    ]
    a = math.radians(p.angle)
    cos, sin = math.cos(a), math.sin(a)
    cx, cy = x + tw / 2, y + p.size * 0.35  # rotate about the middle of the text
    return (
        (
            f"q /GS0 gs {r / 255:.4f} {g / 255:.4f} {b / 255:.4f} rg "
            f"{cos:.6f} {sin:.6f} {-sin:.6f} {cos:.6f} {cx:.3f} {cy:.3f} cm "
            f"BT /F1 {p.size:g} Tf {-tw / 2:.3f} {-p.size * 0.35:.3f} Td "
        ).encode()
        + font.encode(text)
        + b" Tj ET Q"
    )


@pdf_op(
    name="pdf.stamp",
    params=StampParams,
    description="stamp text on pages: header/footer, page numbers, Bates numbers, watermark",
)
def stamp(ctx: OpContext, inputs: Sequence[Artifact], p: StampParams) -> Artifact:
    """Draw `text` on the chosen pages at a position. `{page}` is the page number, `{pages}` the
    page count and `{bates}` a running number that starts at `bates_start` (a legal Bates stamp,
    e.g. `ACME-{bates}`). For a watermark use position `center`, a large size, low opacity and
    angle 45. The text is set in the bundled Inter, embedded in the file, and stays selectable.
    Keep it inside the margins the page needs: the safe-margin check does not apply to PDFs."""
    pdf = pdfio.open_pdf(inputs[0].path)
    count = len(pdf.pages)
    targets: list[tuple[int, str]] = []
    for i in expand_pages(p.pages, count):
        text = p.text.replace("{pages}", str(count)).replace("{page}", str(i + 1))
        targets.append((i, text.replace("{bates}", str(p.bates_start + i).zfill(p.bates_digits))))
    if not targets:
        raise OpError("no pages were selected")
    font = embed.embed(pdf, "".join(text for _, text in targets))  # one subset for the whole op
    _, _, _, alpha = rgba(p.color)
    for i, text in targets:
        w, h = _visible_size(pdf.pages[i])
        if font.width(text, p.size) > w:
            raise OpError(
                f"the stamp is {font.width(text, p.size):.0f}pt wide but page {i + 1} is only "
                f"{w:.0f}pt: use a smaller size or shorter text"
            )
        content = _stamp_content(p, font, text, w, h)
        _place(pdf, pdf.pages[i], content, p.opacity * alpha / 255, font.font)
    return finish(ctx, pdf, stamped=len(targets))


# -- form --------------------------------------------------------------------------------------


class FormParams(Params):
    fields: dict[str, str | bool] = Field(
        min_length=1, description="value by field name; true/false ticks a checkbox"
    )
    flatten: bool = Field(
        False, description="make the values part of the page (no longer editable)"
    )


@pdf_op(
    name="pdf.form",
    params=FormParams,
    description="fill the fields of a PDF form, optionally flatten it",
)
def form(ctx: OpContext, inputs: Sequence[Artifact], p: FormParams) -> Artifact:
    """Set form fields by name (see the file's `fields` in inspect_media). Text fields take
    text, checkboxes take true/false. With `flatten` the values are drawn into the page and the
    form goes away, which is what you want before sending or merging the file."""
    pdf = pdfio.open_pdf(inputs[0].path)
    acro = pdf.acroform
    if not acro.exists:
        raise OpError("this PDF has no form fields")
    by_name = {f.fully_qualified_name: f for f in acro.fields}
    unknown = [n for n in p.fields if n not in by_name]
    if unknown:
        raise OpError(
            f"no such field: {', '.join(repr(u) for u in unknown)}. "
            f"The form has: {', '.join(sorted(by_name))}"
        )
    for name, value in p.fields.items():
        field = by_name[name]
        if field.is_checkbox:
            if not isinstance(value, bool):
                raise OpError(f"{name!r} is a checkbox: give true or false")
            field.set_value(pikepdf.Name.Yes if value else pikepdf.Name.Off)
        else:
            field.set_value(str(value))
    acro.generate_appearances_if_needed()
    if p.flatten:
        pdf.flatten_annotations("all")
        if "/AcroForm" in pdf.Root:
            del pdf.Root["/AcroForm"]
    return finish(ctx, pdf)


# -- redact ------------------------------------------------------------------------------------


class RedactParams(Params):
    terms: list[str] = Field(
        default_factory=list, max_length=MAX_TERMS, description="exact texts to remove (any case)"
    )
    patterns: list[str] = Field(
        default_factory=list, max_length=20, description="regular expressions to remove"
    )
    pages: str = Field("all", description="which pages to search; default all")
    dpi: int = Field(150, ge=72, le=300, description="resolution of the redacted pages")
    padding: float = Field(1.5, ge=0, le=10, description="points added around each match")
    require_match: bool = Field(True, description="fail if nothing was found to redact")

    @field_validator("pages")
    @classmethod
    def _valid(cls, v: str) -> str:
        return check_pages(v)

    @field_validator("terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        if any(not t.strip() for t in v):
            raise ValueError("a term to redact cannot be empty")
        return v

    @field_validator("patterns")
    @classmethod
    def _patterns(cls, v: list[str]) -> list[str]:
        for pat in v:
            if len(pat) > MAX_PATTERN:
                raise ValueError(f"a pattern is limited to {MAX_PATTERN} characters")
            try:
                re.compile(pat)
            except re.error as e:
                raise ValueError(f"{pat!r} is not a valid pattern: {e}") from None
        return v


def _regex(p: RedactParams) -> re.Pattern[str]:
    parts = [re.escape(t) for t in p.terms] + [f"(?:{x})" for x in p.patterns]
    return re.compile("|".join(parts), re.IGNORECASE if p.terms and not p.patterns else 0)


def _picture_page(pdf: pikepdf.Pdf, image: Image.Image, w: float, h: float) -> pikepdf.Page:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=88, optimize=False)
    xobject = pdf.make_stream(
        buf.getvalue(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=image.width,
        Height=image.height,
        ColorSpace=pikepdf.Name.DeviceRGB,
        BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
    content = pdf.make_stream(f"q {w:.3f} 0 0 {h:.3f} 0 0 cm /Im0 Do Q".encode())
    return pikepdf.Page(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, w, h],
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobject)),
            Contents=content,
        )
    )


def _scrub_document(pdf: pikepdf.Pdf, pattern: re.Pattern[str]) -> None:
    """Titles, authors and bookmarks can repeat what was redacted: drop those that match."""
    for key in list(pdf.docinfo.keys()):
        if pattern.search(str(pdf.docinfo[key])):
            del pdf.docinfo[key]
    if "/Outlines" in pdf.Root:
        with pdf.open_outline() as outline:
            if any(pattern.search(item.title or "") for item in _walk_outline(outline.root)):
                outline.root.clear()


def _walk_outline(items: Any) -> Any:
    for item in items:
        yield item
        yield from _walk_outline(item.children)


@pdf_op(
    name="pdf.redact",
    params=RedactParams,
    description="permanently remove texts from a PDF (the text is gone, not covered)",
    determinism=Determinism.PERCEPTUAL,
)
def redact(ctx: OpContext, inputs: Sequence[Artifact], p: RedactParams) -> Artifact:
    """Find every match of `terms` (exact, any case) and `patterns` (regular expressions) on the
    chosen pages and black them out. Each affected page is re-drawn as a picture, so the text
    underneath does not exist in the result: it cannot be selected, searched or extracted. The
    other pages are untouched. The result records what was found; the `redaction_complete`
    check reads the finished file to confirm nothing is left."""
    if not p.terms and not p.patterns:
        raise OpError("give at least one term or pattern to redact")
    pattern = _regex(p)
    src = inputs[0].path
    pdf = pdfio.open_pdf(src)
    render = pdfdoc.open_render(src)
    hits_per_page: dict[int, int] = {}
    try:
        for i in expand_pages(p.pages, len(pdf.pages)):
            boxes = pdfdoc.find_boxes(render, i, pattern)
            if not boxes:
                continue
            hits_per_page[i] = len(boxes)
            scale = p.dpi / 72
            image = pdfdoc.render_page(render, i, p.dpi)
            page = render[i]
            try:
                w, h = page.get_size()
            finally:
                page.close()
            paint = Image.new("RGB", image.size, "black")
            for left, bottom, right, top in boxes:
                box = (
                    round((left - p.padding) * scale),
                    round((h - top - p.padding) * scale),
                    round((right + p.padding) * scale),
                    round((h - bottom + p.padding) * scale),
                )
                image.paste(
                    paint.crop((0, 0, max(1, box[2] - box[0]), max(1, box[3] - box[1]))), box[:2]
                )
            new = _picture_page(pdf, image, w, h)
            pdf.pages.insert(i, new)
            del pdf.pages[i + 1]
    finally:
        render.close()
    if not hits_per_page and p.require_match:
        raise OpError(
            "nothing matched: the search found none of the terms or patterns on the chosen "
            "pages (text in scanned pages is a picture and cannot be searched)"
        )
    _scrub_document(pdf, pattern)
    result = finish(
        ctx,
        pdf,
        redaction={
            "terms": len(p.terms),
            "patterns": len(p.patterns),
            "pages": sorted(i + 1 for i in hits_per_page),
            "hits": sum(hits_per_page.values()),
            "leaked": [],
        },
    )
    leaked = sorted({m.group(0) for m in pattern.finditer(pdfdoc.all_text(result.path))})
    meta = dict(result.meta)
    meta["redaction"] = {**meta["redaction"], "leaked": leaked[:20]}
    return Artifact(result.path, PDF, ".pdf", meta)


# -- compress ----------------------------------------------------------------------------------


class CompressParams(Params):
    quality: int = Field(70, ge=20, le=95, description="JPEG quality for pictures in the file")
    max_side: int = Field(1600, ge=200, le=8000, description="longest side of a picture, in px")


@pdf_op(
    name="pdf.compress",
    params=CompressParams,
    description="shrink a PDF: recompress and downscale its pictures",
    determinism=Determinism.PERCEPTUAL,
    also=("pillow", "icc"),
)
def compress(ctx: OpContext, inputs: Sequence[Artifact], p: CompressParams) -> Artifact:
    """Recompress the pictures in the file as JPEG at `quality` and shrink any larger than
    `max_side` pixels. Text and vector drawing are untouched. Pictures with transparency or an
    unusual color space are left as they are. If the result would be larger it is not used."""
    pdf = pdfio.open_pdf(inputs[0].path)
    before = inputs[0].path.stat().st_size
    changed = 0
    seen: set[int] = set()
    for page in pdf.pages:
        for name in list(page.images.keys()):
            image = page.images[name]
            key = image.objgen[0]
            if key in seen:
                continue
            seen.add(key)
            changed += _recompress(image, p)
    result = finish(ctx, pdf, images_recompressed=changed)
    if result.path.stat().st_size >= before:  # never hand back a bigger file
        keep = pdfio.open_pdf(inputs[0].path)
        result = finish(ctx, keep, images_recompressed=0)
    return result


def _recompress(obj: pikepdf.Object, p: CompressParams) -> int:
    if "/SMask" in obj or "/Mask" in obj:
        return 0
    try:
        with pikepdf.PdfImage(cast(pikepdf.Stream, obj)).as_pil_image() as im:
            im.load()
            if im.mode not in ("RGB", "L"):
                return 0
            if max(im.size) > p.max_side:
                ratio = p.max_side / max(im.size)
                im = im.resize(
                    (round(im.width * ratio), round(im.height * ratio)), Image.Resampling.LANCZOS
                )
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=p.quality)
    except Exception:  # an image we cannot decode is left exactly as it is
        return 0
    data = buf.getvalue()
    if len(data) >= len(obj.read_raw_bytes()):
        return 0
    obj.write(data, filter=pikepdf.Name.DCTDecode)
    obj.Width, obj.Height = im.width, im.height
    obj.ColorSpace = pikepdf.Name.DeviceRGB if im.mode == "RGB" else pikepdf.Name.DeviceGray
    obj.BitsPerComponent = 8
    for key in ("/DecodeParms", "/Decode"):
        if key in obj:
            del obj[key]
    return 1


# -- convert (LibreOffice) ---------------------------------------------------------------------


class ConvertParams(Params):
    pass


OFFICE_TYPES = frozenset({MediaType.DOCX, MediaType.PPTX, MediaType.XLSX})


@op(
    name="doc.convert",
    version="1.0.0",
    inputs=[AnyOf(OFFICE_TYPES)],
    output=PDF,
    determinism=Determinism.PERCEPTUAL,
    params=ConvertParams,
    summary=pdf_summary,
    description="turn a Word, PowerPoint or Excel file into a PDF (LibreOffice)",
    resources=("libreoffice", "office", "fonts", "pdf"),
)
def convert(ctx: OpContext, inputs: Sequence[Artifact], p: ConvertParams) -> Artifact:
    """Render the document to PDF the way it prints, with fonts embedded. Layout uses the
    bundled fonts only, so a document that names another font (Calibri, Arial) is set in the
    closest bundled one. Check the result with `preview` when layout matters."""
    from atelier.docs import convert as libreoffice
    from atelier.docs import package

    src = inputs[0]
    package.check(src.path)
    made = libreoffice.convert(src.path, src.ext, "pdf", ctx.work_dir)
    pdf = pdfio.open_pdf(made)
    return finish(ctx, pdf)
