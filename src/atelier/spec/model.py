"""The canonical, validated form of a spec. The engine only ever runs this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atelier.canonical import canonical_json, sha256_hex
from atelier.media import AssetInfo
from atelier.ops.registry import OpDef

SPEC_VERSIONS = (1,)
CHECK_MODES = ("default", "strict", "none")


@dataclass(frozen=True)
class CanonicalStep:
    id: str
    op: OpDef
    refs: tuple[str, ...]
    args: dict[str, Any]  # defaults filled, units normalized, floats rounded


@dataclass(frozen=True)
class CanonicalSpec:
    version: int
    inputs: dict[str, AssetInfo]
    steps: tuple[CanonicalStep, ...]  # topological order
    outputs: tuple[str, ...]
    checks: str | tuple[str, ...]
    origin: dict[str, Any] | None = None  # e.g. {"recipe": "social_clip", "version": 2}

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": self.version,
            "inputs": {name: info.handle for name, info in self.inputs.items()},
            "steps": [
                {
                    "id": s.id,
                    "op": s.op.name,
                    "op_version": s.op.version,
                    "in": list(s.refs),
                    "args": s.args,
                }
                for s in self.steps
            ],
            "outputs": list(self.outputs),
            "checks": self.checks if isinstance(self.checks, str) else list(self.checks),
        }
        if self.origin is not None:
            data["origin"] = self.origin
        return data

    @property
    def hash(self) -> str:
        return sha256_hex(canonical_json(self.to_json()))
