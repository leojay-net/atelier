"""Spec validation and canonicalization.

Accepts the full DAG form and the linear shorthand, reports *all* errors at once with
precise paths, and returns a CanonicalSpec. Nothing runs until this succeeds.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from pydantic import ValidationError

from atelier.canonical import round_floats
from atelier.fmt import short_repr
from atelier.media import AmbiguousHandle, AssetInfo, AssetNotFound, InvalidHandle
from atelier.ops.catalogue import public_fields
from atelier.ops.registry import AnyOf, ArgError, OpDef, Registry
from atelier.recipes import expand as expand_recipe
from atelier.recipes import remap_errors
from atelier.spec.errors import SpecError, SpecInvalid, closest, join_path
from atelier.spec.model import CHECK_MODES, SPEC_VERSIONS, CanonicalSpec, CanonicalStep
from atelier.spec.types import literal_values

_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_FULL_KEYS = ("checks", "inputs", "outputs", "steps", "version")
_LINEAR_KEYS = ("checks", "in", "ops", "version")
_STEP_KEYS = ("args", "id", "in", "op")
LINEAR_INPUT = "in"


class AssetResolver(Protocol):
    def resolve(self, handle: str) -> AssetInfo: ...


@dataclass(frozen=True)
class SpecLimits:
    max_steps: int = 64
    max_inputs: int = 32


@dataclass
class _RawStep:
    path: str  # error-path prefix: "steps[2]" or "ops[2]"
    id: Any
    op: Any
    refs: list[Any] | None
    refs_path: str
    ref_paths: list[str]
    args: Any
    args_path: str
    opdef: OpDef | None = None
    canonical_args: dict[str, Any] | None = None


@dataclass
class _Parsed:
    version: Any
    inputs: list[tuple[str, Any, Any]]  # (path, name, handle)
    steps: list[_RawStep]
    outputs: list[tuple[str, Any]] | None  # (path, step id)
    checks: Any
    outputs_path: str = "outputs"
    input_paths: dict[str, str] = field(default_factory=dict)


def validate(
    raw: Any,
    *,
    registry: Registry,
    store: AssetResolver,
    known_checks: Collection[str] = (),
    limits: SpecLimits = SpecLimits(),
) -> CanonicalSpec:
    return _Validator(registry, store, known_checks, limits).run(raw)


class _Validator:
    def __init__(
        self,
        registry: Registry,
        store: AssetResolver,
        known_checks: Collection[str],
        limits: SpecLimits,
    ) -> None:
        self.registry = registry
        self.store = store
        self.known_checks = sorted(known_checks)
        self.limits = limits
        self.errors: list[SpecError] = []
        self.invalid_input_names: set[str] = set()

    # -- helpers -----------------------------------------------------------------------

    def err(
        self,
        path: str,
        code: str,
        message: str,
        allowed: Iterable[str] | None = None,
        suggestion: str | None = None,
    ) -> None:
        self.errors.append(
            SpecError(path, code, message, tuple(allowed) if allowed else None, suggestion)
        )

    def raise_if_errors(self) -> None:
        if self.errors:
            raise SpecInvalid(self.errors)

    def check_keys(self, obj: dict[Any, Any], allowed: tuple[str, ...], prefix: str) -> None:
        for key in obj:
            path = join_path(prefix, key)
            if key not in allowed:
                self.err(
                    path,
                    "unknown_key",
                    f"unknown key {short_repr(key)}",
                    allowed,
                    closest(key, allowed),
                )

    # -- entry -------------------------------------------------------------------------

    def run(self, raw: Any) -> CanonicalSpec:
        if not isinstance(raw, dict):
            self.err("$", "type", "spec must be a JSON object")
            self.raise_if_errors()
        if "recipe" in raw:
            full, recipe = expand_recipe(raw)
            inner = _Validator(self.registry, self.store, self.known_checks, self.limits)
            try:
                spec = inner.run(full)
            except SpecInvalid as e:
                raise SpecInvalid(remap_errors(e.errors, recipe)) from None
            return replace(spec, origin={"recipe": recipe.id, "version": recipe.version})
        if "ops" in raw:
            parsed = self.parse_linear(raw)
        elif "steps" in raw:
            parsed = self.parse_full(raw)
        else:
            self.err(
                "$", "missing_key", "spec needs 'steps' (full form) or 'in' + 'ops' (linear form)"
            )
            self.raise_if_errors()
            raise AssertionError("unreachable")

        version = self.check_version(parsed.version)
        inputs = self.check_inputs(parsed)
        self.check_steps(parsed, set(inputs) | self.invalid_input_names)
        outputs = self.check_outputs(parsed)
        checks = self.check_checks(parsed.checks)

        order: list[_RawStep] = []
        if not self.errors:
            order = self.toposort(parsed.steps)
        if not self.errors:
            self.check_media(order, inputs)
            self.check_usage(parsed, order, outputs)
        self.raise_if_errors()

        steps = []
        for s in order:
            assert s.opdef is not None and s.canonical_args is not None and s.refs is not None
            steps.append(CanonicalStep(s.id, s.opdef, tuple(s.refs), s.canonical_args))
        return CanonicalSpec(version, inputs, tuple(steps), tuple(outputs), checks)

    # -- parsing the two forms ---------------------------------------------------------

    def parse_full(self, raw: dict[str, Any]) -> _Parsed:
        self.check_keys(raw, _FULL_KEYS, "")
        inputs_raw = raw.get("inputs", {})
        if not isinstance(inputs_raw, dict):
            self.err("inputs", "type", "inputs must be an object of name → asset handle")
            inputs_raw = {}
        inputs = [(join_path("inputs", name), name, value) for name, value in inputs_raw.items()]

        steps: list[_RawStep] = []
        steps_raw = raw["steps"]
        if not isinstance(steps_raw, list) or not steps_raw:
            self.err("steps", "type", "steps must be a non-empty list")
            steps_raw = []
        for i, s in enumerate(steps_raw):
            path = f"steps[{i}]"
            if not isinstance(s, dict):
                self.err(path, "type", "each step must be an object with id, op, in, args")
                continue
            self.check_keys(s, _STEP_KEYS, path)
            for key in ("id", "op", "in"):
                if key not in s:
                    self.err(f"{path}.{key}", "missing_key", f"step needs '{key}'")
            refs: list[Any] | None = None
            ref_paths: list[str] = []
            if isinstance(s.get("in"), str):
                refs, ref_paths = [s["in"]], [f"{path}.in"]
            elif isinstance(s.get("in"), list) and s["in"]:
                refs = list(s["in"])
                ref_paths = [f"{path}.in[{j}]" for j in range(len(refs))]
            elif "in" in s:
                self.err(
                    f"{path}.in", "type", "in must be a step id, input name, or a list of them"
                )
            steps.append(
                _RawStep(
                    path,
                    s.get("id"),
                    s.get("op"),
                    refs,
                    f"{path}.in",
                    ref_paths,
                    s.get("args", {}),
                    f"{path}.args",
                )
            )

        outputs: list[tuple[str, Any]] | None = None
        if "outputs" not in raw:
            last = steps[-1].id if steps and isinstance(steps[-1].id, str) else None
            self.err(
                "outputs",
                "missing_key",
                "list the step ids to deliver",
                suggestion=(f'["{last}"]' if last else None),
            )
        elif not isinstance(raw["outputs"], list) or not raw["outputs"]:
            self.err("outputs", "type", "outputs must be a non-empty list of step ids")
        else:
            outputs = [(f"outputs[{i}]", o) for i, o in enumerate(raw["outputs"])]
        return _Parsed(raw.get("version", 1), inputs, steps, outputs, raw.get("checks", "default"))

    def parse_linear(self, raw: dict[str, Any]) -> _Parsed:
        self.check_keys(raw, _LINEAR_KEYS, "")
        inputs: list[tuple[str, Any, Any]] = []
        if "in" in raw:
            inputs.append(("in", LINEAR_INPUT, raw["in"]))
        else:
            self.err("in", "missing_key", "linear spec needs 'in': an asset handle")

        steps: list[_RawStep] = []
        ops = raw["ops"]
        if not isinstance(ops, list) or not ops:
            self.err("ops", "type", "ops must be a non-empty list of [op, {args}]")
            ops = []
        prev = LINEAR_INPUT
        for i, item in enumerate(ops):
            path = f"ops[{i}]"
            name: Any = None
            args: Any = {}
            if isinstance(item, str):
                name = item
            elif isinstance(item, list) and 1 <= len(item) <= 2 and isinstance(item[0], str):
                name = item[0]
                args = item[1] if len(item) == 2 else {}
            else:
                self.err(path, "type", 'each op must be ["op.name", {args}]')
            sid = f"s{i + 1}"
            steps.append(_RawStep(path, sid, name, [prev], path, [path], args, f"{path}.args"))
            prev = sid
        outputs = [("ops", prev)] if steps else None
        return _Parsed(
            raw.get("version", 1),
            inputs,
            steps,
            outputs,
            raw.get("checks", "default"),
            outputs_path="ops",
        )

    # -- checks ------------------------------------------------------------------------

    def check_version(self, version: Any) -> int:
        if isinstance(version, bool) or version not in SPEC_VERSIONS:
            self.err(
                "version",
                "unsupported_version",
                f"spec version {short_repr(version)} is not supported",
                [str(v) for v in SPEC_VERSIONS],
            )
            return SPEC_VERSIONS[-1]
        return int(version)

    def check_inputs(self, parsed: _Parsed) -> dict[str, AssetInfo]:
        resolved: dict[str, AssetInfo] = {}
        if len(parsed.inputs) > self.limits.max_inputs:
            self.err(
                "inputs",
                "limit",
                f"{len(parsed.inputs)} inputs exceeds the limit of {self.limits.max_inputs}",
            )
        for path, name, value in parsed.inputs:
            parsed.input_paths[name] = path
            if not isinstance(name, str) or not _ID_RE.match(name):
                self.err(
                    path,
                    "invalid_name",
                    "input names must be identifiers "
                    "(letters, digits, _; not starting with a digit)",
                )
                continue
            try:
                resolved[name] = self.store.resolve(value)
                continue
            except InvalidHandle:
                self.err(
                    path,
                    "invalid_handle",
                    f"{short_repr(value)} is not an asset handle; "
                    "upload the file and use its asset:<sha256> handle "
                    "(paths and URLs are not allowed)",
                )
            except AssetNotFound:
                self.err(path, "asset_not_found", f"{value} is not in the store")
            except AmbiguousHandle as e:
                self.err(
                    path,
                    "asset_ambiguous",
                    f"{value} matches {e.count} assets; use more hex digits",
                )
            self.invalid_input_names.add(name)
        return resolved

    def check_steps(self, parsed: _Parsed, input_names: set[str]) -> None:
        if len(parsed.steps) > self.limits.max_steps:
            self.err(
                "steps",
                "limit",
                f"{len(parsed.steps)} steps exceeds the limit of {self.limits.max_steps}",
            )
        step_ids = [s.id for s in parsed.steps if isinstance(s.id, str)]
        known_refs = sorted(input_names | set(step_ids))
        seen: dict[str, str] = {}
        for s in parsed.steps:
            if s.id is not None:
                if not isinstance(s.id, str) or not _ID_RE.match(s.id):
                    self.err(
                        f"{s.path}.id",
                        "invalid_id",
                        "step ids must be identifiers "
                        "(letters, digits, _; not starting with a digit)",
                    )
                elif s.id in seen:
                    self.err(
                        f"{s.path}.id",
                        "duplicate_id",
                        f"id {s.id!r} is already used by {seen[s.id]}",
                    )
                elif s.id in input_names:
                    self.err(f"{s.path}.id", "id_conflict", f"id {s.id!r} is also an input name")
                else:
                    seen[s.id] = s.path

            if s.op is not None:
                if not isinstance(s.op, str):
                    self.err(f"{s.path}.op", "type", "op must be a string like 'image.resize'")
                elif (opdef := self.registry.get(s.op)) is None:
                    domain = s.op.split(".")[0]
                    same_domain = [n for n in self.registry.names() if n.startswith(f"{domain}.")]
                    self.err(
                        f"{s.path}.op",
                        "unknown_op",
                        f"unknown op {s.op!r}",
                        same_domain,
                        closest(s.op, self.registry.names()),
                    )
                else:
                    s.opdef = opdef

            if s.refs is not None:
                for ref, ref_path in zip(s.refs, s.ref_paths, strict=True):
                    if not isinstance(ref, str):
                        self.err(ref_path, "type", "references must be strings")
                    elif ref == s.id:
                        self.err(ref_path, "self_ref", "a step cannot take its own output")
                    elif ref not in known_refs:
                        self.err(
                            ref_path,
                            "unknown_ref",
                            f"{ref!r} is not an input or step id",
                            known_refs,
                            closest(ref, known_refs),
                        )
                if s.opdef is not None and not s.opdef.accepts(len(s.refs)):
                    self.err(
                        s.refs_path,
                        "arity",
                        f"{s.opdef.name} takes {s.opdef.arity_text()} input(s), got {len(s.refs)}",
                    )

            if s.opdef is not None:
                if isinstance(s.args, dict):
                    n_inputs = len(s.refs) if s.refs is not None else None
                    s.canonical_args = self.check_args(
                        s.opdef, s.args, s.args_path, n_inputs, s.refs_path
                    )
                else:
                    self.err(s.args_path, "type", "args must be an object")

    def check_args(
        self, opdef: OpDef, args: dict[str, Any], path: str, n_inputs: int | None, refs_path: str
    ) -> dict[str, Any] | None:
        fields = opdef.params.model_fields
        public = public_fields(opdef)
        try:
            model = opdef.params.model_validate(args)
        except ValidationError as exc:
            for d in exc.errors(include_url=False):
                loc = d["loc"]
                p = path
                for x in loc:
                    p = f"{p}[{x}]" if isinstance(x, int) else join_path(p, x)
                name = loc[0] if loc and isinstance(loc[0], str) else None
                kind = d["type"]
                if kind == "extra_forbidden":
                    names = sorted(public)
                    self.err(
                        p,
                        "unknown_arg",
                        f"{opdef.name} has no arg {name!r}"
                        + ("" if names else " (it takes no args)"),
                        names,
                        closest(name, names),
                    )
                elif kind == "enum_mismatch":
                    ctx = d.get("ctx") or {}
                    options = tuple(str(a) for a in ctx.get("allowed", []))
                    self.err(
                        p, "enum_mismatch", d["msg"], options, closest(ctx.get("value"), options)
                    )
                elif kind == "missing":
                    self.err(p, "missing_arg", f"required by {opdef.name}")
                elif kind == "literal_error" and name in fields:
                    allowed = literal_values(fields[name].annotation) or ()
                    self.err(
                        p,
                        "enum_mismatch",
                        f"{short_repr(d['input'])} invalid",
                        allowed,
                        closest(d["input"], allowed),
                    )
                else:
                    msg = d["msg"].removeprefix("Value error, ")
                    self.err(p, "invalid_arg", f"{short_repr(d['input'])} invalid: {msg}")
            return None
        if (
            opdef.check_inputs is not None
            and n_inputs is not None
            and (problem := opdef.check_inputs(model, n_inputs)) is not None
        ):
            self.err(refs_path, "arity", problem)
        dumped: dict[str, Any] = model.model_dump(mode="json")
        if opdef.canonicalize is not None:
            try:
                dumped = opdef.canonicalize(dumped)
            except ArgError as e:
                self.err(f"{path}.{e.field}", e.code, e.message, e.allowed, e.suggestion)
                return None
        canonical: dict[str, Any] = round_floats(dumped, opdef.float_precision)
        return canonical

    def check_outputs(self, parsed: _Parsed) -> list[str]:
        if parsed.outputs is None:
            return []
        step_ids = sorted(s.id for s in parsed.steps if isinstance(s.id, str))
        outputs: list[str] = []
        for path, out in parsed.outputs:
            if not isinstance(out, str) or out not in step_ids:
                self.err(
                    path,
                    "unknown_output",
                    f"{short_repr(out)} is not a step id",
                    step_ids,
                    closest(out, step_ids),
                )
            elif out in outputs:
                self.err(path, "duplicate_output", f"{out!r} is listed twice")
            else:
                outputs.append(out)
        return outputs

    def check_checks(self, checks: Any) -> str | tuple[str, ...]:
        if isinstance(checks, str):
            if checks not in CHECK_MODES:
                self.err(
                    "checks",
                    "enum_mismatch",
                    f"{checks!r} invalid",
                    [*CHECK_MODES, "[check names]"],
                    closest(checks, CHECK_MODES),
                )
            return checks
        if isinstance(checks, list):
            names: list[str] = []
            for i, name in enumerate(checks):
                if not isinstance(name, str) or name not in self.known_checks:
                    self.err(
                        f"checks[{i}]",
                        "unknown_check",
                        f"unknown check {short_repr(name)}",
                        self.known_checks,
                        closest(name, self.known_checks),
                    )
                else:
                    names.append(name)
            return tuple(sorted(set(names)))
        self.err("checks", "type", "checks must be default|strict|none or a list of check names")
        return "default"

    def toposort(self, steps: list[_RawStep]) -> list[_RawStep]:
        """Kahn's algorithm, always picking the earliest-declared ready step."""
        by_id = {s.id: s for s in steps}
        pending = list(steps)
        done: set[str] = set()
        order: list[_RawStep] = []
        while pending:
            ready = next(
                (s for s in pending if all(r in done or r not in by_id for r in s.refs or [])), None
            )
            if ready is None:
                cycle = ", ".join(sorted(s.id for s in pending))
                self.err("steps", "cycle", f"steps {cycle} depend on each other in a cycle")
                return []
            pending.remove(ready)
            done.add(ready.id)
            order.append(ready)
        return order

    def check_media(self, order: list[_RawStep], inputs: dict[str, AssetInfo]) -> None:
        types = {name: info.media for name, info in inputs.items()}
        for s in order:
            assert s.opdef is not None and s.refs is not None
            compatible = True
            for j, (ref, ref_path) in enumerate(zip(s.refs, s.ref_paths, strict=True)):
                expected = s.opdef.input_type(j)
                actual = types[ref]
                if actual not in expected if isinstance(expected, AnyOf) else actual != expected:
                    compatible = False
                    self.err(
                        ref_path,
                        "media_mismatch",
                        f"{s.opdef.name} input {j + 1} must be "
                        f"{s.opdef.input_label(j)}, but {ref!r} is {actual.value}",
                    )
            in_types = [types[r] for r in s.refs]
            types[s.id] = s.opdef.output_for(in_types)
            if compatible and s.opdef.media_check is not None and s.canonical_args is not None:
                problem = s.opdef.media_check(s.canonical_args, in_types)
                if problem is not None:
                    self.err(
                        f"{s.args_path}.{problem.field}",
                        problem.code,
                        problem.message,
                        problem.allowed,
                        problem.suggestion,
                    )

    def check_usage(self, parsed: _Parsed, order: list[_RawStep], outputs: list[str]) -> None:
        by_id = {s.id: s for s in order}
        needed: set[str] = set()
        stack = list(outputs)
        while stack:
            ref = stack.pop()
            if ref in needed:
                continue
            needed.add(ref)
            if ref in by_id:
                stack.extend(by_id[ref].refs or [])
        for path, name, _ in parsed.inputs:
            if name not in needed:
                self.err(path, "unused_input", f"input {name!r} is not used by any output")
        for s in order:
            if s.id not in needed:
                self.err(
                    f"{s.path}.id",
                    "unused_step",
                    f"step {s.id!r} does not feed any output; add it to outputs or remove it",
                )
