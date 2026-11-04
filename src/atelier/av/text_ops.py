"""Titles and captions on video (docs/03 §3.4).

Text is drawn by the same typesetter as `image.text` (bundled fonts, wrapping, outline, plate)
into a transparent PNG per cue, and ffmpeg only overlays that picture during the cue's time
window. No user text ever reaches a filter string, and the result does not depend on whether
this ffmpeg was built with drawtext or libass.

Sizes are fractions of the *shorter* side of the frame, so the same style reads the same on a
vertical Reel and on a landscape clip. Every cue of a caption track is fitted to the same box
at the same maximum size: short cues share one size, only a cue too long for the box shrinks.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from atelier import fonts
from atelier.av.graph import Graph, Source, num
from atelier.av.ops import video_op
from atelier.av.subtitles import Cue, parse
from atelier.av.textlayer import Layer, render_layer
from atelier.media import MediaType
from atelier.ops.image.text import TextStyle
from atelier.ops.registry import OpError, Params
from atelier.spec.types import Color, Fraction, Timecode

MAX_CUES = 300  # every cue is one overlay input; more than this is a different kind of job
Position = Literal["top", "upper_safe", "middle", "lower_safe", "bottom"]
StyleName = Literal["clean", "bold_pop", "minimal_lower"]

# name -> (box [x, y, w, h] as fractions of the frame, vertical alignment inside it).
# The *_safe positions keep clear of the platform UI zones in the 9:16 presets
# (top 14 %, bottom 20 %, sides 6 %); "top" and "bottom" hug the edges and will trip the
# safe_margins check on those presets, which is the point of having both. The boxes sit a little
# inside the zones because an outline or shadow is drawn beyond the box the words are laid out in.
POSITIONS: dict[str, tuple[tuple[float, float, float, float], str]] = {
    "top": ((0.09, 0.05, 0.82, 0.22), "top"),
    "upper_safe": ((0.09, 0.16, 0.82, 0.20), "top"),
    "middle": ((0.09, 0.39, 0.82, 0.22), "middle"),
    "lower_safe": ((0.09, 0.56, 0.82, 0.21), "bottom"),
    "bottom": ((0.09, 0.72, 0.82, 0.22), "bottom"),
}

# name -> (default size as a fraction of the shorter side, TextStyle fields)
STYLES: dict[str, tuple[float, dict[str, Any]]] = {
    "clean": (
        0.055,
        {"weight": "bold", "color": "#ffffffff", "stroke": 0.09, "shadow": True},
    ),
    "bold_pop": (
        0.075,
        {"weight": "bold", "color": "#ffe14dff", "stroke": 0.14, "shadow": True},
    ),
    "minimal_lower": (
        0.045,
        {"weight": "regular", "color": "#ffffffff", "background": "#000000a6"},
    ),
}


class TextLook(Params):
    style: StyleName = Field("clean", description="clean | bold_pop | minimal_lower")
    position: Position = Field("lower_safe", description="top|upper_safe|middle|lower_safe|bottom")
    size: Fraction | None = Field(
        None, gt=0, le=0.3, description="text height x the shorter frame side; omit for the style's"
    )
    color: Color | None = Field(None, description="text color; omit for the style's")


class TextParams(TextLook):
    text: str = Field(min_length=1, max_length=500, description="\\n for a line break")
    start: Timecode = Field(0.0, description="when it appears")
    end: Timecode | None = Field(None, description="when it disappears; omit for the rest")

    @model_validator(mode="after")
    def _ordered(self) -> TextParams:
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"end ({self.end:g}s) must be after start ({self.start:g}s)")
        return self


class CaptionsParams(TextLook):
    shift: float = Field(
        0.0, ge=-60, le=60, description="seconds added to every cue (negative: earlier)"
    )


def _style(look: TextLook, frame: tuple[int, int]) -> TextStyle:
    default_size, fields = STYLES[look.style]
    _box, valign = POSITIONS[look.position]
    W, H = frame
    short = look.size if look.size is not None else default_size
    fields = {**fields, **({"color": look.color} if look.color else {})}
    return TextStyle.model_validate(
        {
            **fields,
            "valign": valign,
            "size": None,
            "max_size": min(1.0, short * min(W, H) / H),  # TextStyle sizes are x frame height
        }
    )


def _place(
    g: Graph, layer: Layer, start: float, end: float | None
) -> tuple[float, float, float, float]:
    """Add one rendered layer to the graph, shown from `start` to `end`."""
    name = g.write_file(f"layer{len(g.sources)}.png", _png(layer))
    index = g.add_source(
        Source(g.work / name, MediaType.IMAGE, ".png", {"size": list(layer.image.size)})
    )
    window = f"gte(t,{num(start)})" if end is None else f"between(t,{num(start)},{num(end)})"
    assert g.video is not None
    (g.video,) = g.add(
        "overlay",
        {"x": layer.x, "y": layer.y, "enable": window},
        ins=[g.video, g.source_label(index)],
        prefix="v",
    )
    return layer.box


def _png(layer: Layer) -> bytes:
    import io

    buf = io.BytesIO()
    layer.image.save(buf, "PNG")
    return buf.getvalue()


def _record(
    g: Graph, kind: str, label: str, boxes: list[tuple[float, float, float, float]]
) -> None:
    """One layout box covering everything drawn, for the safe-margin check to judge."""
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    g.notes.setdefault("boxes", []).append(
        {"kind": kind, "label": label, "box": [x0, y0, x1 - x0, y1 - y0]}
    )


# -- text --------------------------------------------------------------------------------------


@video_op(
    name="video.text",
    version="1.0.1",
    params=TextParams,
    description="put a title or a line of text on the video for a time range",
    also=("pillow", "icc", "fonts"),
)
def text(g: Graph, p: TextParams) -> None:
    """Show `text` on the video from `start` to `end` in a house style at a named position.

    Line breaks are `\\n`; long lines wrap inside the box and shrink to fit it. Use
    `lower_safe` (or `upper_safe`) for vertical platforms so the text stays clear of the UI."""
    total = g.duration
    if total is not None and p.start >= total:
        raise OpError(f"text starts at {p.start:g}s but the video is only {total:.2f}s long")
    frame = (g.width, g.height)
    box, _ = POSITIONS[p.position]
    layer = render_layer(p.text, _style(p, frame), frame, box)
    if layer.box[2] == 0:
        raise OpError("the text drew nothing: it has no printable characters")
    _record(g, "text", p.text[:32], [_place(g, layer, p.start, p.end)])


# -- captions ----------------------------------------------------------------------------------


def _timed(cues: list[Cue], shift: float, total: float | None) -> list[Cue]:
    """Cues moved by `shift`, cut to the video, and without the ones that fall outside it."""
    out: list[Cue] = []
    for c in cues:
        start, end = max(0.0, c.start + shift), c.end + shift
        if total is not None:
            end = min(end, total)
        if end - start >= 0.04 and (total is None or start < total):
            out.append(Cue(start, end, c.text))
    return out


@video_op(
    name="video.captions",
    version="1.0.1",
    params=CaptionsParams,
    inputs=(MediaType.VIDEO, MediaType.TEXT),
    description="burn in captions from an SRT/WebVTT file (second input)",
    also=("pillow", "icc", "fonts"),
)
def captions(g: Graph, p: CaptionsParams) -> None:
    """Burn the cues of an SRT or WebVTT file into the video, each shown for its own time range.

    Markup and styling codes in the file are ignored (plain words only); the look comes from
    `style`. `shift` moves every cue when the file is a little early or late. Up to 300 cues."""
    raw = g.extra(0).path.read_bytes().decode("utf-8", errors="replace")
    cues = _timed(parse(raw), p.shift, g.duration)
    if not cues:
        raise OpError("no caption falls inside the video: check `shift`, or the file's times")
    if len(cues) > MAX_CUES:
        raise OpError(
            f"{len(cues)} captions is more than the {MAX_CUES} allowed: "
            "trim the video first, or split the file"
        )
    frame = (g.width, g.height)
    box, _ = POSITIONS[p.position]
    style = _style(p, frame)
    drawn = []
    for cue in cues:
        layer = render_layer(cue.text, style, frame, box)
        if layer.box[2] > 0:
            drawn.append(_place(g, layer, cue.start, cue.end))
    if not drawn:
        raise OpError("the captions drew nothing: the cues have no printable characters")
    _record(g, "captions", f"captions ({len(cues)} cues)", drawn)
    g.notes["captions"] = {"cues": len(cues), "style": p.style, "position": p.position}


# -- lower third -------------------------------------------------------------------------------


class LowerThirdParams(Params):
    name: str = Field(description="speaker name or main headline (e.g. 'Jane Doe')")
    title: str | None = Field(
        None, description="secondary title, role, or company (e.g. 'Founder & CEO')"
    )
    start: Timecode = Field(1.0, ge=0, description="timestamp in seconds when lower third appears")
    duration: Timecode = Field(5.0, ge=0.5, le=30.0, description="duration to display in seconds")
    accent_color: Color = Field(
        "#3b82f6ff", description="accent color for the highlight bar or pill"
    )
    card_color: Color = Field(
        "#0f172ae6", description="card background color (RGBA hex, e.g. #0f172ae6)"
    )
    text_color: Color = Field(
        "#ffffffff", description="primary text color for name"
    )
    title_color: Color = Field(
        "#94a3b8ff", description="secondary text color for title"
    )
    position: Literal["bottom_left", "bottom_center", "bottom_right"] = Field(
        "bottom_left", description="horizontal alignment along the lower third"
    )


def _render_lower_third(p: LowerThirdParams, frame: tuple[int, int]) -> Layer:
    from PIL import Image, ImageDraw, ImageFont

    from atelier.spec.types import rgba

    W, H = frame
    font_family = "Inter"
    try:
        f_name_path = fonts.font_path(font_family, "bold")
        f_title_path = fonts.font_path(font_family, "regular")
    except Exception:
        f_name_path = fonts.font_path(fonts.DEFAULT_FAMILY, "bold")
        f_title_path = fonts.font_path(fonts.DEFAULT_FAMILY, "regular")

    name_size = max(14, round(H * 0.034))
    title_size = max(11, round(H * 0.022))

    font_name = ImageFont.truetype(str(f_name_path), name_size)
    font_title = ImageFont.truetype(str(f_title_path), title_size)

    dummy_img = Image.new("RGBA", (1, 1))
    draw_dummy = ImageDraw.Draw(dummy_img)
    name_box = draw_dummy.textbbox((0, 0), p.name, font=font_name)
    name_w = round(name_box[2] - name_box[0])
    name_h = round(name_box[3] - name_box[1])

    title_w, title_h = 0, 0
    if p.title:
        t_box = draw_dummy.textbbox((0, 0), p.title, font=font_title)
        title_w = round(t_box[2] - t_box[0])
        title_h = round(t_box[3] - t_box[1])

    content_w = max(name_w, title_w)
    pad_x = round(H * 0.025)
    pad_y = round(H * 0.015)
    bar_w = round(H * 0.008)
    gap = round(H * 0.008) if p.title else 0

    card_w = int(content_w + pad_x * 2 + bar_w + round(H * 0.01))
    card_h = int(name_h + title_h + gap + pad_y * 2)
    corner_radius = round(card_h * 0.18)

    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    bg_rgba = rgba(p.card_color)
    accent_rgba = rgba(p.accent_color)
    text_rgba = rgba(p.text_color)
    title_rgba = rgba(p.title_color)

    draw.rounded_rectangle([0, 0, card_w, card_h], radius=corner_radius, fill=bg_rgba)

    bar_x = round(pad_x * 0.6)
    draw.rounded_rectangle(
        [bar_x, pad_y, bar_x + bar_w, card_h - pad_y],
        radius=round(bar_w / 2),
        fill=accent_rgba,
    )

    tx = bar_x + bar_w + round(H * 0.015)
    ty = pad_y
    draw.text((tx, ty), p.name, font=font_name, fill=text_rgba)
    if p.title:
        draw.text((tx, ty + name_h + gap), p.title, font=font_title, fill=title_rgba)

    margin_x = round(W * 0.06)
    margin_y = round(H * 0.12)

    if p.position == "bottom_left":
        pos_x = int(margin_x)
    elif p.position == "bottom_right":
        pos_x = int(W - card_w - margin_x)
    else:
        pos_x = int((W - card_w) // 2)

    pos_y = int(H - card_h - margin_y)
    drawn = (pos_x / W, pos_y / H, card_w / W, card_h / H)
    return Layer(card, pos_x, pos_y, drawn)


@video_op(
    name="video.lower_third",
    version="1.0.1",
    params=LowerThirdParams,
    description="branded speaker name and title card overlay in lower third",
    also=("pillow", "icc", "fonts"),
)
def lower_third(g: Graph, p: LowerThirdParams) -> None:
    """Display a professional branded lower-third card with speaker name and subtitle/role.
    Automatically sized, typeset in Inter, and positioned in the lower third."""
    total = g.duration
    if total is not None and p.start >= total:
        raise OpError(f"lower third starts at {p.start:g}s but the video is only {total:.2f}s long")
    frame = (g.width, g.height)
    layer = _render_lower_third(p, frame)
    end = p.start + p.duration
    if total is not None:
        end = min(end, total)
    _record(g, "lower_third", p.name, [_place(g, layer, p.start, end)])
