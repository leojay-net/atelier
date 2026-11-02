"""Step-result cache and step keys (docs/05-determinism.md §5.3)."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from atelier.canonical import canonical_json, sha256_hex
from atelier.media import MediaType
from atelier.ops.registry import OpDef

# Bump to invalidate every cached result (e.g. a change in how keys are computed).
ENGINE_VERSION = "1"


def step_key(
    op: OpDef,
    args: Mapping[str, Any],
    input_hashes: Sequence[str],
    resources: Mapping[str, str],
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "engine": ENGINE_VERSION,
                "op": op.name,
                "op_version": op.version,
                "args": dict(args),
                "inputs": list(input_hashes),
                "resources": dict(resources),
            }
        )
    )


def group_key(
    steps: Sequence[tuple[OpDef, Mapping[str, Any], Sequence[int]]],
    input_hashes: Sequence[str],
    resources: Mapping[str, str],
) -> str:
    """Key for a fused chain of steps: the ordered ops (with versions and args), how each step
    reads its inputs (an index into `input_hashes`, or -1 for the previous step's output), the
    content of every external input, and the pinned resources (the ffmpeg build)."""
    return sha256_hex(
        canonical_json(
            {
                "engine": ENGINE_VERSION,
                "group": [
                    {"op": op.name, "op_version": op.version, "args": dict(args), "in": list(refs)}
                    for op, args, refs in steps
                ],
                "inputs": list(input_hashes),
                "resources": dict(resources),
            }
        )
    )


@dataclass(frozen=True)
class CachedStep:
    hash: str
    media: MediaType
    ext: str
    size: int
    summary: str
    meta: dict[str, Any]


class ResultCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        bucket = key.rsplit("-", 1)[-1][:2]
        return self.root / bucket / f"{key}.json"

    def get(self, key: str) -> CachedStep | None:
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
            return CachedStep(
                hash=data["hash"],
                media=MediaType(data["media"]),
                ext=data["ext"],
                size=data["size"],
                summary=data["summary"],
                meta=data["meta"],
            )
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def put(self, key: str, entry: CachedStep) -> None:
        self.put_raw(key, {**asdict(entry), "media": entry.media.value})

    def get_raw(self, key: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def put_raw(self, key: str, data: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
        os.replace(tmp, path)
