"""Versioned presets: expert settings decided once, reviewed like code (principle P3).

Each YAML file under this package holds one preset:

    id: web_jpeg
    version: 1
    kind: export.image
    description: one line for the catalogue
    settings: {...}          # validated by the op module that owns the kind

A preset's `version` must be bumped whenever its settings change. The expanded settings
and the version both go into cache keys.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class PresetError(Exception):
    pass


@dataclass(frozen=True)
class Preset:
    id: str
    version: int
    kind: str
    description: str
    settings: dict[str, Any]
    source: Path


def _load(path: Path) -> Preset:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PresetError(f"{path}: expected a mapping")
    missing = {"id", "version", "kind", "description", "settings"} - set(data)
    if missing:
        raise PresetError(f"{path}: missing keys {sorted(missing)}")
    if extra := set(data) - {"id", "version", "kind", "description", "settings"}:
        raise PresetError(f"{path}: unknown keys {sorted(extra)}")
    if not isinstance(data["id"], str) or not _ID_RE.match(data["id"]):
        raise PresetError(f"{path}: invalid id {data['id']!r}")
    if path.stem != data["id"]:
        raise PresetError(f"{path}: file name must match id {data['id']!r}")
    if not isinstance(data["version"], int) or data["version"] < 1:
        raise PresetError(f"{path}: version must be a positive integer")
    if not isinstance(data["settings"], dict):
        raise PresetError(f"{path}: settings must be a mapping")
    return Preset(
        data["id"], data["version"], data["kind"], data["description"], data["settings"], path
    )


@functools.cache
def all_presets() -> dict[str, Preset]:
    presets: dict[str, Preset] = {}
    for path in sorted(ROOT.rglob("*.yaml")):
        preset = _load(path)
        if preset.id in presets:
            raise PresetError(f"duplicate preset id {preset.id!r} ({path})")
        presets[preset.id] = preset
    return presets


def of_kind(kind: str) -> dict[str, Preset]:
    return {k: p for k, p in all_presets().items() if p.kind == kind}


def get(preset_id: str, kind: str) -> Preset | None:
    preset = all_presets().get(preset_id)
    return preset if preset is not None and preset.kind == kind else None
