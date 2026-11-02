from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import pytest
from pydantic import Field

from atelier.media import MediaType
from atelier.ops.catalogue import catalogue, describe
from atelier.ops.registry import (
    Artifact,
    Determinism,
    OpContext,
    Params,
    Registry,
    RegistryError,
    load_builtin_ops,
    op,
)
from atelier.spec.types import Timecode


class AnchorParams(Params):
    start: Timecode
    anchor: Literal["top_left", "top_right", "center"] = "top_right"
    scale: float = Field(0.12, description="relative to frame width")
    font: str | None = None


def _noop(ctx: OpContext, inputs: Sequence[Artifact], p: AnchorParams) -> Artifact:
    raise NotImplementedError


def _register(reg: Registry, **overrides: object) -> None:
    kwargs: dict[str, object] = dict(
        name="video.overlay",
        version="1.0.0",
        inputs=[MediaType.VIDEO, MediaType.IMAGE],
        output=MediaType.VIDEO,
        determinism=Determinism.PERCEPTUAL,
        params=AnchorParams,
        summary=lambda a: "",
        description="overlay an image",
        registry=reg,
    )
    kwargs.update(overrides)
    op(**kwargs)(_noop)  # type: ignore[arg-type]


def test_catalogue_line_format() -> None:
    reg = Registry()
    _register(reg)
    assert catalogue(reg) == (
        "video.overlay(start,anchor=top_right|top_left|center,scale=0.12,font?) "
        "video,image→video — overlay an image"
    )


def test_describe() -> None:
    reg = Registry()
    _register(reg)
    opdef = reg.get("video.overlay")
    assert opdef is not None
    assert describe(opdef).splitlines() == [
        "video.overlay(start,anchor=top_right|top_left|center,scale=0.12,font?) "
        "video,image→video · v1.0.0 · perceptual",
        "overlay an image",
        "params:",
        "  start: timecode",
        '  anchor: top_left|top_right|center = "top_right"',
        "  scale: float = 0.12 — relative to frame width",
        "  font: str | null = null",
    ]


def test_hidden_ops_are_excluded_from_catalogue() -> None:
    reg = load_builtin_ops()
    public = catalogue(reg)
    assert "debug." not in public and "preview." not in public
    assert "image.resize(" in public
    assert "debug.upper" in catalogue(reg, include_hidden=True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Video.Overlay"},
        {"version": "1.0"},
        {"inputs": []},
        {"description": "x" * 81},
        {"resources": ["imagemagick"]},
    ],
)
def test_registration_rules(overrides: dict[str, object]) -> None:
    with pytest.raises(RegistryError):
        _register(Registry(), **overrides)


def test_duplicate_names_rejected() -> None:
    reg = Registry()
    _register(reg)
    with pytest.raises(RegistryError):
        _register(reg)


def test_arity() -> None:
    reg = load_builtin_ops()
    join = reg.get("debug.join")
    upper = reg.get("debug.upper")
    assert join is not None and upper is not None
    assert [join.accepts(n) for n in (1, 2, 5)] == [False, True, True]
    assert [upper.accepts(n) for n in (0, 1, 2)] == [False, True, False]
