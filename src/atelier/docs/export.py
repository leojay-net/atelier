"""Final output of a document: `export` with a preset (docs/03 §3.4, docs/05 §5.5).

Exporting is where a document becomes a deliverable: metadata is normalized, active content is
gone, and the preset's options are applied (image recompression and linearization for a PDF, a
title for an Office file). Everything before it may be an intermediate; the result of this step is
what the checks judge and what the user receives.
"""

from __future__ import annotations

from typing import Any

import pikepdf
from pydantic import Field

from atelier.docs import package, pdfdoc, pdfio
from atelier.docs.doc_ops import describe
from atelier.docs.pdf_ops import CompressParams, _recompress
from atelier.media import MediaType
from atelier.ops.registry import Artifact, OpContext, Params

PDF_KIND = "export.pdf"
DOCX_KIND = "export.docx"
XLSX_KIND = "export.xlsx"
PPTX_KIND = "export.pptx"


class PdfExportSettings(Params):
    compress: bool = Field(False, description="recompress and downscale the pictures")
    quality: int = Field(70, ge=20, le=95, description="JPEG quality when compressing")
    max_side: int = Field(
        1600, ge=200, le=8000, description="longest picture side when compressing"
    )
    linearize: bool = Field(False, description="write for fast first-page viewing on the web")
    flatten_forms: bool = Field(False, description="make form values part of the page")
    title: str | None = Field(None, max_length=200, description="document title")
    checks: list[str] = Field(
        default_factory=list, description="checks to run besides the defaults"
    )


class OfficeExportSettings(Params):
    title: str | None = Field(None, max_length=200, description="document title")
    checks: list[str] = Field(
        default_factory=list, description="checks to run besides the defaults"
    )


def export_document(
    ctx: OpContext, src: Artifact, preset: str, version: int, settings: dict[str, Any]
) -> Artifact:
    info = {"preset": preset, "version": version}
    if src.media is MediaType.PDF:
        s = PdfExportSettings.model_validate(settings)
        pdf = pdfio.open_pdf(src.path)
        pdfio.strip_active_content(pdf)
        recompressed = 0
        if s.flatten_forms:
            pdf.flatten_annotations("all")
            if "/AcroForm" in pdf.Root:
                del pdf.Root["/AcroForm"]
        if s.compress:
            p = CompressParams(quality=s.quality, max_side=s.max_side)
            seen: set[int] = set()
            for page in pdf.pages:
                for name in list(page.images.keys()):
                    obj = page.images[name]
                    if obj.objgen[0] not in seen:
                        seen.add(obj.objgen[0])
                        recompressed += _recompress(obj, p)
        if s.title is not None:
            pdf.docinfo["/Title"] = pikepdf.String(s.title)
        out = ctx.output_path(".pdf")
        pdfio.save(pdf, out, linearize=s.linearize)
        pdf.close()
        meta = {**pdfdoc.describe(out), "images_recompressed": recompressed}
        return Artifact(
            out, MediaType.PDF, ".pdf", {**meta, "export": {**info, "checks": s.checks}}
        )
    o = OfficeExportSettings.model_validate(settings)
    package.check(src.path)
    out = ctx.output_path(src.ext)
    package.write(src.path, out, title=o.title)
    return Artifact(
        out,
        src.media,
        src.ext,
        {**describe(out, src.media), "export": {**info, "checks": o.checks}},
    )
