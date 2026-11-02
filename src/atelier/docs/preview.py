"""Document previews for the escalation ladder (docs/06 §6.2): pages as small pictures.

`preview.pages` renders pages of a PDF (with pypdfium2, a fixed renderer) into one labelled
contact sheet, or one larger page. Word, PowerPoint and Excel files are converted to PDF first
(`doc.convert`), so a preview shows them the way they print.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from atelier.docs import pdfdoc, pdfio
from atelier.media import MediaType
from atelier.ops.image.grid import MAX_CELLS, build_grid
from atelier.ops.preview import _encode, preview_summary
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op


class PagesParams(Params):
    count: int = Field(6, ge=1, le=MAX_CELLS, description="how many pages, from the first")
    at: list[int] | None = Field(None, description="exact pages (1-based) instead of the first N")
    columns: int | None = Field(None, ge=1, le=8, description="null = square-ish")
    cell_width: int = Field(256, ge=96, le=900, description="px per page")


@op(
    name="preview.pages",
    version="1.0.0",
    inputs=[MediaType.PDF],
    output=MediaType.IMAGE,
    determinism=Determinism.PERCEPTUAL,
    params=PagesParams,
    summary=preview_summary,
    description="contact sheet of labelled pages from a PDF (L2)",
    hidden=True,
    resources=("pdf", "pillow", "icc", "fonts"),
)
def pages(ctx: OpContext, inputs: Sequence[Artifact], p: PagesParams) -> Artifact:
    """One image of the chosen pages, each labelled with its number, at a size that keeps the
    sheet cheap to look at."""
    src = inputs[0].path
    with pdfio.open_pdf(src) as pdf:
        total = len(pdf.pages)
    wanted = list(p.at) if p.at is not None else list(range(1, min(p.count, total) + 1))
    if not wanted or any(n < 1 or n > total for n in wanted):
        bad = next((n for n in wanted if n < 1 or n > total), 0)
        raise OpError(f"page {bad} does not exist: the document has {total} page(s)")
    if len(wanted) > MAX_CELLS:
        raise OpError(f"at most {MAX_CELLS} pages fit in one preview")
    doc = pdfdoc.open_render(src)
    try:
        first_w, first_h = doc[wanted[0] - 1].get_size()
        dpi = max(24, round(72 * p.cell_width / max(first_w, 1)))
        images = [pdfdoc.render_page(doc, n - 1, dpi) for n in wanted]
    finally:
        doc.close()
    sheet = build_grid(
        images,
        columns=p.columns if p.columns is not None else (1 if len(images) == 1 else None),
        cell_width=p.cell_width,
        aspect=first_w / first_h,
        fit="contain",
        gap=0.03,
        background="#e6e6e6ff",
        labels=[f"p{n}" for n in wanted] if len(images) > 1 else None,
    )
    return _encode(ctx, sheet)
