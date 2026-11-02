"""Hidden text ops that exercise the engine end to end before real media ops exist.

They are excluded from the agent catalogue but valid in specs, so tests and the CLI can
drive validation, caching and worker limits without any media libraries.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from pydantic import Field

from atelier.media import MediaType
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op

TEXT = MediaType.TEXT


def _read(artifact: Artifact) -> str:
    return artifact.path.read_bytes().decode("utf-8")


def _write(ctx: OpContext, text: str) -> Artifact:
    out = ctx.output_path(".txt")
    out.write_bytes(text.encode("utf-8"))
    return Artifact(
        path=out,
        media=TEXT,
        ext=".txt",
        meta={"chars": len(text), "lines": len(text.splitlines())},
    )


def text_summary(result: Artifact) -> str:
    lines = result.meta["lines"]
    return f"text · {result.meta['chars']} chars · {lines} line{'s' * (lines != 1)}"


class NoParams(Params):
    pass


class JoinParams(Params):
    sep: str = Field("", description="inserted between inputs")


class SleepParams(Params):
    seconds: float = Field(ge=0, le=3600, description="how long to block")


class FailParams(Params):
    message: str = Field(description="error message to raise")


@op(
    name="debug.upper",
    version="1.0.0",
    inputs=[TEXT],
    output=TEXT,
    determinism=Determinism.BIT_EXACT,
    params=NoParams,
    summary=text_summary,
    description="uppercase a text file",
    hidden=True,
)
def upper(ctx: OpContext, inputs: Sequence[Artifact], p: NoParams) -> Artifact:
    """Uppercase the whole text (Unicode-aware)."""
    return _write(ctx, _read(inputs[0]).upper())


@op(
    name="debug.join",
    version="1.0.0",
    inputs=[TEXT, TEXT],
    variadic=True,
    output=TEXT,
    determinism=Determinism.BIT_EXACT,
    params=JoinParams,
    summary=text_summary,
    description="concatenate text files in order",
    hidden=True,
)
def join(ctx: OpContext, inputs: Sequence[Artifact], p: JoinParams) -> Artifact:
    """Concatenate two or more text inputs in the order given, with `sep` between them."""
    return _write(ctx, p.sep.join(_read(a) for a in inputs))


@op(
    name="debug.sleep",
    version="1.0.0",
    inputs=[TEXT],
    output=TEXT,
    determinism=Determinism.BIT_EXACT,
    params=SleepParams,
    summary=text_summary,
    description="block for a while, then pass the input through",
    hidden=True,
)
def sleep(ctx: OpContext, inputs: Sequence[Artifact], p: SleepParams) -> Artifact:
    """Used to test wall-time limits."""
    time.sleep(p.seconds)
    return _write(ctx, _read(inputs[0]))


@op(
    name="debug.fail",
    version="1.0.0",
    inputs=[TEXT],
    output=TEXT,
    determinism=Determinism.BIT_EXACT,
    params=FailParams,
    summary=text_summary,
    description="always fail with the given message",
    hidden=True,
)
def fail(ctx: OpContext, inputs: Sequence[Artifact], p: FailParams) -> Artifact:
    """Used to test error reporting."""
    raise OpError(p.message)
