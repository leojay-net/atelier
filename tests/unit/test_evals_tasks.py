"""The eval task file, checked offline (no tokens): composition, and that every task is solvable.

A task whose reference solution fails its own graders is a broken task, not a bad model answer,
so this is what keeps the (expensive) live eval honest.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from atelier.agent.loop import AgentResult
from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits
from atelier.evals.graders import Context, grade
from atelier.evals.task import KINDS, SPLITS, Task, TaskFilter, bind_reference, load_tasks

EVALS = Path(__file__).resolve().parents[1] / "evals"
SUITES = {
    name: load_tasks(EVALS / "tasks" / f"{name}.yaml") for name in ("image", "video", "docs")
}  # all have the same shape: 40 tasks, 20/14/3/3 by kind, 22/8/10 by split
TASKS = SUITES["image"]
suites = pytest.mark.parametrize("suite", list(SUITES))
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
needs_soffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="needs LibreOffice")
_LIBREOFFICE = ("doc.convert", "xlsx.recalc", "invoice_from_template", "report_docx_to_pdf")


def _marks(name: str, task: Task) -> list[pytest.MarkDecorator]:
    if name == "video":
        return [needs_ffmpeg]
    if name == "docs" and any(word in json.dumps(task.reference) for word in _LIBREOFFICE):
        return [needs_soffice]  # printing a document to PDF or recalculating a workbook
    return []


SOLVABLE = [
    pytest.param(t, id=f"{name}/{t.id}", marks=_marks(name, t))
    for name, tasks in SUITES.items()
    for t in tasks
    if not t.asks
]


@suites
def test_suite_composition(suite: str) -> None:
    """docs/09 §9.4: ~40 tasks per domain; ~50% recipe-shaped, ~35% custom, ~15% ambiguous."""
    tasks = SUITES[suite]
    assert len(tasks) == 40
    kinds = Counter(t.kind for t in tasks)
    assert kinds["recipe"] == 20 and kinds["custom"] == 14
    assert kinds["ambiguous"] + kinds["aesthetic"] == 6
    assert set(kinds) <= set(KINDS)
    splits = Counter(t.split for t in tasks)
    assert splits == {"train": 22, "validation": 8, "test": 10}
    assert set(splits) == set(SPLITS)


@suites
def test_every_split_covers_every_kind_it_can(suite: str) -> None:
    """The held-out split must not be a single easy kind."""
    by_split = {s: {t.kind for t in SUITES[suite] if t.split == s} for s in SPLITS}
    assert by_split["test"] == set(KINDS)
    assert by_split["train"] == set(KINDS)


@suites
def test_inputs_exist_and_ids_are_unique(suite: str) -> None:
    tasks = SUITES[suite]
    assert len({t.id for t in tasks}) == len(tasks)
    for t in tasks:
        for name, filename in t.inputs.items():
            assert (EVALS / "inputs" / filename).is_file(), (t.id, name)


@suites
def test_ambiguous_tasks_expect_a_question_and_the_rest_expect_output(suite: str) -> None:
    for t in SUITES[suite]:
        assert t.asks == (t.kind == "ambiguous"), t.id
        if t.asks:
            assert t.reference is None
        else:
            assert t.graders[0]["type"] == "agent_ok" and t.reference is not None


@suites
def test_aesthetic_tasks_carry_a_rubric_and_only_they_do(suite: str) -> None:
    for t in SUITES[suite]:
        assert bool(t.rubric) == (t.kind == "aesthetic"), t.id


@suites
def test_every_request_is_written_like_a_person_would(suite: str) -> None:
    """No leaked implementation vocabulary in requests (op names, handles, spec words)."""
    leaks = re.compile(
        r"\b(image|video|audio|doc|pdf|xlsx|pptx)\.[a-z]+|asset:|\brecipes?\b|\bpresets?\b|\bspec\b"
        r"|run_pipeline"
    )
    for t in SUITES[suite]:
        assert not leaks.search(t.request.lower()), t.id


def test_task_filter() -> None:
    assert {t.split for t in TaskFilter(split="test").apply(TASKS)} == {"test"}
    assert len(TaskFilter(kind="recipe").apply(TASKS)) == 20
    assert [t.id for t in TaskFilter(ids=("ask-crop",)).apply(TASKS)] == ["ask-crop"]
    assert len(TaskFilter(limit=3).apply(TASKS)) == 3


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Engine:
    return Engine.open(
        tmp_path_factory.mktemp("evals") / "home", run_limits=RunLimits(wall_seconds=60)
    )


def solve(engine: Engine, task: Task) -> tuple[AgentResult, list[Any]]:
    handles = {
        n: engine.store.put_file(EVALS / "inputs" / f).handle for n, f in task.inputs.items()
    }
    spec = bind_reference(task, handles)
    result = engine.run(spec)
    outcome = AgentResult(
        status="ok" if result.passed else "checks_failed",
        outputs={i: r.handle for i, r in result.outputs.items()},
        spec=spec,
        engine_result=result,
    )
    return outcome, grade(task.graders, Context(outcome, engine))


@pytest.mark.parametrize("task", SOLVABLE)
def test_reference_solution_passes_every_grader(engine: Engine, task: Task) -> None:
    outcome, results = solve(engine, task)
    failed = [f"{g.name}: {g.detail}" for g in results if not g.passed]
    assert not failed, failed
    assert outcome.ok
