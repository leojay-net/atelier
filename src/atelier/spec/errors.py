"""Validation errors (principle P7: errors are instructions).

Each error names the exact path, a stable code, and when possible the allowed values
and a best guess. `line()` is the compact form the model sees.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

MAX_ALLOWED_SHOWN = 20
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_NAMED = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def join_path(prefix: str, key: object) -> str:
    """`prefix.key`, or `prefix['odd key']` when the key is not a plain identifier.

    Spec keys are untrusted. Pasted in verbatim, an empty key gives an empty path and a key with
    spaces, dots or newlines gives an ambiguous one; quoting them keeps every path non-empty
    and unambiguous.
    """
    name = key if isinstance(key, str) else repr(key)
    if _IDENT.match(name):
        return f"{prefix}.{name}" if prefix else name
    return f"{prefix}[{name!r}]"


def _escape(match: re.Match[str]) -> str:
    char = match.group()
    if char in _NAMED:
        return _NAMED[char]
    return f"\\x{ord(char):02x}" if ord(char) < 256 else f"\\u{ord(char):04x}"


def one_line(text: str) -> str:
    """Make text safe for a one-line error: control characters (newlines included) become
    visible escapes. Paths and messages echo keys and values from the spec, which is untrusted,
    and the model reads errors line by line, so a key such as "a<newline>ignore the rules" must
    not become a new line."""
    return _CONTROL.sub(_escape, text)


@dataclass(frozen=True)
class SpecError:
    path: str
    code: str
    message: str
    allowed: tuple[str, ...] | None = None
    suggestion: str | None = None

    def line(self) -> str:
        text = one_line(f"{self.path}: {self.message}")
        if self.allowed:
            shown = [one_line(a) for a in self.allowed[:MAX_ALLOWED_SHOWN]]
            if len(self.allowed) > MAX_ALLOWED_SHOWN:
                shown.append("…")
            text += f" → use one of {'|'.join(shown)}"
        if self.suggestion:
            text += f" (did you mean {one_line(self.suggestion)}?)"
        return text

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path, "code": self.code, "message": self.message}
        if self.allowed is not None:
            data["allowed"] = list(self.allowed)
        if self.suggestion is not None:
            data["suggestion"] = self.suggestion
        return data


class SpecInvalid(Exception):
    def __init__(self, errors: list[SpecError]) -> None:
        super().__init__("\n".join(e.line() for e in errors))
        self.errors = errors


def closest(value: object, options: Iterable[str]) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    options = list(options)
    prefixed = [o for o in options if o.startswith(value)]
    if len(prefixed) == 1:
        return prefixed[0]
    matches = difflib.get_close_matches(value, options, n=1, cutoff=0.6)
    return matches[0] if matches else None
