"""PowerPoint slide ops (docs/03 §3.4): add, duplicate, delete and move slides."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any, Literal

from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pydantic import Field, model_validator

from atelier.docs import slides
from atelier.docs.doc_ops import describe, doc_op
from atelier.media import MediaType
from atelier.ops.registry import Artifact, OpContext, OpError, Params

MAX_ITEMS = 60
MAX_SLIDES = 500


class SlideItem(Params):
    layout: str = Field("Title and Content", description="a layout name from the deck")
    title: str = Field("", max_length=300, description="text for the title box")
    body: list[str] = Field(
        default_factory=list, max_length=40, description="one paragraph per entry in the body box"
    )


class SlideParams(Params):
    action: Literal["add", "duplicate", "delete", "move"] = Field(description="what to do")
    items: list[SlideItem] = Field(
        default_factory=list, max_length=MAX_ITEMS, description="add: the slides to create"
    )
    index: int | None = Field(
        None, ge=1, description="duplicate/delete/move: the slide (1-based); add: insert before it"
    )
    to: int | None = Field(None, ge=1, description="move: the position it ends up at (1-based)")
    copies: int = Field(1, ge=1, le=20, description="duplicate: how many copies")

    @model_validator(mode="after")
    def _shape(self) -> SlideParams:
        if self.action == "add" and not self.items:
            raise ValueError("add needs items: the slides to create")
        if self.action in ("duplicate", "delete", "move") and self.index is None:
            raise ValueError(f"{self.action} needs index: which slide (1-based)")
        if self.action == "move" and self.to is None:
            raise ValueError("move needs to: the position the slide ends up at")
        return self


def _layout(prs: Any, name: str) -> Any:
    for layout in prs.slide_layouts:
        if layout.name == name:
            return layout
    raise OpError(
        f"no layout named {name!r}. The deck has: {', '.join(x.name for x in prs.slide_layouts)}"
    )


def _reorder(prs: Any, from_index: int, to_index: int) -> None:
    ids = prs.slides._sldIdLst
    item = list(ids)[from_index]
    ids.remove(item)
    ids.insert(to_index, item)


def _fill(slide: Any, item: SlideItem) -> None:
    if item.title:
        if slide.shapes.title is None:
            raise OpError(f"the layout {item.layout!r} has no title box")
        slide.shapes.title.text = item.title
    if item.body:
        body = next(
            (s for s in slide.placeholders if s.placeholder_format.idx == 1 and s.has_text_frame),
            None,
        )
        if body is None:
            raise OpError(f"the layout {item.layout!r} has no body box for the text")
        body.text_frame.text = item.body[0]
        for line in item.body[1:]:
            body.text_frame.add_paragraph().text = line


def _duplicate(prs: Any, index: int) -> None:
    source = prs.slides[index]
    new = prs.slides.add_slide(source.slide_layout)
    for shape in list(new.shapes):  # drop the placeholders the layout created
        shape._element.getparent().remove(shape._element)
    for shape in source.shapes:
        element = copy.deepcopy(shape._element)
        for blip in element.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"):
            rid = blip.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
            )
            if rid:
                image = source.part.related_part(rid)
                blip.set(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed",
                    new.part.relate_to(image, RT.IMAGE),
                )
        if any(el.tag.endswith(("}chart", "}oleObj")) for el in element.iter()):
            raise OpError("this slide has a chart or embedded object, which cannot be duplicated")
        new.shapes._spTree.append(element)
    _reorder(prs, len(prs.slides) - 1, index + 1)


@doc_op(
    name="pptx.slide",
    params=SlideParams,
    description="add, duplicate, delete or move slides in a PowerPoint file",
    inputs=(MediaType.PPTX,),
)
def slide_op(ctx: OpContext, inputs: Sequence[Artifact], p: SlideParams) -> Artifact:
    """Edit the slide list. `add` creates slides from the deck's layouts (`items`: layout, title,
    body lines), at the end or before slide `index`. `duplicate` copies slide `index` right after
    itself, `copies` times. `delete` removes slide `index`. `move` puts slide `index` at position
    `to`. Slides are numbered from 1. Fill the new slides' text with `doc.fill`."""
    src = inputs[0]
    prs = slides.open_pptx(src.path)
    count = len(prs.slides)

    def check(n: int, what: str) -> int:
        if not 1 <= n <= count:
            raise OpError(f"{what} {n} is out of range: the deck has {count} slide(s)")
        return n - 1

    if p.action == "add":
        if count + len(p.items) > MAX_SLIDES:
            raise OpError(f"a deck has at most {MAX_SLIDES} slides")
        before = check(p.index, "slide") if p.index else count
        for offset, item in enumerate(p.items):
            slide = prs.slides.add_slide(_layout(prs, item.layout))
            _fill(slide, item)
            _reorder(prs, len(prs.slides) - 1, before + offset)
    elif p.action == "duplicate":
        i = check(p.index or 1, "slide")
        if count + p.copies > MAX_SLIDES:
            raise OpError(f"a deck has at most {MAX_SLIDES} slides")
        for _ in range(p.copies):
            _duplicate(prs, i)
    elif p.action == "delete":
        i = check(p.index or 1, "slide")
        if count == 1:
            raise OpError("a deck needs at least one slide")
        ids = prs.slides._sldIdLst
        item = list(ids)[i]
        prs.part.drop_rel(item.rId)
        ids.remove(item)
    else:
        i = check(p.index or 1, "slide")
        _reorder(prs, i, check(p.to or 1, "position"))
    out = ctx.output_path(src.ext)
    slides.save(prs, out)
    return Artifact(out, src.media, src.ext, describe(out, src.media))
