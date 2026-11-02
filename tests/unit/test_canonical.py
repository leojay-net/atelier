import math

import pytest

from atelier.canonical import canonical_json, round_floats


def test_sorted_compact_utf8() -> None:
    assert (
        canonical_json({"b": 1, "a": ["é", None, True]}) == '{"a":["é",null,true],"b":1}'.encode()
    )


def test_negative_zero_folds() -> None:
    assert canonical_json({"x": -0.0}) == canonical_json({"x": 0.0})


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite(bad: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": bad})


def test_rejects_non_string_keys() -> None:
    with pytest.raises(TypeError):
        canonical_json({1: "a"})


def test_round_floats_nested() -> None:
    assert round_floats({"a": [0.1234567, {"b": 1.0000004}]}, 6) == {"a": [0.123457, {"b": 1.0}]}
