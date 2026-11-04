"""Op registry: the @op decorator, op definitions and determinism classes."""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.config import JsonDict

from atelier.media import MediaType
from atelier.resources import KNOWN_RESOURCES

# The worker finds an op's definition through this attribute on the op function.
OP_ATTR = "__atelier_op__"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_DESCRIPTION = 80

BUILTIN_MODULES = (
    "atelier.ops.debug",
    "atelier.ops.image.geometry",
    "atelier.ops.image.color",
    "atelier.ops.image.composite",
    "atelier.ops.image.text",
    "atelier.ops.image.grid",
    "atelier.ops.export",
    "atelier.av.ops",
    "atelier.av.text_ops",
    "atelier.av.join_ops",
    "atelier.av.frame_ops",
    "atelier.av.audio_ops",
    "atelier.docs.doc_ops",
    "atelier.docs.pdf_ops",
    "atelier.docs.xlsx_ops",
    "atelier.docs.pptx_ops",
    "atelier.docs.deck_ops",
    "atelier.ops.preview",
    "atelier.av.preview",
    "atelier.docs.preview",
    "atelier.ml.segment",
    "atelier.ml.speech",
    "atelier.ml.faces",
    "atelier.ml.upscale",
)


class Determinism(StrEnum):
    BIT_EXACT = "bit_exact"
    PERCEPTUAL = "perceptual"
    SEEDED_ML = "seeded_ml"
    NONDETERMINISTIC = "nondeterministic"


class Params(BaseModel):
    """Base class for op params. Unknown keys are rejected; instances are immutable.

    Fields marked with `internal()` are filled by the op's canonicalize hook (e.g. an
    expanded preset) and are left out of the catalogue.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


INTERNAL: JsonDict = {"internal": True}


@dataclass(frozen=True)
class AnyOf:
    """An input that accepts several media types (only `export` needs this today)."""

    types: frozenset[MediaType]

    def __contains__(self, media: object) -> bool:
        return media in self.types

    @property
    def label(self) -> str:
        return "|".join(t.value for t in MediaType if t in self.types)  # the enum's own order


class _Same:
    """Output type: the same media type as the op's first input."""

    def __repr__(self) -> str:
        return "same"


SAME = _Same()
InputType = MediaType | AnyOf
OutputType = MediaType | _Same


@dataclass(frozen=True)
class Artifact:
    path: Path
    media: MediaType
    ext: str
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpContext:
    work_dir: Path
    resources: Mapping[str, str]

    def output_path(self, ext: str) -> Path:
        return self.work_dir / f"output{ext}"


class OpError(Exception):
    """An op failure shown to the model and user as-is. Say how to fix it."""


class RegistryError(Exception):
    pass


