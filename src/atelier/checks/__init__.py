"""Deterministic quality checks (docs/06-verification.md §6.1).

A check is a pure function of an output's metadata (dimensions, bytes, layout boxes,
export settings…) that returns pass/warn/fail with a message and a fix hint, or None
when it does not apply. Ops measure; checks judge. No pixels are decoded here.

Selection per spec `checks` field:
* "default": the domain's default checks plus those the export preset lists
* "strict":  every check for the domain; warnings count as failures
* "none":    nothing
* [names]:   exactly those
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from atelier.media import MediaType

BUILTIN_MODULES = ("atelier.checks.image", "atelier.checks.video", "atelier.checks.docs")


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str = ""
    hint: str | None = None

    def line(self) -> str:
        text = f"{self.status.value.upper()} {self.name}: {self.message}"
        return f"{text} → hint: {self.hint}" if self.hint else text


CheckFn = Callable[[Mapping[str, Any]], CheckResult | None]


@dataclass(frozen=True)
class CheckDef:
    name: str
    media: frozenset[MediaType]
    fn: CheckFn
    description: str
    default: bool


CHECKS: dict[str, CheckDef] = {}


def check(
    name: str,
    media: MediaType | tuple[MediaType, ...],
    description: str,
    *,
    default: bool = False,
) -> Callable[[CheckFn], CheckFn]:
    def register(fn: CheckFn) -> CheckFn:
        if name in CHECKS:
            raise ValueError(f"check {name!r} already registered")
        kinds = frozenset(media) if isinstance(media, tuple) else frozenset({media})
        CHECKS[name] = CheckDef(name, kinds, fn, description, default)
        return fn

    return register


def load_builtin_checks() -> dict[str, CheckDef]:
    for module in BUILTIN_MODULES:
        importlib.import_module(module)
    return CHECKS


def select(mode: str | Sequence[str], media: MediaType, meta: Mapping[str, Any]) -> list[CheckDef]:
    checks = load_builtin_checks()
    if isinstance(mode, str):
        if mode == "none":
            return []
        if mode == "strict":
            return [c for c in checks.values() if media in c.media]
        wanted = set((meta.get("export") or {}).get("checks", []))  # None for a non-export output
        return [c for c in checks.values() if media in c.media and (c.default or c.name in wanted)]
    return [checks[n] for n in mode if n in checks and media in checks[n].media]


def run_checks(
    mode: str | Sequence[str], media: MediaType, meta: Mapping[str, Any]
) -> list[CheckResult]:
    results = []
    for c in select(mode, media, meta):
        result = c.fn(meta)
        if result is None:
            continue
        if mode == "strict" and result.status is Status.WARN:
            result = CheckResult(result.name, Status.FAIL, result.message, result.hint)
        results.append(result)
    return results


def summarize(results: Sequence[CheckResult]) -> str:
    """One line, e.g. `checks 3/4 · FAIL text_fit: … → hint: …`. Passes are only counted."""
    passed = sum(r.status is Status.PASS for r in results)
    head = f"checks {passed}/{len(results)}"
    problems = [r.line() for r in results if r.status is not Status.PASS]
    if not results:
        return "checks none applicable"
    return " · ".join([head, *problems]) if problems else f"{head} pass"
