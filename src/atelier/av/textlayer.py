"""Text as picture: render titles and captions with Pillow, overlay them with ffmpeg.

ffmpeg's own text filters (drawtext, subtitles) depend on how that ffmpeg was built (FreeType,
HarfBuzz, libass, system fonts) and give different pixels on different machines. Here text is
drawn by the same engine as `image.text`: the bundled, hash-verified fonts, the same auto-fit,
outline and plate logic, so a caption looks the same everywhere and is judged by the same rules.
ffmpeg only composites a transparent PNG at a position and a time window, so no user text ever
reaches a filter string.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from atelier.ops.image.text import TextStyle, render_text


@dataclass(frozen=True)
class Layer:
    image: Image.Image  # RGBA, cropped to the text (and its plate/outline)
    x: int  # top-left position in the frame, px
    y: int
    box: tuple[float, float, float, float]  # layout box as fractions of the frame


def render_layer(
    text: str, style: TextStyle, frame: tuple[int, int], box: tuple[float, float, float, float]
) -> Layer:
    """Draw `text` into `box` (fractions of the frame) on a transparent canvas and crop it.

    Returns a tight PNG-sized layer plus where it goes. The `background` in `style` should be
    "none" or a fixed color: "auto" needs the pixels underneath, which change over time.
    """
    W, H = frame
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas, _ = render_text(canvas, text, style, box)
    canvas = canvas.convert("RGBA")
    bbox = canvas.getchannel("A").getbbox()
    if bbox is None:  # nothing was drawn
        return Layer(Image.new("RGBA", (2, 2), (0, 0, 0, 0)), 0, 0, (0.0, 0.0, 0.0, 0.0))
    x0, y0, x1, y1 = bbox
    drawn = (
        x0 / W,
        y0 / H,
        (x1 - x0) / W,
        (y1 - y0) / H,
    )  # what was really painted, plate included
    return Layer(canvas.crop(bbox), x0, y0, drawn)
