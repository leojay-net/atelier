"""PDF files: safe to open, deterministic to save, and a tiny builder for text pages.

pikepdf (qpdf) parses and writes; pypdfium2 renders and extracts text. Both see untrusted
files. A PDF may not be encrypted (we would have to guess a password), and JavaScript, launch
actions and embedded files are removed on the way out (docs/10-security.md) unless asked for.
Saving derives the document ID from the content and drops every date, so the same edit of the
same file gives the same bytes.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf

from atelier.ops.registry import OpError

MAX_PAGES = 2000
MAX_BYTES = 256 * 1024 * 1024
PRODUCER = "Atelier"
_TRAILER_KEEP = ("/Root", "/Info", "/Size", "/Encrypt", "/Prev")
_ACTIVE = ("/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE")


def open_pdf(path: Path) -> pikepdf.Pdf:
    """Open a PDF for editing, or raise OpError saying what is wrong with it."""
    if path.stat().st_size > MAX_BYTES:
        raise OpError(f"the PDF is larger than {MAX_BYTES // 2**20} MB")
    try:
        pdf = pikepdf.Pdf.open(path)
    except pikepdf.PasswordError:
        raise OpError(
            "the PDF is password protected; remove the password and upload it again"
        ) from None
    except (pikepdf.PdfError, OSError) as e:
        raise OpError(f"not a readable PDF: {str(e).splitlines()[0][:160]}") from None
    if len(pdf.pages) > MAX_PAGES:
        raise OpError(f"the PDF has {len(pdf.pages)} pages (limit {MAX_PAGES})")
    return pdf


def strip_active_content(pdf: pikepdf.Pdf) -> int:
    """Remove JavaScript, launch/submit actions and embedded files; returns how many were cut."""
    removed = 0
    root = pdf.Root
    names = root.get("/Names")
    if names is not None:
        for key in ("/JavaScript", "/EmbeddedFiles"):
            if key in names:
                del names[key]
                removed += 1
    for key in ("/OpenAction", "/AA"):
        if key in root:
            del root[key]
            removed += 1
    for page in pdf.pages:
        if "/AA" in page:
            del page["/AA"]
            removed += 1
        for annot in page.get("/Annots", []):
            for key in ("/A", "/AA"):
                action = annot.get(key)
                if action is None:
                    continue
                kind = action.get("/S") if isinstance(action, pikepdf.Dictionary) else None
                if key == "/AA" or str(kind) in _ACTIVE:
                    del annot[key]
                    removed += 1
            if annot.get("/Subtype") == "/FileAttachment" and "/FS" in annot:
                del annot["/FS"]
                removed += 1
    return removed


def normalize_info(pdf: pikepdf.Pdf) -> None:
    """No dates, a fixed producer, and no XMP packet (which repeats them)."""
    info = pdf.docinfo
    for key in ("/CreationDate", "/ModDate", "/Creator"):
        if key in info:
            del info[key]
    info["/Producer"] = PRODUCER
    if "/Metadata" in pdf.Root:
        del pdf.Root["/Metadata"]
    # A file keeps the first half of its ID through an edit, and converters make that half random
    # (LibreOffice also writes a /DocChecksum of its own): keep only the trailer keys a reader
    # needs and derive the ID from the content.
    for key in list(pdf.trailer.keys()):
        if key not in _TRAILER_KEEP:
            del pdf.trailer[key]


def save(pdf: pikepdf.Pdf, dst: Path, *, linearize: bool = False) -> None:
    """Write `pdf` deterministically (content-derived ID, no dates, compressed streams)."""
    normalize_info(pdf)
    pdf.save(
        dst,
        deterministic_id=True,
        linearize=linearize,
        compress_streams=True,
        object_stream_mode=pikepdf.ObjectStreamMode.generate,
        recompress_flate=False,
    )


# -- a tiny builder for text pages (fixtures, stamps) ----------------------------------------


def escape(text: str) -> bytes:
    """Text as a PDF string literal body in WinAnsi (characters outside it become '?')."""
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def text_stream(
    lines: list[str], x: float, y: float, size: float = 12, leading: float | None = None,
    font: str = "F1",
) -> bytes:  # fmt: skip
    """Content stream operators drawing `lines` from (x, y) downwards."""
    lead = leading if leading is not None else size * 1.3
    out = [b"BT", f"/{font} {size:g} Tf {lead:g} TL {x:g} {y:g} Td".encode()]
    for i, line in enumerate(lines):
        out.append(b"(" + escape(line) + (b") Tj" if i == len(lines) - 1 else b") Tj T*"))
    out.append(b"ET")
    return b"\n".join(out)


def helvetica(pdf: pikepdf.Pdf, bold: bool = False) -> pikepdf.Object:
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/Helvetica-Bold" if bold else "/Helvetica"),
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )


def build_text_pdf(
    pages: list[list[str]], size: tuple[float, float] = (612, 792), font_size: float = 12
) -> pikepdf.Pdf:
    """A PDF with one text page per entry (Helvetica, not embedded): for tests and fixtures."""
    pdf = pikepdf.Pdf.new()
    font = helvetica(pdf)
    for lines in pages:
        content = pdf.make_stream(text_stream(lines, 72, size[1] - 72, font_size))
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, size[0], size[1]],
            Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
            Contents=content,
        )
        pdf.pages.append(pikepdf.Page(page))
    return pdf
