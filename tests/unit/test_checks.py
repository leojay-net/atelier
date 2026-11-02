"""Checks are pure functions of metadata, so they are tested without running ops."""

from __future__ import annotations

from typing import Any

import pytest

from atelier.checks import CheckResult, Status, run_checks, select, summarize
from atelier.media import MediaType

IMAGE = MediaType.IMAGE


def meta(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "width": 1200,
        "height": 630,
        "mode": "RGB",
        "bytes": 100_000,
        "quality": 85,
        "icc": "sRGB",
        "has_alpha": False,
        "boxes": [],
        "export": {
            "preset": "og_image",
            "version": 1,
            "max_bytes": 307_200,
            "alpha": False,
            "icc": "srgb",
            "checks": ["dimensions", "file_size_budget", "color_profile", "safe_margins"],
            "size": {"width": 1200, "height": 630, "mode": "fill"},
            "safe_area": {"top": 0.06, "bottom": 0.06, "left": 0.06, "right": 0.06},
        },
    }
    base.update(overrides)
    return base


def text_box(**overrides: Any) -> dict[str, Any]:
    box = {
        "kind": "text",
        "label": "Title",
        "box": [0.1, 0.1, 0.5, 0.2],
        "fit": True,
        "contrast": 7.0,
        "min_contrast": 4.5,
        "font_px": 40,
    }
    box.update(overrides)
    return box


def by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


def test_all_pass() -> None:
    results = run_checks("default", IMAGE, meta(boxes=[text_box()]))
    assert summarize(results) == "checks 6/6 pass"


def test_not_applicable_checks_are_skipped() -> None:
    results = run_checks("default", IMAGE, {"width": 10, "height": 10, "mode": "RGB", "boxes": []})
    assert results == []
    assert summarize(results) == "checks none applicable"


@pytest.mark.parametrize(
    ("overrides", "name", "message"),
    [
        ({"width": 1199}, "dimensions", "1199x630, preset wants exactly 1200x630"),
        ({"bytes": 400_000}, "file_size_budget", "390.6KB > 300.0KB even at q85"),
        ({"icc": None}, "color_profile", "sRGB profile missing"),
        (
            {"has_alpha": True, "mode": "RGBA"},
            "color_profile",
            "output has alpha; preset forbids it",
        ),
        ({"boxes": [text_box(fit=False)]}, "text_fit", "'Title' overflows its box at 40px"),
        (
            {"boxes": [text_box(clipped=True)]},
            "text_fit",
            "'Title' is cut off by a later crop/resize",
        ),
        (
            {"boxes": [text_box(box=[0.02, 0.5, 0.3, 0.1])]},
            "safe_margins",
            "text 'Title' enters the left safe margin by 48px",
        ),
        (
            {"boxes": [text_box(box=[0.6, 0.9, 0.3, 0.1])]},
            "safe_margins",
            "text 'Title' enters the bottom safe margin by 38px",
        ),
    ],
)
def test_failures(overrides: dict[str, Any], name: str, message: str) -> None:
    result = by_name(run_checks("default", IMAGE, meta(**overrides)))[name]
    assert (result.status, result.message) == (Status.FAIL, message)
    assert result.hint or name in ("dimensions", "color_profile")


def test_low_contrast_warns_and_strict_fails() -> None:
    m = meta(boxes=[text_box(contrast=3.1)])
    warn = by_name(run_checks("default", IMAGE, m))["text_contrast"]
    assert warn.status is Status.WARN
    assert warn.line() == (
        "WARN text_contrast: 'Title' 3.1:1 (min 4.5) → hint: set background=\"auto\", "
        "shadow=true, or stroke=0.06 on that image.text step"
    )
    assert by_name(run_checks("strict", IMAGE, m))["text_contrast"].status is Status.FAIL


def test_tiled_watermarks_are_ignored_by_layout_checks() -> None:
    tiled = {"kind": "watermark", "label": "PROOF", "box": [0, 0, 1, 1], "tiled": True}
    assert "safe_margins" not in by_name(run_checks("default", IMAGE, meta(boxes=[tiled])))


def test_selection_modes() -> None:
    m = meta()
    assert select("none", IMAGE, m) == []
    assert {c.name for c in select(["dimensions"], IMAGE, m)} == {"dimensions"}
    no_preset = {c.name for c in select("default", IMAGE, {"boxes": []})}
    assert no_preset == {"text_fit", "text_contrast", "safe_margins"}
    assert len(select("strict", IMAGE, m)) == 6


def test_summary_lists_only_problems() -> None:
    results = run_checks("default", IMAGE, meta(width=1, boxes=[text_box(contrast=2.0)]))
    assert summarize(results).startswith("checks 4/6 · FAIL dimensions: 1x630")
    assert "WARN text_contrast" in summarize(results)
