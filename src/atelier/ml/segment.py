"""Cutting a subject out of its background with U2-Net (small)."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image, ImageFilter
from pydantic import Field

from atelier import models
from atelier.ops.image._io import image_op, load, save
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params
from atelier.spec.types import Color, Fraction, rgba

SIZE = 320  # the network's input side
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
INTRA_OP_THREADS = 4  # fixed, not derived from the machine; see _session()


class RemoveBackgroundParams(Params):
    threshold: Fraction | None = Field(
        None, gt=0, lt=1, description="cut hard at this level instead of a soft edge"
    )
    feather: float = Field(0.0, ge=0, le=20, description="blur the edge by this many px")
    background: Color | None = Field(
        None, description="fill the background with this color instead of making it transparent"
    )


@functools.cache
def _session() -> Any:
    """One session per worker process: cheap to reuse across many calls (a tracked reframe
    samples dozens of frames), and each worker is single-use, so nothing goes stale."""
    try:
        import onnxruntime as ort
    except ImportError:
        raise OpError(
            "background removal needs the ML runtime: install it with `pip install atelier[ml]`"
        ) from None
    try:
        path = models.file_path("u2netp", "u2netp.onnx")
    except models.ModelError as e:
        raise OpError(str(e)) from None
    options = ort.SessionOptions()
    # A *fixed* thread count, not the host's core count: onnxruntime's parallel reduction
    # changes floating-point rounding with the thread count (confirmed: same count, same
    # mask bytes every time; different counts, different bytes), so this number is part of
    # what makes the mask reproducible, the same way x264 gets a fixed `-threads`. 1 thread
    # was correct but needlessly slow (measured 5-6x faster at 4 than at 1 on one platform);
    # 4 is fixed here instead of 1, not derived from the machine, so that stays true.
    options.intra_op_num_threads = INTRA_OP_THREADS
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def subject_mask(im: Image.Image) -> Image.Image:
    """A grayscale mask (255 = subject) the size of `im`."""
    session = _session()
    small = im.convert("RGB").resize((SIZE, SIZE), Image.Resampling.BILINEAR)
    pixels = np.asarray(small, dtype=np.float32) / 255.0
    norm = (pixels - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
    batch = np.ascontiguousarray(norm.transpose(2, 0, 1)[None], dtype=np.float32)
    out = session.run(None, {session.get_inputs()[0].name: batch})[0]  # the fused prediction
    pred = np.asarray(out[0, 0], dtype=np.float32)
    span = float(pred.max() - pred.min())
    pred = (pred - pred.min()) / span if span > 1e-8 else np.zeros_like(pred)
    mask = Image.fromarray(np.clip(np.rint(pred * 255), 0, 255).astype(np.uint8), "L")
    return mask.resize(im.size, Image.Resampling.BICUBIC)


@image_op(
    name="image.remove_background",
    version="1.0.0",
    params=RemoveBackgroundParams,
    description="cut the main subject out of its background (transparent or a color)",
    determinism=Determinism.SEEDED_ML,
    resources=("pillow", "icc", "ml_runtime", "model_u2netp"),
)
def remove_background(
    ctx: OpContext, inputs: Sequence[Artifact], p: RemoveBackgroundParams
) -> Artifact:
    """Find the main subject with a small neural network and make everything else transparent
    (or `background`). Works best on a single, clear subject: a product, a person, an animal.
    `threshold` turns the soft edge into a hard cut, `feather` softens it. Existing transparency
    is kept. A photo with several equal subjects, or a subject that fills the frame, may come
    out wrong: check it with `preview`."""
    im = load(inputs[0])
    rgba_im = im.convert("RGBA")
    mask = subject_mask(rgba_im)
    if p.threshold is not None:
        cut = round(p.threshold * 255)
        mask = mask.point(lambda v: 255 if v >= cut else 0)
    if p.feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(p.feather))
    existing = rgba_im.getchannel("A")
    alpha = Image.fromarray(
        np.minimum(np.asarray(mask, dtype=np.uint16), np.asarray(existing, dtype=np.uint16)).astype(
            np.uint8
        ),
        "L",
    )
    cut_out = rgba_im.copy()
    cut_out.putalpha(alpha)
    if p.background is not None:
        r, g, b, a = rgba(p.background)
        base = Image.new("RGBA", cut_out.size, (r, g, b, a))
        base.alpha_composite(cut_out)
        cut_out = base if a < 255 else base.convert("RGB")
    return save(ctx, cut_out)
