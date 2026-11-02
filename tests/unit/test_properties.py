"""Property-based tests (docs/09-testing.md §9.1)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from atelier.engine.cache import step_key
from atelier.engine.store import LocalStore
from atelier.media import MediaType
from atelier.ops.registry import Artifact, Determinism, OpContext, Params, Registry, op
from atelier.spec.errors import SpecInvalid
from atelier.spec.types import Fraction, Timecode
from atelier.spec.validate import validate

REG = Registry()


class TrimParams(Params):
    start: Timecode
    end: Timecode | None = None
    gain: Fraction = 1.0
    mode: Literal["fast", "precise"] = "fast"


@op(
    name="t.trim",
    version="1.0.0",
    inputs=[MediaType.TEXT],
    output=MediaType.TEXT,
    determinism=Determinism.BIT_EXACT,
    params=TrimParams,
    summary=lambda a: "",
    description="test op",
    registry=REG,
)
def trim(ctx: OpContext, inputs: Sequence[Artifact], p: TrimParams) -> Artifact:
    raise NotImplementedError


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> LocalStore:
    return LocalStore(tmp_path_factory.mktemp("store"))


def _key(store: LocalStore, handle: str, args: dict[str, Any]) -> str:
    spec = validate({"in": handle, "ops": [["t.trim", args]]}, registry=REG, store=store)
    step = spec.steps[0]
    return step_key(step.op, step.args, [spec.inputs["in"].hash], {})


def _canon(store: LocalStore, raw: dict[str, Any]) -> dict[str, Any]:
    spec = validate(raw, registry=REG, store=store)
    return spec.to_json()


seconds = st.integers(min_value=0, max_value=3 * 3600 - 1)


@given(seconds)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_equivalent_timecodes_share_cache_key(store: LocalStore, total: int) -> None:
    handle = store.put_bytes(b"x\n").handle
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    forms: list[object] = [total, float(total), f"{total}s", f"{h}:{m:02d}:{s:02d}"]
    if h == 0:
        forms.append(f"{m}:{s:02d}")
    keys = {_key(store, handle, {"start": f}) for f in forms}
    assert len(keys) == 1


def test_percent_and_fraction_share_cache_key(store: LocalStore) -> None:
    handle = store.put_bytes(b"x\n").handle
    assert _key(store, handle, {"start": 0, "gain": "12%"}) == _key(
        store, handle, {"start": 0, "gain": 0.12}
    )


def test_explicit_defaults_share_cache_key(store: LocalStore) -> None:
    handle = store.put_bytes(b"x\n").handle
    assert _key(store, handle, {"start": 1}) == _key(
        store, handle, {"start": 1, "end": None, "gain": 1, "mode": "fast"}
    )


@given(st.floats(min_value=0, max_value=1e6, allow_nan=False), st.sampled_from(["fast", "precise"]))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_canonicalization_is_idempotent(store: LocalStore, start: float, mode: str) -> None:
    handle = store.put_bytes(b"x\n").handle
    once = _canon(store, {"in": handle, "ops": [["t.trim", {"start": start, "mode": mode}]]})
    again = _canon(
        store,
        {
            "version": once["version"],
            "inputs": once["inputs"],
            "steps": [{k: v for k, v in s.items() if k != "op_version"} for s in once["steps"]],
            "outputs": once["outputs"],
            "checks": once["checks"],
        },
    )
    assert once == again


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=8),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=6), children, max_size=4)
    ),
    max_leaves=20,
)
spec_like = st.fixed_dictionaries(
    {},
    optional={
        "version": json_values,
        "inputs": json_values | st.dictionaries(st.text(max_size=4), json_values),
        "steps": json_values
        | st.lists(
            st.dictionaries(st.sampled_from(["id", "op", "in", "args", "bogus"]), json_values)
        ),
        "outputs": json_values,
        "checks": json_values,
        "in": json_values,
        "ops": json_values | st.lists(json_values),
    },
)


@given(st.one_of(json_values, spec_like))
@example({"": None, "recipe": None})  # an empty key once produced an empty error path
@example({"steps": [{"\n": None}]})  # a control character in an echoed key once broke the format
@example({"in": "x", "ops": [["debug.upper", {"a\u2028b": 1}]]})
@settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_invalid_specs_always_produce_path_errors(store: LocalStore, raw: Any) -> None:
    try:
        validate(raw, registry=REG, store=store)
    except SpecInvalid as exc:
        assert exc.errors
        for error in exc.errors:
            assert error.path and error.code and error.message
            assert "\n" not in error.line()
    # A random input that happens to be valid is fine too; crashing is not.
