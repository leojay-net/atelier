"""Plan cache: zero-token repeats (docs/04 §4.7).

Key: sha256(normalized prompt + sorted input content hashes + catalogue version + model id).
Value: the spec that produced an output passing every check. Exact match only; semantic
("similar request") matching would bring nondeterminism back and is deliberately left out.

The stored spec is the one the model submitted, not its canonical expansion: a hit goes
through the normal validator again, so a spec written against an older op version fails
validation, is evicted, and the request falls through to the model.
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atelier.canonical import canonical_json, sha256_hex


def normalize_prompt(text: str) -> str:
    """Case, width and whitespace differences must not change the key."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def plan_key(prompt: str, input_hashes: Iterable[str], catalogue_version: str, model: str) -> str:
    return sha256_hex(
        canonical_json(
            {
                "prompt": normalize_prompt(prompt),
                "inputs": sorted(input_hashes),
                "catalogue": catalogue_version,
                "model": model,
            }
        )
    )


@dataclass(frozen=True)
class CachedPlan:
    spec: Any
    summary: str


class PlanCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def lookup(self, key: str) -> CachedPlan | None:
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
            return CachedPlan(spec=data["spec"], summary=data["summary"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def store(self, key: str, spec: Any, summary: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{key}.tmp"
        tmp.write_text(
            json.dumps({"spec": spec, "summary": summary}, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self._path(key))

    def discard(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
