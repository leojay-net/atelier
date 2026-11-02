"""Regenerate the eval suite's input images (synthetic, deterministic, no licensing questions).

    uv run python tests/evals/make_inputs.py

The committed files are what the tasks use; rerun only when adding an input. Aspect ratios are
deliberately varied (3:2, 2:3, 1:1, 16:7, 16:10) because that is where layout bugs show up.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent / "inputs"


def scene(
    size: tuple[int, int], sky: tuple[int, int, int], sun: tuple[int, int, int]
) -> Image.Image:
    """A sky gradient, a sun off to the right of center, a ridge and some ground."""
    w, h = size
    y = np.linspace(0, 1, h)[:, None, None]
    x = np.linspace(0, 1, w)[None, :, None]
    base = np.array(sky, dtype=float)[None, None, :]
    arr = base * (0.55 + 0.45 * (1 - y)) + 40 * x
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im)
    d.ellipse((w * 0.58, h * 0.16, w * 0.86, h * 0.16 + w * 0.28), fill=sun)
    d.polygon([(0, h * 0.72), (w * 0.22, h * 0.42), (w * 0.45, h * 0.66), (w * 0.62, h * 0.5),
               (w, h * 0.74), (w, h), (0, h)], fill=(70, 88, 96))  # fmt: skip
    d.rectangle((0, h * 0.82, w, h), fill=(52, 96, 58))
    return im


def logo(fg: tuple[int, int, int, int], bg: tuple[int, int, int, int]) -> Image.Image:
    im = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, 299, 119), radius=24, fill=bg)
    d.ellipse((18, 18, 102, 102), fill=fg)
    d.rectangle((126, 44, 276, 76), fill=fg)
    return im


def product() -> Image.Image:
    """An opaque object on a fully transparent background, slightly off-center."""
    im = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((230, 180, 640, 760), radius=60, fill=(196, 62, 48, 255))
    d.ellipse((330, 260, 540, 470), fill=(245, 245, 245, 255))
    d.rectangle((280, 560, 590, 620), fill=(40, 40, 46, 255))
    return im


def screenshot() -> Image.Image:
    im = Image.new("RGB", (1440, 900), (243, 245, 248))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1440, 64), fill=(30, 41, 59))
    d.rectangle((0, 64, 240, 900), fill=(226, 232, 240))
    for i in range(6):
        d.rectangle((24, 100 + i * 56, 216, 132 + i * 56), fill=(203, 213, 225))
    for r in range(2):
        for c in range(3):
            x0, y0 = 280 + c * 380, 110 + r * 340
            d.rounded_rectangle((x0, y0, x0 + 340, y0 + 300), radius=14, fill=(255, 255, 255))
            d.rectangle((x0 + 24, y0 + 24, x0 + 200, y0 + 44), fill=(148, 163, 184))
            d.rectangle((x0 + 24, y0 + 70, x0 + 316, y0 + 260), fill=(226, 232, 240))
    return im


def main() -> None:
    HERE.mkdir(exist_ok=True)
    scene((1200, 800), (90, 150, 220), (250, 200, 80)).save(HERE / "landscape.jpg", quality=88)
    scene((800, 1200), (120, 100, 200), (255, 170, 120)).save(HERE / "portrait.jpg", quality=88)
    scene((1000, 1000), (70, 170, 170), (255, 230, 120)).save(HERE / "square.png")
    scene((1600, 700), (200, 120, 90), (255, 220, 160)).save(HERE / "wide.jpg", quality=88)
    scene((300, 200), (90, 150, 220), (250, 200, 80)).save(HERE / "tiny.png")
    logo((20, 120, 220, 255), (255, 255, 255, 230)).save(HERE / "logo.png")
    logo((255, 255, 255, 255), (24, 30, 44, 235)).save(HERE / "logo_dark.png")
    product().save(HERE / "product.png")
    screenshot().save(HERE / "screenshot.png")
    # Stored sideways with EXIF orientation 6, so it displays as an 800x1200 portrait.
    sideways = scene((800, 1200), (150, 110, 90), (250, 210, 120)).transpose(
        Image.Transpose.ROTATE_90
    )
    exif = Image.Exif()
    exif[0x0112] = 6
    sideways.save(HERE / "phone.jpg", quality=88, exif=exif)
    palettes = [
        ((90, 150, 220), (250, 200, 80)),
        ((200, 110, 110), (255, 235, 150)),
        ((100, 180, 120), (255, 200, 200)),
        ((150, 130, 210), (250, 220, 100)),
    ]
    for i, (sky, sun) in enumerate(palettes, 1):
        scene((600, 600), sky, sun).save(HERE / f"shot{i}.jpg", quality=88)


if __name__ == "__main__":
    main()
