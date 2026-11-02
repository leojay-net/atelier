"""Self-tests for the perceptual harness. Real perceptual op tests arrive with Phase 1."""

from __future__ import annotations

import math

import numpy as np
import pytest

from harness.perceptual import assert_perceptually_equal, psnr, ssim

RNG = np.random.default_rng(0)
IMG = RNG.integers(0, 256, size=(64, 64, 3)).astype(np.uint8)


def test_identical() -> None:
    assert ssim(IMG, IMG) == pytest.approx(1.0)
    assert psnr(IMG, IMG) == math.inf
    assert_perceptually_equal(IMG, IMG)


def test_one_level_noise_passes() -> None:
    noisy = np.clip(IMG.astype(int) + RNG.integers(-1, 2, IMG.shape), 0, 255)
    assert_perceptually_equal(IMG, noisy)


def test_visible_change_fails() -> None:
    shifted = np.roll(IMG, 3, axis=1)
    assert ssim(IMG, shifted) < 0.5
    with pytest.raises(AssertionError, match="SSIM"):
        assert_perceptually_equal(IMG, shifted)


def test_shape_mismatch() -> None:
    with pytest.raises(ValueError):
        psnr(IMG, IMG[:32])
