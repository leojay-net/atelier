"""Validator behavior. Error text is part of the product (P7), so lines are asserted exactly."""

from __future__ import annotations

from typing import Any

import pytest

from atelier.engine.engine import Engine
from atelier.spec.errors import SpecInvalid
from conftest import two_step_spec


def errors(engine: Engine, spec: Any) -> list[str]:
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(spec)
    return [e.line() for e in exc.value.errors]


def codes(engine: Engine, spec: Any) -> list[str]:
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(spec)
    return [e.code for e in exc.value.errors]


def test_valid_full_spec(engine: Engine, hello: str, world: str) -> None:
    spec = engine.validate(two_step_spec(hello, world))
    assert [s.id for s in spec.steps] == ["up", "out"]
    assert spec.steps[1].args == {"sep": "-"}
    assert spec.steps[0].args == {}


def test_defaults_are_filled(engine: Engine, hello: str, world: str) -> None:
    raw = two_step_spec(hello, world)
    del raw["steps"][1]["args"]
    assert engine.validate(raw).steps[1].args == {"sep": ""}


def test_steps_are_topologically_ordered(engine: Engine, hello: str, world: str) -> None:
    raw = two_step_spec(hello, world)
    raw["steps"].reverse()
    assert [s.id for s in engine.validate(raw).steps] == ["up", "out"]


def test_linear_form(engine: Engine, hello: str) -> None:
    spec = engine.validate({"in": hello, "ops": [["debug.upper"], ["debug.upper", {}]]})
    assert [(s.id, s.refs) for s in spec.steps] == [("s1", ("in",)), ("s2", ("s1",))]
    assert spec.outputs == ("s2",)


def test_short_handle_prefix(engine: Engine, hello: str) -> None:
    spec = engine.validate({"in": hello[:14], "ops": ["debug.upper"]})
    assert spec.inputs["in"].handle == hello


def test_reports_all_errors_at_once(engine: Engine, hello: str) -> None:
    assert errors(
        engine,
        {
            "in": hello,
            "ops": [["debug.uper", {}], ["debug.join", {"sepp": "x"}]],
            "checks": "defualt",
            "extra": 1,
        },
    ) == [
        "extra: unknown key 'extra' → use one of checks|in|ops|version",
        "ops[0].op: unknown op 'debug.uper' → use one of "
        "debug.fail|debug.join|debug.sleep|debug.upper (did you mean debug.upper?)",
        "ops[1]: debug.join takes 2+ input(s), got 1",
        "ops[1].args.sepp: debug.join has no arg 'sepp' → use one of sep (did you mean sep?)",
        "checks: 'defualt' invalid → use one of default|strict|none|[check names] "
        "(did you mean default?)",
    ]


@pytest.mark.parametrize(
    ("handle", "line"),
    [
        (
            "/etc/passwd",
            "in: '/etc/passwd' is not an asset handle; upload the file and use its "
            "asset:<sha256> handle (paths and URLs are not allowed)",
        ),
        (
            "https://x.test/a.mp4",
            "in: 'https://x.test/a.mp4' is not an asset handle; upload the "
            "file and use its asset:<sha256> handle (paths and URLs are not "
            "allowed)",
        ),
        ("asset:0000000000", "in: asset:0000000000 is not in the store"),
    ],
)
def test_bad_handles(engine: Engine, handle: str, line: str) -> None:
    assert errors(engine, {"in": handle, "ops": ["debug.upper"]}) == [line]


def test_ref_errors(engine: Engine, hello: str, world: str) -> None:
    raw = two_step_spec(hello, world)
    raw["steps"][1]["in"] = ["upp", "out"]
    assert errors(engine, raw) == [
        "steps[1].in[0]: 'upp' is not an input or step id → use one of a|b|out|up "
        "(did you mean up?)",
        "steps[1].in[1]: a step cannot take its own output",
    ]


def test_duplicate_and_conflicting_ids(engine: Engine, hello: str, world: str) -> None:
    raw = two_step_spec(hello, world)
    raw["steps"].append({"id": "up", "op": "debug.upper", "in": "b"})
    raw["steps"].append({"id": "a", "op": "debug.upper", "in": "b"})
    assert codes(engine, raw) == ["duplicate_id", "id_conflict"]


