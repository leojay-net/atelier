"""Eval tasks (docs/09 §9.4): a request, its inputs, graders, and a reference solution.

    - id: og-card-title
      split: train                      # train | validation | test
      kind: recipe                      # recipe | custom | ambiguous | aesthetic
      request: "Make a link-preview card from this photo titled 'Launch day'."
      inputs: {photo: photo_landscape.jpg}
      graders:
        - {type: dims, width: 1200, height: 630}
        - {type: format, formats: [JPEG]}
        - {type: recipe, name: batch_thumbnails, soft: true}     # reported, not required
      reference: {recipe: batch_thumbnails, params: {in: "${photo}", size: og_image}}

`agent_ok` (the job finished and every check passed) is added to every task that is not an
ask-a-question task. `reference` is a known-good spec with `${input}` placeholders; the offline
tests run it and require every non-soft grader to pass, so a task nobody can solve, or a
grader nobody can satisfy, is caught without spending a token.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SPLITS = ("train", "validation", "test")
KINDS = ("recipe", "custom", "ambiguous", "aesthetic")
_KEYS = {"id", "split", "kind", "request", "inputs", "graders", "rubric", "reference", "budget"}


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class Task:
    id: str
    split: str
    kind: str
    request: str
    inputs: dict[
        str, str
    ]  # input name → file under the inputs dir; order = order shown to the agent
    graders: tuple[dict[str, Any], ...]
    rubric: tuple[str, ...] = ()
    reference: Any = None
    budget: int | None = None  # billed input-token equivalents (see runner.units); None = default

    @property
    def asks(self) -> bool:
        return any(g["type"] == "asks_question" for g in self.graders)


def _task(raw: Any, known_graders: set[str]) -> Task:
    if not isinstance(raw, dict) or "id" not in raw:
        raise TaskError(f"a task must be a mapping with an id, got {raw!r}")
    tid = str(raw["id"])
    if unknown := set(raw) - _KEYS:
        raise TaskError(f"{tid}: unknown keys {sorted(unknown)}")
    for key in ("split", "kind", "request", "graders"):
        if key not in raw:
            raise TaskError(f"{tid}: missing {key}")
    if raw["split"] not in SPLITS:
        raise TaskError(f"{tid}: split must be one of {'|'.join(SPLITS)}")
    if raw["kind"] not in KINDS:
        raise TaskError(f"{tid}: kind must be one of {'|'.join(KINDS)}")
    inputs = raw.get("inputs") or {}
    if not isinstance(inputs, dict) or not all(isinstance(v, str) for v in inputs.values()):
        raise TaskError(f"{tid}: inputs must map names to file names")
    graders = [dict(g) for g in raw["graders"]]
    for g in graders:
        if g.get("type") not in known_graders:
            raise TaskError(f"{tid}: unknown grader type {g.get('type')!r}")
    asks = any(g["type"] == "asks_question" for g in graders)
    if not asks and not any(g["type"] == "agent_ok" for g in graders):
        graders.insert(0, {"type": "agent_ok"})
    if not asks and raw.get("reference") is None:
        raise TaskError(f"{tid}: a task that expects output needs a reference spec")
    return Task(
        id=tid,
        split=raw["split"],
        kind=raw["kind"],
        request=str(raw["request"]).strip(),
        inputs=dict(inputs),
        graders=tuple(graders),
        rubric=tuple(str(r) for r in raw.get("rubric") or ()),
        reference=raw.get("reference"),
        budget=raw.get("budget"),
    )


def load_tasks(path: Path) -> list[Task]:
    from atelier.evals.graders import GRADERS

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TaskError(f"{path}: expected a list of tasks")
    tasks = [_task(t, set(GRADERS)) for t in raw]
    seen: set[str] = set()
    for t in tasks:
        if t.id in seen:
            raise TaskError(f"duplicate task id {t.id!r}")
        seen.add(t.id)
    return tasks


def bind_reference(task: Task, handles: dict[str, str]) -> Any:
    """The reference spec with `${name}` replaced by that input's asset handle."""
    text = json.dumps(task.reference)
    for name, handle in handles.items():
        text = text.replace("${" + name + "}", handle)
    return json.loads(text)


@dataclass
class TaskFilter:
    split: str | None = None
    kind: str | None = None
    ids: tuple[str, ...] = ()
    limit: int | None = None

    def apply(self, tasks: list[Task]) -> list[Task]:
        out = [
            t
            for t in tasks
            if (not self.split or t.split == self.split)
            and (not self.kind or t.kind == self.kind)
            and (not self.ids or t.id in self.ids)
        ]
        return out[: self.limit] if self.limit else out
