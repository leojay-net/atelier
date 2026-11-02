"""Canonical JSON and hashing. Everything that feeds a hash goes through here."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


def _normalize(obj: Any, precision: int | None) -> Any:
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"non-finite float {obj!r} cannot be canonicalized")
        if precision is not None:
            obj = round(obj, precision)
        return obj + 0.0  # folds -0.0 into 0.0
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError(f"canonical JSON keys must be strings, got {key!r}")
            out[key] = _normalize(value, precision)
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalize(v, precision) for v in obj]
    raise TypeError(f"{type(obj).__name__} is not canonical-JSON serializable")


def round_floats(obj: Any, precision: int) -> Any:
    """Return a copy of a JSON-like value with every float rounded to `precision` digits."""
    return _normalize(obj, precision)


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, fixed separators, UTF-8, no NaN/Infinity."""
    return json.dumps(
        _normalize(obj, None),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()
