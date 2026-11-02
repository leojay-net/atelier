"""Param types that normalize units at validation time, plus field introspection helpers.

Normalizing here is what makes `{"start": "0:05"}` and `{"start": 5}` canonicalize to the
same args, and therefore the same cache key.
"""

from __future__ import annotations

import re
import types
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BeforeValidator
from pydantic.fields import FieldInfo
from pydantic_core import PydanticCustomError

_NUM_RE = re.compile(r"^\d+(\.\d+)?$")
_INT_RE = re.compile(r"^\d+$")


def parse_timecode(value: object) -> object:
    """Seconds as a number, or "[[HH:]MM:]SS[.fff]" with an optional trailing "s"."""
    if isinstance(value, bool):
        raise ValueError("expected a timecode like 00:05 or 5.0")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("timecode must be >= 0")
        return float(value)
    if isinstance(value, str):
        parts = value.strip().removesuffix("s").split(":")
        head, last = parts[:-1], parts[-1]
        if (
            len(parts) <= 3
            and _NUM_RE.match(last)
            and all(_INT_RE.match(p) for p in head)
            and all(int(p) < 60 for p in head[1:])
            and (not head or float(last) < 60)
        ):
            total = float(last)
            for i, part in enumerate(reversed(head), start=1):
                total += int(part) * 60**i
            return total
    raise ValueError(f"{value!r} is not a timecode; use seconds (5.0) or [HH:]MM:SS[.mmm]")


def parse_fraction(value: object) -> object:
    """A number, or a percentage string such as "12%" (→ 0.12)."""
    if isinstance(value, str) and value.strip().endswith("%"):
        number = value.strip()[:-1].strip()
        try:
            return float(number) / 100
        except ValueError:
            pass
        raise ValueError(f'{value!r} is not a percentage; use e.g. "12%" or 0.12')
    return value


_NAMED_COLORS = {"white": "#ffffffff", "black": "#000000ff", "transparent": "#00000000"}
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_ASPECT = re.compile(r"^(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)$")


def parse_color(value: object) -> object:
    """ "#rgb", "#rrggbb", "#rrggbbaa" or white/black/transparent → canonical "#rrggbbaa"."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _NAMED_COLORS:
            return _NAMED_COLORS[v]
        if _HEX_COLOR.match(v):
            h = v[1:]
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            return f"#{h}ff" if len(h) == 6 else f"#{h}"
    raise ValueError(
        f"{value!r} is not a color; use #rrggbb, #rrggbbaa, white, black or transparent"
    )


def parse_aspect(value: object) -> object:
    """ "16:9", "4/5", "1x1" or a positive number (width / height)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ratio = float(value)
    elif isinstance(value, str) and (m := _ASPECT.match(value.strip())):
        w, h = float(m.group(1)), float(m.group(2))
        ratio = w / h if h else 0.0
    else:
        raise ValueError(f'{value!r} is not an aspect ratio; use e.g. "16:9" or 1.777')
    if not 0.05 <= ratio <= 20:
        raise ValueError(f"aspect ratio {ratio:g} is out of range (0.05–20)")
    return ratio


def rgba(color: str) -> tuple[int, int, int, int]:
    """Canonical "#rrggbbaa" → RGBA tuple."""
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16), int(color[7:9], 16))


def choice(value: str, allowed: Sequence[str], what: str) -> str:
    """Field-validator helper for values whose allowed set is only known at runtime."""
    if value not in allowed:
        raise PydanticCustomError(
            "enum_mismatch",
            "{what} {shown} invalid",
            {"what": what, "shown": repr(value), "value": value, "allowed": list(allowed)},
        )
    return value


Timecode = Annotated[float, BeforeValidator(parse_timecode)]
Fraction = Annotated[float, BeforeValidator(parse_fraction)]
Color = Annotated[str, BeforeValidator(parse_color)]
Aspect = Annotated[float, BeforeValidator(parse_aspect)]

_UNIT_LABELS = {
    parse_timecode: "timecode",
    parse_fraction: "fraction",
    parse_color: "color",
    parse_aspect: "aspect",
}


def literal_values(annotation: Any) -> tuple[str, ...] | None:
    """The allowed values of a Literal (or Optional[Literal]) annotation, as strings."""
    if get_origin(annotation) is Literal:
        return tuple(str(v) for v in get_args(annotation))
    if get_origin(annotation) in (Union, types.UnionType):
        for arg in get_args(annotation):
            if (values := literal_values(arg)) is not None:
                return values
    return None


def _label(annotation: Any, metadata: Sequence[Any] = ()) -> str:
    for meta in metadata:
        if isinstance(meta, BeforeValidator) and meta.func in _UNIT_LABELS:
            return _UNIT_LABELS[meta.func]
    origin = get_origin(annotation)
    if origin is Annotated:
        base, *extra = get_args(annotation)
        return _label(base, extra)
    if (values := literal_values(annotation)) is not None and origin is Literal:
        return "|".join(values)
    if origin in (Union, types.UnionType):
        return " | ".join(_label(a) for a in get_args(annotation))
    if origin in (list, tuple):
        args = [a for a in get_args(annotation) if a is not Ellipsis]
        inner = ",".join(_label(a) for a in args) if origin is tuple else _label(args[0])
        return f"[{inner}]" if origin is tuple else f"[{inner}…]"
    if annotation is type(None):
        return "null"
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def type_label(field: FieldInfo) -> str:
    return _label(field.annotation, field.metadata)
