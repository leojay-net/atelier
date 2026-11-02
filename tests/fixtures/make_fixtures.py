"""Regenerate the synthetic image fixtures. The committed bytes are what golden tests use;
rerun only when adding fixtures (then update goldens with an op version bump if needed).

    uv run python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent


def photo(size: tuple[int, int] = (640, 427)) -> Image.Image:
    w, h = size
    im = Image.new("RGB", size)
    px = im.load()
    assert px is not None
    for y in range(h):
        for x in range(w):
            px[x, y] = (40 + 150 * x // w, 90 + 120 * y // h, 200 - 120 * y // h)  # sky-ish
    d = ImageDraw.Draw(im)
    d.ellipse((380, 120, 560, 300), fill=(230, 80, 40))  # the "subject", right of center
    d.rectangle((0, 330, w, h), fill=(60, 110, 50))  # ground
    d.polygon([(60, 330), (160, 180), (260, 330)], fill=(90, 90, 100))
    return im


def logo() -> Image.Image:
    im = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, 199, 79), radius=16, fill=(255, 255, 255, 220))
    d.ellipse((12, 12, 68, 68), fill=(20, 120, 220, 255))
    d.rectangle((84, 30, 184, 50), fill=(30, 30, 30, 255))
    return im


def main() -> None:
    p = photo()
    p.save(HERE / "photo.jpg", quality=90, subsampling=0)
    p.save(HERE / "photo.png")  # lossless twin for golden tests
    # Same photo stored sideways with EXIF orientation 6 (display = rotate 90° clockwise).
    exif = Image.Exif()
    exif[0x0112] = 6
    p.transpose(Image.Transpose.ROTATE_90).save(HERE / "photo_exif6.jpg", quality=90, exif=exif)
    logo().save(HERE / "logo.png")
    p.resize((96, 64), Image.Resampling.LANCZOS).save(HERE / "small.png")


if __name__ == "__main__":
    main()
