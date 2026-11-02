"""Perceptual comparison for PERCEPTUAL-class ops off the reference platform.

Thresholds from docs/05-determinism.md §5.4: SSIM >= 0.995 and PSNR >= 45 dB.
SSIM here uses a uniform 8x8 window on the luma (or single) channel. That is simpler than
the Gaussian-window original but stable and good enough as a regression gate.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray

MIN_SSIM = 0.995
MIN_PSNR = 45.0

Array = NDArray[np.floating] | NDArray[np.integer]


def _luma(img: Array) -> NDArray[np.float64]:
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 3:
        if a.shape[2] >= 3:
            return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        return a[..., 0]
    return a


def psnr(a: Array, b: Array, max_value: float = 255.0) -> float:
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if x.shape != y.shape:
        raise ValueError(f"shape mismatch {x.shape} vs {y.shape}")
    mse = float(np.mean((x - y) ** 2))
    return math.inf if mse == 0 else 10 * math.log10(max_value**2 / mse)


def ssim(a: Array, b: Array, max_value: float = 255.0, window: int = 8) -> float:
    x, y = _luma(a), _luma(b)
    if x.shape != y.shape:
        raise ValueError(f"shape mismatch {x.shape} vs {y.shape}")
    if min(x.shape) < window:
        raise ValueError(f"images must be at least {window}x{window}")
    c1, c2 = (0.01 * max_value) ** 2, (0.03 * max_value) ** 2
    wx = sliding_window_view(x, (window, window))
    wy = sliding_window_view(y, (window, window))
    mx, my = wx.mean(axis=(-1, -2)), wy.mean(axis=(-1, -2))
    vx, vy = wx.var(axis=(-1, -2)), wy.var(axis=(-1, -2))
    cov = (wx * wy).mean(axis=(-1, -2)) - mx * my
    score = ((2 * mx * my + c1) * (2 * cov + c2)) / ((mx**2 + my**2 + c1) * (vx + vy + c2))
    return float(score.mean())


def assert_perceptually_equal(
    a: Array, b: Array, *, min_ssim: float = MIN_SSIM, min_psnr: float = MIN_PSNR
) -> None:
    s, p = ssim(a, b), psnr(a, b)
    assert s >= min_ssim and p >= min_psnr, (
        f"SSIM {s:.4f} (min {min_ssim}) · PSNR {p:.1f} dB (min {min_psnr})"
    )