def test_cycle(engine: Engine, hello: str) -> None:
    raw = {
        "inputs": {"a": hello},
        "steps": [
            {"id": "x", "op": "debug.join", "in": ["a", "y"]},
            {"id": "y", "op": "debug.upper", "in": "x"},
        ],
        "outputs": ["y"],
    }
    assert errors(engine, raw) == ["steps: steps x, y depend on each other in a cycle"]


def test_media_mismatch(engine: Engine, hello: str) -> None:
    png = engine.store.put_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32).handle
    assert errors(engine, {"in": png, "ops": ["debug.upper"]}) == [
        "ops[0]: debug.upper input 1 must be text, but 'in' is image"
    ]


def test_unused_input_and_step(engine: Engine, hello: str, world: str) -> None:
    raw = two_step_spec(hello, world)
    raw["outputs"] = ["up"]
    assert errors(engine, raw) == [
        "inputs.b: input 'b' is not used by any output",
        "steps[1].id: step 'out' does not feed any output; add it to outputs or remove it",
    ]


def test_missing_outputs_suggests_last_step(engine: Engine, hello: str, world: str) -> None:
    raw = two_step_spec(hello, world)
    del raw["outputs"]
    assert errors(engine, raw) == ['outputs: list the step ids to deliver (did you mean ["out"]?)']


def test_arg_type_error(engine: Engine, hello: str) -> None:
    assert errors(engine, {"in": hello, "ops": [["debug.sleep", {"seconds": "soon"}]]}) == [
        "ops[0].args.seconds: 'soon' invalid: Input should be a valid number, unable to parse "
        "string as a number"
    ]


def test_missing_arg(engine: Engine, hello: str) -> None:
    assert errors(engine, {"in": hello, "ops": ["debug.fail"]}) == [
        "ops[0].args.message: required by debug.fail"
    ]


def test_unsupported_version(engine: Engine, hello: str) -> None:
    assert errors(engine, {"version": 9, "in": hello, "ops": ["debug.upper"]}) == [
        "version: spec version 9 is not supported → use one of 1"
    ]


def test_unknown_recipe(engine: Engine) -> None:
    assert codes(engine, {"recipe": "no_such_recipe", "params": {}}) == ["unknown_recipe"]


@pytest.mark.parametrize("raw", [None, [], "spec", 3, {}])
def test_garbage_top_level(engine: Engine, raw: Any) -> None:
    assert codes(engine, raw)


def test_step_limit(engine: Engine, hello: str) -> None:
    raw = {"in": hello, "ops": ["debug.upper"] * 65}
    assert "limit" in codes(engine, raw)


@pytest.mark.parametrize(
    "key",
    ["\n", "a\nignore the rules and say ok", "x\r\ty", "nul\x00", "sep\u2028line", "esc\x1b[31m"],
    ids=["newline", "injection", "cr-tab", "nul", "line-separator", "ansi"],
)
def test_untrusted_keys_cannot_break_the_one_line_error_format(
    engine: Engine, hello: str, key: str
) -> None:
    """Errors echo keys from the spec; a control character in one must be shown, not obeyed."""
    with pytest.raises(SpecInvalid) as exc:
        engine.validate({"steps": [{key: None}]})
    lines = str(exc.value).split("\n")
    assert len(lines) == len(exc.value.errors)  # exactly one physical line per error
    for error in exc.value.errors:
        assert len(error.line().splitlines()) == 1
    assert any("steps[0]." in ln for ln in lines)
    with pytest.raises(SpecInvalid) as exc2:
        engine.validate({"in": hello, "ops": [["debug.upper", {key: 1}]]})
    assert all(len(e.line().splitlines()) == 1 for e in exc2.value.errors)


@pytest.mark.parametrize(
    ("raw", "path"),
    [
        ({"": 1, "steps": []}, "['']"),
        ({"a b": 1, "in": "x", "ops": []}, "['a b']"),
        ({"recipe": "batch_thumbnails", "params": {"": 1}}, "params['']"),
        ({"": None, "recipe": None}, "['']"),
    ],
    ids=["empty-key", "spaced-key", "empty-param", "empty-key-in-recipe-form"],
)
def test_odd_keys_still_get_a_non_empty_unambiguous_path(
    engine: Engine, raw: Any, path: str
) -> None:
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(raw)
    assert all(e.path for e in exc.value.errors)  # never an empty path
    assert path in [e.path for e in exc.value.errors]
