"""`pptx.create`: a new presentation from pictures and an outline (no template needed).

The deck is 16:9 with real, editable text: a title slide (the first picture full-bleed with a
soft dark gradient baked in, big title and subtitle) and content slides (title and bullet
points on a clean panel, the picture on the right). Speaker notes carry what to say. Pictures
are cropped and encoded here with Pillow, and the package is written through `slides.save`,
so equal inputs give the same file.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Literal

from PIL import Image, ImageOps
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR
from pptx.util import Emu, Pt
from pydantic import BaseModel, ConfigDict, Field

from atelier.docs import slides
from atelier.docs.doc_ops import RESOURCES, describe, doc_summary
from atelier.media import MediaType
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op
from atelier.spec.types import Color, rgba

MAX_DECK_SLIDES = 40
W, H = Emu(12192000), Emu(6858000)  # 13.333 x 7.5 in, 16:9
FONT = "Calibri"
WHITE, SOFT = (255, 255, 255), (226, 232, 240)
THEMES = {  # panel, title, body
    "dark": ((15, 23, 42), (255, 255, 255), (203, 213, 225)),
    "light": ((248, 250, 252), (15, 23, 42), (51, 65, 85)),
}


class DeckSlide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str = Field(min_length=1, max_length=120)
    body: list[str] = Field(default_factory=list, max_length=8, description="bullet lines")
    notes: str = Field("", max_length=4000, description="speaker notes: what to say")


class CreateDeckParams(Params):
    slides: list[DeckSlide] = Field(
        min_length=1, max_length=MAX_DECK_SLIDES, description="first is the title slide"
    )
    theme: Literal["dark", "light"] = Field("dark", description="colors of the text panels")
    accent: Color = Field("#3b82f6ff", description="accent bar color")


def _picture(path: object, aspect: float, darken_bottom: bool = False) -> io.BytesIO:
    """The picture center-cropped to `aspect`, at most 1920 px wide, as JPEG bytes."""
    with Image.open(path) as im:  # type: ignore[arg-type]
        im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    if w / h > aspect:
        nw = round(h * aspect)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / aspect)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    if im.width > 1920:
        im = im.resize((1920, round(1920 / aspect)), Image.Resampling.LANCZOS)
    if darken_bottom:  # a soft gradient so white title text reads on any photo
        shade = Image.linear_gradient("L").resize(im.size).point(lambda v: int(v * 0.78))
        im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im, shade)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88, optimize=False)
    out.seek(0)
    return out


def _text(
    slide: object,
    box_emu: tuple[int, int, int, int],
    lines: list[str],
    size: int,
    color: tuple[int, int, int],
    bold: bool = False,
    bullets: bool = False,
) -> None:
    x, y, w, h = (Emu(v) for v in box_emu)
    box = slide.shapes.add_textbox(x, y, w, h)  # type: ignore[attr-defined]
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    for i, line in enumerate(lines):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        run = para.add_run()
        run.text = f"•  {line}" if bullets else line
        run.font.size, run.font.bold, run.font.name = Pt(size), bold, FONT
        run.font.color.rgb = RGBColor(*color)
        para.space_after = Pt(size * 0.6)


@op(
    name="pptx.create",
    version="1.0.0",
    inputs=[MediaType.IMAGE],
    variadic=True,
    output=MediaType.PPTX,
    determinism=Determinism.BIT_EXACT,
    params=CreateDeckParams,
    summary=doc_summary,
    description="new presentation: a picture, title and bullets per slide",
    resources=RESOURCES,
)
def create(ctx: OpContext, inputs: Sequence[Artifact], p: CreateDeckParams) -> Artifact:
    """A new 16:9 PowerPoint deck. Slide 1 is the title slide (its picture full-bleed, title
    and the first body line as subtitle); every other slide shows its title and bullet `body`
    lines on a `theme` panel with an `accent` bar and its picture on the right half. Pictures
    go to slides in order (reused from the start if there are fewer pictures than slides).
    `notes` become speaker notes. Text stays editable."""
    panel, title_c, body_c = THEMES[p.theme]
    accent = rgba(p.accent)[:3]
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]
    for i, item in enumerate(p.slides):
        pic = inputs[i % len(inputs)].path  # fewer pictures than slides: reuse them in order
        slide = prs.slides.add_slide(blank)
        if i == 0:
            slide.shapes.add_picture(_picture(pic, W / H, darken_bottom=True), 0, 0, W, H)
            _text(slide, (640080, 3931920, 10972800, 1554480), [item.title], 48, WHITE, True)
            if item.body:
                _text(slide, (640080, 5394960, 10972800, 914400), item.body[:1], 22, SOFT)
        else:
            half = W // 2
            bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, half, H)
            bg.fill.solid()
            bg.fill.fore_color.rgb = RGBColor(*panel)
            bg.line.fill.background()
            bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 640080, 822960, 731520, 73152)
            bar.fill.solid()
            bar.fill.fore_color.rgb = RGBColor(*accent)
            bar.line.fill.background()
            slide.shapes.add_picture(_picture(pic, (W - half) / H), half, 0, W - half, H)
            _text(
                slide, (640080, 1051560, half - 1097280, 1371600), [item.title], 34, title_c, True
            )
            if item.body:
                box = (640080, 2560320, half - 1097280, 3931920)
                _text(slide, box, item.body, 18, body_c, bullets=True)
        if item.notes:
            slide.notes_slide.notes_text_frame.text = item.notes
    if len(prs.slides) != len(p.slides):  # pragma: no cover - a python-pptx surprise
        raise OpError("the deck could not be built")
    out = ctx.output_path(".pptx")
    slides.save(prs, out)
    return Artifact(out, MediaType.PPTX, ".pptx", describe(out, MediaType.PPTX))
