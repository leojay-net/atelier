"""Recipes: versioned, parameterized pipeline templates (docs/03 §3.6).

    id: watermark_batch
    version: 1
    description: one line for the catalogue
    params:
      in:     {type: asset, media: image}
      style:  {type: preset, kind: watermark, default: corner_subtle}
    steps:
      - {op: image.watermark, in: [in, logo], args: {preset: "{{style}}"}}
      - when: "title != ''"
        op: image.text
        args: {text: "{{title}}"}

A step's `in` defaults to the previous step (or the `in` param for the first step).
`"{{p}}"` as a whole value substitutes the typed param; inside a longer string it
substitutes the text. `when` supports `p`, `not p`, `p == 'x'` and `p != 'x'`; give a list of
them when all must hold.

`expand` turns `{"recipe": id, "params": {...}}` into a full-form spec. Param errors are
reported at `params.<name>` with the same actionable format as every other spec error.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from atelier import presets
from atelier.fmt import short_repr
from atelier.media import MediaType
from atelier.spec.errors import SpecError, SpecInvalid, closest, join_path
from atelier.spec.types import parse_color, parse_fraction, parse_timecode

ROOT = Path(__file__).parent
MAX_STRUCTURED = 64 * 1024  # bytes of JSON in an object or list parameter
PARAM_TYPES = (
    "asset",
    "enum",
    "preset",
    "str",
    "int",
    "float",
    "bool",
    "timecode",
    "fraction",
    "color",
    "object",
    "list",
)
_TEMPLATE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_WHEN = re.compile(r"^\s*(not\s+)?([a-z_][a-z0-9_]*)\s*(?:(==|!=)\s*'([^']*)')?\s*$")


class RecipeError(Exception):
    pass


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    default: Any
    required: bool
    media: MediaType | None
    values: tuple[str, ...] | None  # enum values, or preset ids for type preset
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True)
class Recipe:
    id: str
    version: int
    description: str
    params: dict[str, Param]
    steps: tuple[dict[str, Any], ...]
    source: Path


def _param(name: str, spec: dict[str, Any], where: Path) -> Param:
    kind = spec.get("type")
    if kind not in PARAM_TYPES:
        raise RecipeError(f"{where}: param {name!r} has unknown type {kind!r}")
    values: tuple[str, ...] | None = None
    if kind == "enum":
        values = tuple(str(v) for v in spec["values"])
    elif kind == "preset":
        values = tuple(sorted(presets.of_kind(spec["kind"])))
        if not values:
            raise RecipeError(f"{where}: no presets of kind {spec['kind']!r}")
    media = MediaType(spec["media"]) if kind == "asset" and "media" in spec else None
    return Param(
        name,
        kind,
        spec.get("default"),
        "default" not in spec,
        media,
        values,
        spec.get("min"),
        spec.get("max"),
    )


def _load(path: Path) -> Recipe:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("id", "version", "description", "params", "steps"):
        if key not in data:
            raise RecipeError(f"{path}: missing {key!r}")
    if path.stem != data["id"]:
        raise RecipeError(f"{path}: file name must match id {data['id']!r}")
    params = {n: _param(n, s, path) for n, s in data["params"].items()}
    steps = tuple(data["steps"])
    for i, step in enumerate(steps):
        if "op" not in step:
            raise RecipeError(f"{path}: steps[{i}] needs 'op'")
        for name in _TEMPLATE.findall(repr(step)):
            if name not in params:
                raise RecipeError(f"{path}: steps[{i}] uses unknown param {name!r}")
    return Recipe(data["id"], data["version"], data["description"], params, steps, path)


@functools.cache
def all_recipes() -> dict[str, Recipe]:
    return {r.id: r for r in (_load(p) for p in sorted(ROOT.glob("*.yaml")))}


# -- params ------------------------------------------------------------------------------


def _coerce(p: Param, value: Any) -> Any:
    """Return the typed value or raise ValueError with a short reason."""
    if p.type in ("enum", "preset"):
        assert p.values is not None
        if value not in p.values:
            raise LookupError(value)
        return value
    if p.type in ("asset", "str"):
        if not isinstance(value, str):
            raise ValueError("expected a string")
        return value
    if p.type == "bool":
        if not isinstance(value, bool):
            raise ValueError("expected true or false")
        return value
    if p.type in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected a number")
        if p.type == "int" and not float(value).is_integer():
            raise ValueError("expected an integer")
        number = int(value) if p.type == "int" else float(value)
        if (p.minimum is not None and number < p.minimum) or (
            p.maximum is not None and number > p.maximum
        ):
            raise ValueError(f"must be between {p.minimum} and {p.maximum}")
        return number
    if p.type in ("object", "list"):
        want = dict if p.type == "object" else list
        if not isinstance(value, want):
            raise ValueError(f"expected {'an object' if p.type == 'object' else 'a list'}")
        if len(json.dumps(value, default=str)) > MAX_STRUCTURED:
            raise ValueError(f"too large (over {MAX_STRUCTURED // 1024} KB)")
        return value
    parser = {"timecode": parse_timecode, "fraction": parse_fraction, "color": parse_color}
    return parser[p.type](value)


def _resolve_params(recipe: Recipe, given: Any, errors: list[SpecError]) -> dict[str, Any]:
    if not isinstance(given, dict):
        errors.append(SpecError("params", "type", "params must be an object"))
        return {}
    names = sorted(recipe.params)
    for key in given:
        if key not in recipe.params:
            errors.append(
                SpecError(
                    join_path("params", key),
                    "unknown_key",
                    f"recipe {recipe.id} has no param {short_repr(key)}",
                    tuple(names),
                    closest(key, names),
                )
            )
    values: dict[str, Any] = {}
    for name, p in recipe.params.items():
        path = f"params.{name}"
        if name not in given:
            if p.required:
                errors.append(SpecError(path, "missing_key", f"required by recipe {recipe.id}"))
            else:
                values[name] = p.default
            continue
        try:
            values[name] = _coerce(p, given[name])
        except LookupError:
            assert p.values is not None
            errors.append(
                SpecError(
                    path,
                    "enum_mismatch",
                    f"{short_repr(given[name])} invalid",
                    p.values,
                    closest(given[name], p.values),
                )
            )
        except ValueError as e:
            errors.append(SpecError(path, "invalid_arg", f"{short_repr(given[name])} invalid: {e}"))
    return values


# -- expansion ---------------------------------------------------------------------------


def _render(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _TEMPLATE.fullmatch(value.strip())
        if whole:
            return params[whole.group(1)]
        return _TEMPLATE.sub(lambda m: str(params[m.group(1)]), value)
    if isinstance(value, dict):
        return {k: _render(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, params) for v in value]
    return value


def _when(expr: str, params: dict[str, Any]) -> bool:
    m = _WHEN.match(expr)
    if not m or m.group(2) not in params:
        raise RecipeError(f"unsupported when expression {expr!r}")
    negate, name, op, literal = m.groups()
    value = params[name]
    result = bool(value) if op is None else (str(value) == literal) == (op == "==")
    return not result if negate else result


def expand(raw: dict[str, Any]) -> tuple[dict[str, Any], Recipe]:
    """Recipe form → full-form spec. Raises SpecInvalid for unknown recipes or bad params."""
    recipes = all_recipes()
    extra = sorted(set(raw) - {"recipe", "params", "checks"})
    errors = [
        SpecError(
            join_path("", k), "unknown_key", f"unknown key {k!r}", ("checks", "params", "recipe")
        )
        for k in extra
    ]
    name = raw.get("recipe")
    recipe = recipes.get(name) if isinstance(name, str) else None
    if recipe is None:
        names = sorted(recipes)
        errors.append(
            SpecError(
                "recipe",
                "unknown_recipe",
                f"recipe {short_repr(name)} not found",
                tuple(names),
                closest(name, names),
            )
        )
        raise SpecInvalid(errors)
    params = _resolve_params(recipe, raw.get("params", {}), errors)
    if errors:
        raise SpecInvalid(errors)

    assets = {n for n, p in recipe.params.items() if p.type == "asset"}
    inputs: dict[str, str] = {}
    steps: list[dict[str, Any]] = []
    prev = "in" if "in" in assets else None
    for i, template in enumerate(recipe.steps):
        when = template.get("when", [])
        if not all(_when(e, params) for e in ([when] if isinstance(when, str) else when)):
            continue
        refs = template.get("in", [prev] if prev else [])
        refs = [refs] if isinstance(refs, str) else list(refs)
        for ref in refs:
            if ref in assets:
                inputs[ref] = params[ref]
        step_id = template.get("id", f"r{i + 1}")
        steps.append(
            {
                "id": step_id,
                "op": template["op"],
                "in": refs,
                "args": _render(template.get("args", {}), params),
            }
        )
        prev = step_id
    spec = {
        "version": 1,
        "inputs": inputs,
        "steps": steps,
        "outputs": [prev],
        "checks": raw.get("checks", "default"),
    }
    return spec, recipe


def remap_errors(errors: list[SpecError], recipe: Recipe) -> list[SpecError]:
    """Point errors on expanded inputs back at `params.<name>`."""
    out = []
    for e in errors:
        path = e.path
        if path.startswith("inputs."):
            path = "params." + path.removeprefix("inputs.")
        elif path.startswith("steps"):
            path = f"recipe:{recipe.id}@{recipe.version}.{path}"
        out.append(SpecError(path, e.code, e.message, e.allowed, e.suggestion))
    return out
