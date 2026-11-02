"""Batch runs, including the Phase 1 exit criterion: a batch of 500 images is
byte-for-byte deterministic across two independent runs (no shared cache)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from atelier.canonical import sha256_file
from atelier.engine.engine import Engine, bind_input
from conftest import FIXTURES

BATCH = 500


def _images(n: int) -> list[bytes]:
    rng = np.random.default_rng(1234)
    out = []
    for i in range(n):
        gradient = np.linspace(0, 255, 96, dtype=np.float64)[None, :, None]
        noise = rng.normal(0, 25, size=(64, 96, 3))
        tint = np.array([(i * 37) % 256, (i * 91) % 256, (i * 53) % 256])[None, None, :]
        arr = np.clip(gradient * 0.6 + tint * 0.4 + noise, 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr, "RGB").save(buf, "PNG")
        out.append(buf.getvalue())
    return out


def _run(home: Path, images: list[bytes]) -> list[str]:
    engine = Engine.open(home)
    logo = engine.store.put_file(FIXTURES / "logo.png").handle
    handles = [engine.store.put_bytes(data).handle for data in images]
    template = {"recipe": "watermark_batch", "params": {"logo": logo, "style": "corner_bold"}}
    items = engine.run_batch(template, "in", handles)
    assert all(i.result is not None for i in items), [i.error for i in items if i.error]
    return [
        sha256_file(engine.store.path(i.result.outputs["r2"].hash))  # type: ignore[union-attr]
        for i in items
    ]


def test_bind_input_forms() -> None:
    assert bind_input({"ops": []}, "in", "asset:1")["in"] == "asset:1"
    assert bind_input({"recipe": "x"}, "in", "asset:1")["params"] == {"in": "asset:1"}
    assert bind_input({"steps": []}, "a", "asset:1")["inputs"] == {"a": "asset:1"}


def test_batch_reports_errors_per_item(engine: Engine) -> None:
    good = engine.store.put_file(FIXTURES / "photo.png").handle
    items = engine.run_batch(
        {"in": "x", "ops": [["export", {"preset": "web_jpeg"}]]}, "in", [good, "asset:deadbeef"]
    )
    assert items[0].result is not None and items[0].error is None
    assert items[1].result is None and "not in the store" in (items[1].error or "")


@pytest.mark.slow
def test_500_image_batch_is_deterministic(tmp_path: Path) -> None:
    images = _images(BATCH)
    assert len({hash(b) for b in images}) == BATCH  # all distinct inputs
    first = _run(tmp_path / "a", images)
    second = _run(tmp_path / "b", images)
    assert first == second
    assert len(set(first)) == BATCH
