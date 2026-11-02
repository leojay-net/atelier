"""Deterministic renderings of the op registry.

`catalogue` is the one-line-per-op block for the system prompt; `describe` is the
on-demand detail behind the describe_op tool. Both are sorted and timestamp-free so the
prompt prefix stays byte-stable and cacheable.
"""

from __future__ import annotations

import json
from typing import Any

import yaml
from pydantic.fields import FieldInfo

from atelier.ops.registry import OpDef, Registry
from atelier.presets import Preset, all_presets
from atelier.recipes import Param, Recipe, all_recipes
from atelier.spec.types import literal_values, type_label


def _default(field: FieldInfo) -> object:
    return field.get_default(call_default_factory=True)


def _param_sig(name: str, field: FieldInfo) -> str:
    if field.is_required():
        return name
    default = _default(field)
    values = literal_values(field.annotation)
    if values is not None and str(default) in values:
        ordered = [str(default), *(v for v in values if v != str(default))]
        return f"{name}={'|'.join(ordered)}"
    if default is None:
        return f"{name}?"
    return f"{name}={json.dumps(default, ensure_ascii=False)}"


def _io(opdef: OpDef) -> str:
    names = [opdef.input_label(i) for i in range(len(opdef.inputs))]
    if opdef.variadic:
        names[-1] += "+"
    return f"{','.join(names)}→{opdef.output_label}"


def public_fields(opdef: OpDef) -> dict[str, FieldInfo]:
    return {
        name: field
        for name, field in opdef.params.model_fields.items()
        if not (
            isinstance(field.json_schema_extra, dict) and field.json_schema_extra.get("internal")
        )
    }


def signature(opdef: OpDef) -> str:
    fields = sorted(public_fields(opdef).items(), key=lambda kv: not kv[1].is_required())
    params = ",".join(_param_sig(n, f) for n, f in fields)
    return f"{opdef.name}({params}) {_io(opdef)}"


def catalogue(registry: Registry, *, include_hidden: bool = False) -> str:
    return "\n".join(
        f"{signature(d)} — {d.description}" for d in registry.ops(include_hidden=include_hidden)
    )


def describe(opdef: OpDef) -> str:
    lines = [
        f"{signature(opdef)} · v{opdef.version} · {opdef.determinism.value}",
        opdef.doc,
    ]
    fields = public_fields(opdef)
    if fields:
        lines.append("params:")
        for name, field in fields.items():
            line = f"  {name}: {type_label(field)}"
            if not field.is_required():
                line += f" = {json.dumps(_default(field), ensure_ascii=False)}"
            if field.description:
                line += f" — {field.description}"
            lines.append(line)
    return "\n".join(lines)


# -- presets and recipes -----------------------------------------------------------------


def preset_line(p: Preset) -> str:
    return f"preset {p.id} [{p.kind} v{p.version}] — {p.description}"


def _recipe_param(p: Param) -> str:
    if p.type == "asset":
        return f"{p.name}:{p.media.value if p.media else 'asset'}"
    if p.required:
        return p.name
    if p.values is not None:
        ordered = [str(p.default), *(v for v in p.values if v != str(p.default))]
        return f"{p.name}={'|'.join(ordered)}"
    return f"{p.name}={json.dumps(p.default, ensure_ascii=False)}"


def recipe_signature(r: Recipe) -> str:
    return f"recipe {r.id}({','.join(_recipe_param(p) for p in r.params.values())})"


def full_catalogue(registry: Registry) -> str:
    """Everything the model may use, one line each: ops, presets, recipes."""
    lines = [catalogue(registry)]
    lines += [preset_line(p) for p in sorted(all_presets().values(), key=lambda p: (p.kind, p.id))]
    lines += [f"{recipe_signature(r)} — {r.description}" for _, r in sorted(all_recipes().items())]
    return "\n".join(line for line in lines if line)


def describe_name(registry: Registry, name: str) -> str | None:
    """Detail for an op, preset or recipe (the describe_op tool)."""
    if (opdef := registry.get(name)) is not None:
        return describe(opdef)
    if (preset := all_presets().get(name)) is not None:
        settings = yaml.safe_dump(preset.settings, sort_keys=True, default_flow_style=None)
        return f"{preset_line(preset)}\nsettings:\n" + "\n".join(
            f"  {line}" for line in settings.strip().splitlines()
        )
    if (recipe := all_recipes().get(name)) is not None:
        lines = [f"{recipe_signature(recipe)} v{recipe.version} — {recipe.description}", "params:"]
        for p in recipe.params.values():
            kind = f"{p.type}({p.media.value})" if p.media else p.type
            line = f"  {p.name}: {kind}"
            if p.values:
                line += f" of {'|'.join(p.values)}"
            if not p.required:
                line += f" = {json.dumps(p.default, ensure_ascii=False)}"
            lines.append(line)
        lines.append("steps: " + " → ".join(_recipe_step(s) for s in recipe.steps))
        return "\n".join(lines)
    return None


def _recipe_step(step: dict[str, Any]) -> str:
    when = step.get("when")
    if when is None:
        return str(step["op"])
    conditions = [when] if isinstance(when, str) else list(when)
    return f"{step['op']} (if {' and '.join(conditions)})"
