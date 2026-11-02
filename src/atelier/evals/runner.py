"""Run tasks through the agent, report, and evaluate the Phase 2 exit gate (docs/08, docs/09 §9.4).

Every job is one agent run with no plan cache: the suite measures the model, not the cache.

Cost is compared in *billed input-token equivalents* (`units`): uncached input, cache writes at
1.25x, cache reads at 0.1x and output at 5x. That weighting is the list-price ratio for the
current models and makes agent runs and a baseline run comparable without picking a currency.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from atelier.agent.client import MessagesClient, MissingCredentials
from atelier.agent.config import AgentConfig
from atelier.agent.loop import Job, run_job
from atelier.agent.prompt import catalogue_version, system_blocks
from atelier.agent.telemetry import JobRecord, summarize
from atelier.engine.engine import Engine
from atelier.evals import rubric
from atelier.evals.graders import PORTABLE, Context, GraderResult, grade
from atelier.evals.task import Task, TaskError
from atelier.media import HandleError

DEFAULT_BUDGET_UNITS = 20_000
GATE_MAX_MEDIAN_TURNS = 3
GATE_MIN_FIRST_PASS = 0.85
GATE_MIN_CACHE_READ = 0.90
GATE_MIN_TOKEN_ADVANTAGE = 5.0
MAX_MEDIAN_UNITS_RISE = 0.10


def units(record: JobRecord) -> float:
    return record.units()


@dataclass
class TaskResult:
    task_id: str
    split: str
    kind: str
    status: str
    detail: str
    graders: list[GraderResult]
    record: JobRecord | None
    units: float | None
    over_budget: bool

    @property
    def passed(self) -> bool:
        return self.status not in ("error", "budget", "refused") and all(
            g.passed for g in self.graders if not g.soft
        )

    def failure(self) -> str:
        bad = [g for g in self.graders if not g.passed and not g.soft]
        first = f"{bad[0].name}: {bad[0].detail}" if bad else self.detail
        return f"{self.status} · {first}" if first else self.status

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "split": self.split,
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
            "graders": [asdict(g) for g in self.graders],
            "record": self.record.to_json() if self.record else None,
            "units": self.units,
            "over_budget": self.over_budget,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> TaskResult:
        return cls(
            task_id=d["task_id"],
            split=d["split"],
            kind=d["kind"],
            status=d["status"],
            detail=d["detail"],
            graders=[GraderResult(**g) for g in d["graders"]],
            record=JobRecord.from_json(d["record"]) if d["record"] else None,
            units=d["units"],
            over_budget=d["over_budget"],
        )


@dataclass
class EvalReport:
    model: str
    effort: str
    catalogue: str
    results: list[TaskResult]

    def subset(self, split: str | None = None) -> list[TaskResult]:
        return [r for r in self.results if split is None or r.split == split]

    def save(self, path: Path) -> None:
        data = {
            "model": self.model,
            "effort": self.effort,
            "catalogue": self.catalogue,
            "results": [r.to_json() for r in self.results],
        }
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> EvalReport:
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            d["model"], d["effort"], d["catalogue"], [TaskResult.from_json(r) for r in d["results"]]
        )


def run_eval(
    tasks: Sequence[Task],
    *,
    engine: Engine,
    client: MessagesClient,
    config: AgentConfig,
    inputs_dir: Path,
    judge_client: MessagesClient | None = None,
    on_result: Callable[[TaskResult], None] | None = None,
) -> EvalReport:
    version = catalogue_version(system_blocks(engine.registry))
    results = []
    for task in tasks:
        result = _run_task(task, engine, client, config, inputs_dir, judge_client)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return EvalReport(config.model, config.effort, version, results)


def _run_task(
    task: Task,
    engine: Engine,
    client: MessagesClient,
    config: AgentConfig,
    inputs_dir: Path,
    judge_client: MessagesClient | None,
) -> TaskResult:
    handles = {}
    for name, filename in task.inputs.items():
        path = inputs_dir / filename
        if not path.is_file():
            raise TaskError(f"{task.id}: input {name!r} → {path} does not exist")
        handles[name] = engine.store.put_file(path).handle
    job = Job(task.request, assets=tuple(handles.values()))
    try:
        result = run_job(job, engine=engine, client=client, config=config)
    except (MissingCredentials, HandleError, TaskError):
        raise  # not a bad answer from the model: the run itself is broken
    except Exception as e:  # one flaky request must not lose the whole run
        return TaskResult(
            task.id,
            task.split,
            task.kind,
            "error",
            f"{type(e).__name__}: {e}",
            [],
            None,
            None,
            False,
        )
    graders = grade(task.graders, Context(result, engine))
    if task.rubric:
        graders.append(
            grade_rubric(task, result.outputs, list(handles.values()), engine, config, judge_client)
        )
    record = result.record
    used = units(record) if record is not None else None
    budget = task.budget or DEFAULT_BUDGET_UNITS
    return TaskResult(
        task.id,
        task.split,
        task.kind,
        result.status,
        result.detail,
        graders,
        record,
        used,
        used is not None and used > budget,
    )


def grade_rubric(
    task: Task,
    outputs: dict[str, str],
    originals: list[str],
    engine: Engine,
    config: AgentConfig,
    judge_client: MessagesClient | None,
) -> GraderResult:
    if judge_client is None:
        return GraderResult("rubric", True, "skipped (no judge configured)", soft=True)
    if not outputs:
        return GraderResult("rubric", False, "nothing to judge")
    return rubric.judge(
        judge_client,
        model=config.model,
        engine=engine,
        request=task.request,
        rubric=task.rubric,
        originals=originals[:2],
        outputs=list(outputs.values())[:2],
    )


# -- aggregates ---------------------------------------------------------------------------------


def _rate(results: Sequence[TaskResult]) -> float | None:
    return sum(r.passed for r in results) / len(results) if results else None


def _mean_units_of_passed(results: Sequence[TaskResult]) -> float | None:
    used = [r.units for r in results if r.passed and r.units is not None]
    return statistics.fmean(used) if used else None


def portable_passed(r: TaskResult) -> bool:
    """Passed on the graders that apply to any system (the delivered files, or asking instead of
    guessing). This is the definition of "completed" used to compare the agent with the baseline:
    the baseline has no spec and no engine checks, so it can only be judged this way."""
    graders = [g for g in r.graders if g.name in PORTABLE and not g.soft]
    return (
        r.status not in ("error", "budget", "refused")
        and bool(graders)
        and all(g.passed for g in graders)
    )


def _portable_rate(results: Sequence[TaskResult]) -> float | None:
    return sum(portable_passed(r) for r in results) / len(results) if results else None


def _portable_mean_units(results: Sequence[TaskResult]) -> float | None:
    used = [r.units for r in results if portable_passed(r) and r.units is not None]
    return statistics.fmean(used) if used else None


def summarize_report(report: EvalReport, split: str | None = None) -> dict[str, Any]:
    rs = report.subset(split)
    records = [r.record for r in rs if r.record is not None]
    kinds = sorted({r.kind for r in rs})
    return {
        **summarize(records),
        "tasks": len(rs),
        "passed": sum(r.passed for r in rs),
        "pass_rate": _rate(rs),
        "pass_rate_by_kind": {k: _rate([r for r in rs if r.kind == k]) for k in kinds},
        "over_budget": sum(r.over_budget for r in rs),
        "mean_units_per_passed_task": _mean_units_of_passed(rs),
        "portable_pass_rate": _portable_rate(rs),
        "mean_units_per_portable_passed_task": _portable_mean_units(rs),
    }


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool | None  # None: cannot be judged from the data given
    value: str
    target: str


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def check_gate(
    report: EvalReport, baseline: EvalReport | None = None, split: str = "test"
) -> list[GateCheck]:
    """The Phase 2 exit criteria (docs/08), on the held-out split by default."""
    s = summarize_report(report, split)
    turns, images = s["median_turns"], s["median_images"]
    first, cache = s["first_pass_rate"], s["cache_read_ratio"]
    checks = [
        GateCheck(
            "median turns per job",
            None if turns is None else turns <= GATE_MAX_MEDIAN_TURNS,
            "n/a" if turns is None else f"{turns:g}",
            f"≤ {GATE_MAX_MEDIAN_TURNS}",
        ),
        GateCheck(
            "median images per job",
            None if images is None else images == 0,
            "n/a" if images is None else f"{images:g}",
            "0",
        ),
        GateCheck(
            "first-pass check rate",
            None if first is None else first >= GATE_MIN_FIRST_PASS,
            _pct(first),
            f"≥ {GATE_MIN_FIRST_PASS:.0%}",
        ),
        GateCheck(
            "prefix cache-read ratio",
            None if cache is None else cache >= GATE_MIN_CACHE_READ,
            _pct(cache),
            f"≥ {GATE_MIN_CACHE_READ:.0%}",
        ),
    ]
    if baseline is None:
        checks.append(GateCheck("tokens vs baseline", None, "no baseline given", "≥ 5x lower"))
        return checks
    # Both sides are judged on the portable graders only, so "completed" means the same thing.
    b = summarize_report(baseline, split)
    mine, theirs = (
        s["mean_units_per_portable_passed_task"],
        b["mean_units_per_portable_passed_task"],
    )
    advantage = theirs / mine if mine and theirs else None
    checks.append(
        GateCheck(
            "tokens per completed job vs baseline",
            None if advantage is None else advantage >= GATE_MIN_TOKEN_ADVANTAGE,
            "n/a" if advantage is None else f"{advantage:.1f}x lower",
            f"≥ {GATE_MIN_TOKEN_ADVANTAGE:g}x lower",
        )
    )
    mine_rate, base_rate = s["portable_pass_rate"], b["portable_pass_rate"]
    checks.append(
        GateCheck(
            "pass rate vs baseline (portable graders)",
            None if mine_rate is None or base_rate is None else mine_rate >= base_rate,
            f"{_pct(mine_rate)} vs {_pct(base_rate)}",
            "equal or better",
        )
    )
    return checks


def acceptance(new: EvalReport, old: EvalReport, split: str = "test") -> list[GateCheck]:
    """docs/09 §9.4: no pass-rate regression; median units per job may not rise > 10%
    without a matching quality gain."""
    a, b = summarize_report(new, split), summarize_report(old, split)
    new_rate, old_rate = a["pass_rate"], b["pass_rate"]
    rate_ok = None if new_rate is None or old_rate is None else new_rate >= old_rate
    med = [
        statistics.median(u)
        for u in ([r.units for r in rep.subset(split) if r.units is not None] for rep in (new, old))
        if u
    ]
    if len(med) == 2 and med[1] > 0:
        rise = med[0] / med[1] - 1
        improved = bool(new_rate is not None and old_rate is not None and new_rate > old_rate)
        # rounded: 1100/1000 - 1 is 0.10000000000000009, and exactly +10% is allowed
        tokens_ok: bool | None = round(rise, 9) <= MAX_MEDIAN_UNITS_RISE or improved
        rise_text = f"{rise:+.0%}"
    else:
        tokens_ok, rise_text = None, "n/a"
    return [
        GateCheck(
            "pass rate did not regress",
            rate_ok,
            f"{_pct(new_rate)} vs {_pct(old_rate)}",
            "≥ previous",
        ),
        GateCheck(
            "median tokens per job",
            tokens_ok,
            rise_text,
            f"≤ +{MAX_MEDIAN_UNITS_RISE:.0%} or better quality",
        ),
    ]


def format_gate(checks: Sequence[GateCheck]) -> str:
    mark = {True: "PASS", False: "FAIL", None: "n/a "}
    return "\n".join(f"  {mark[c.passed]}  {c.name}: {c.value} (target {c.target})" for c in checks)


def format_report(report: EvalReport, split: str | None = None) -> str:
    s = summarize_report(report, split)
    where = f"split {split}" if split else "all splits"
    kinds = " · ".join(f"{k} {_pct(v)}" for k, v in s["pass_rate_by_kind"].items())
    lines = [
        f"eval · {report.model} · effort {report.effort} · catalogue {report.catalogue} · {where}",
        f"passed {s['passed']}/{s['tasks']} ({_pct(s['pass_rate'])})"
        + (f" · {kinds}" if kinds else ""),
        f"turns median {s['median_turns']} · images median {s['median_images']} · "
        f"first-pass checks {_pct(s['first_pass_rate'])} · "
        f"prefix cache-read {_pct(s['cache_read_ratio'])}",
        "mean cost per passed task: "
        + (
            "n/a"
            if s["mean_units_per_passed_task"] is None
            else f"{s['mean_units_per_passed_task']:.0f} units"
        )
        + f" · over budget: {s['over_budget']}",
    ]
    failed = [r for r in report.subset(split) if not r.passed]
    if failed:
        lines.append("failed:")
        lines += [f"  {r.task_id}: {r.failure()}" for r in failed]
    return "\n".join(lines)