class ArgError(Exception):
    """Raised by an op's `canonicalize` hook; becomes a SpecError at `<step>.args.<field>`."""

    def __init__(
        self,
        field: str,
        code: str,
        message: str,
        allowed: Sequence[str] | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.code = code
        self.message = message
        self.allowed = tuple(allowed) if allowed is not None else None
        self.suggestion = suggestion


# Turns validated, dumped args into canonical args (e.g. expanding a preset). Must be pure.
Canonicalizer = Callable[[dict[str, Any]], dict[str, Any]]
# Judges canonical args against the input media types once those are known; an ArgError becomes
# a spec error at `<step>.args.<field>` (e.g. a video preset given an image).
MediaCheck = Callable[[dict[str, Any], Sequence[MediaType]], "ArgError | None"]
# Contributes filters to an ffmpeg graph (av/graph.py) instead of running on its own; adjacent
# ones are fused into a single encode (docs/03 §3.7).
GraphFn = Callable[[Any, Any], None]
# Checks params against the number of inputs; returns an error message or None.
InputCheck = Callable[[Any, int], str | None]


OpFn = Callable[[OpContext, Sequence[Artifact], Any], Artifact]
F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class OpDef:
    name: str
    version: str
    inputs: tuple[InputType, ...]
    variadic: bool
    output: OutputType
    determinism: Determinism
    params: type[Params]
    fn: OpFn
    summary: Callable[[Artifact], str]
    description: str
    doc: str
    resources: tuple[str, ...]
    float_precision: int
    hidden: bool
    canonicalize: Canonicalizer | None = None
    check_inputs: InputCheck | None = None
    media_check: MediaCheck | None = None
    resources_by_media: Mapping[MediaType, tuple[str, ...]] = field(default_factory=dict)
    graph: GraphFn | None = None
    graph_media: frozenset[MediaType] = frozenset()  # input media the graph builder handles

    def accepts(self, count: int) -> bool:
        n = len(self.inputs)
        return count >= n if self.variadic else count == n

    def input_type(self, index: int) -> InputType:
        return self.inputs[min(index, len(self.inputs) - 1)]

    def input_label(self, index: int) -> str:
        t = self.input_type(index)
        return t.label if isinstance(t, AnyOf) else t.value

    @property
    def output_label(self) -> str:
        return "same" if isinstance(self.output, _Same) else self.output.value

    def output_for(self, input_types: Sequence[MediaType]) -> MediaType:
        return input_types[0] if isinstance(self.output, _Same) else self.output

    def resources_for(self, media: MediaType) -> tuple[str, ...]:
        """Pinned resources this op touches for an input of `media` (their IDs enter the key)."""
        return tuple(sorted({*self.resources, *self.resources_by_media.get(media, ())}))

    def fuses(self, media: MediaType) -> bool:
        return self.graph is not None and media in self.graph_media

    def arity_text(self) -> str:
        n = len(self.inputs)
        return f"{n}+" if self.variadic else str(n)


class Registry:
    def __init__(self) -> None:
        self._ops: dict[str, OpDef] = {}

    def register(self, opdef: OpDef) -> None:
        if opdef.name in self._ops:
            raise RegistryError(f"op {opdef.name!r} is already registered")
        self._ops[opdef.name] = opdef

    def get(self, name: str) -> OpDef | None:
        return self._ops.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._ops

    def __iter__(self) -> Iterator[OpDef]:
        return iter(sorted(self._ops.values(), key=lambda d: d.name))

    def __len__(self) -> int:
        return len(self._ops)

    def names(self) -> list[str]:
        return sorted(self._ops)

    def ops(self, *, include_hidden: bool = False) -> list[OpDef]:
        return [d for d in self if include_hidden or not d.hidden]


REGISTRY = Registry()


def op(
    *,
    name: str,
    version: str,
    inputs: Sequence[InputType],
    output: OutputType,
    determinism: Determinism,
    params: type[Params],
    summary: Callable[[Artifact], str],
    description: str,
    variadic: bool = False,
    resources: Sequence[str] = (),
    float_precision: int = 6,
    hidden: bool = False,
    canonicalize: Canonicalizer | None = None,
    check_inputs: InputCheck | None = None,
    media_check: MediaCheck | None = None,
    resources_by_media: Mapping[MediaType, Sequence[str]] | None = None,
    graph: GraphFn | None = None,
    graph_media: Sequence[MediaType] = (),
    registry: Registry | None = None,
) -> Callable[[F], F]:
    """Register an op. Its docstring becomes the detailed doc served by describe_op."""
    if not _NAME_RE.match(name):
        raise RegistryError(f"invalid op name {name!r}")
    if not _VERSION_RE.match(version):
        raise RegistryError(f"{name}: version must be MAJOR.MINOR.PATCH, got {version!r}")
    if not inputs:
        raise RegistryError(f"{name}: an op needs at least one input")
    if "\n" in description or len(description) > MAX_DESCRIPTION:
        raise RegistryError(f"{name}: description must be one line of <= {MAX_DESCRIPTION} chars")
    by_media = {m: tuple(sorted(r)) for m, r in (resources_by_media or {}).items()}
    used = {*resources, *(x for r in by_media.values() for x in r)}
    if unknown := used - KNOWN_RESOURCES:
        raise RegistryError(f"{name}: unknown resources {sorted(unknown)}")
    if bool(graph) != bool(graph_media):
        raise RegistryError(f"{name}: graph and graph_media go together")

    def decorate(fn: F) -> F:
        opdef = OpDef(
            name=name,
            version=version,
            inputs=tuple(inputs),
            variadic=variadic,
            output=output,
            determinism=determinism,
            params=params,
            fn=fn,
            summary=summary,
            description=description,
            doc=inspect.cleandoc(fn.__doc__ or description),
            resources=tuple(sorted(resources)),
            float_precision=float_precision,
            hidden=hidden,
            canonicalize=canonicalize,
            check_inputs=check_inputs,
            media_check=media_check,
            resources_by_media=by_media,
            graph=graph,
            graph_media=frozenset(graph_media),
        )
        (registry if registry is not None else REGISTRY).register(opdef)
        setattr(fn, OP_ATTR, opdef)
        return fn

    return decorate


def load_builtin_ops() -> Registry:
    for module in BUILTIN_MODULES:
        importlib.import_module(module)
    return REGISTRY
