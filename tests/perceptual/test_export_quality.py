"""Quality floor for lossy exports, independent of platform (their bytes are only pinned on
the reference image). Each preset must stay perceptually close to the lossless source.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from atelier.engine.engine import Engine
from conftest import FIXTURES
from harness.perceptual import psnr, ssim

FLOORS = {"web_jpeg": (0.95, 36.0), "web_webp": (0.95, 36.0), "web_avif": (0.93, 34.0)}


@pytest.mark.parametrize("preset", sorted(FLOORS))
def test_lossy_presets_meet_quality_floor(engine: Engine, preset: str) -> None:
    src = engine.store.put_file(FIXTURES / "photo.png").handle
    rep = engine.run({"in": src, "ops": [["export", {"preset": preset}]]}).outputs["s1"]
    with Image.open(engine.store.path(rep.hash)) as out:
        decoded = np.asarray(out.convert("RGB"))
    reference = np.asarray(Image.open(FIXTURES / "photo.png").convert("RGB"))
    min_ssim, min_psnr = FLOORS[preset]
    assert ssim(reference, decoded) >= min_ssim
    assert psnr(reference, decoded) >= min_psnr
