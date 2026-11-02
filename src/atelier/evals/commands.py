"""`atelier eval`: run the suite (spends tokens) or report on a saved run (free).

atelier eval run [--split S] [--kind K] [--id ID]... [--limit N] [--judge] [--out FILE]
atelier eval baseline [same filters as run] [--image IMAGE]   (uses tokens, runs Docker)
atelier eval report FILE [--split S] [--baseline FILE] [--previous FILE]

`run` uses its own engine home (`<home>/eval`) so eval inputs and caches never mix with real
assets. The exit code is 0 unless `--gate` is given and a Phase 2 exit criterion fails (3).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from atelier.agent.client import AnthropicMessages, MissingCredentials
from atelier.agent.config import DEFAULT_MODEL, EFFORTS, AgentConfig
from atelier.engine.engine import Engine
from atelier.evals.baseline import run_baseline
from atelier.evals.runner import (
    EvalReport,
    GateCheck,
    TaskResult,
    acceptance,
    check_gate,
    format_gate,
    format_report,
    run_eval,
)
from atelier.evals.sandbox import (
    DEFAULT_IMAGE,
    SandboxError,
    SandboxFactory,
    docker_factory,
    image_available,
)
from atelier.evals.task import KINDS, SPLITS, Task, TaskError, TaskFilter, load_tasks

SUITES = ("image", "video", "docs")
TASKS_DIR = Path("tests/evals/tasks")
AV_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".m4a", ".ogg", ".flac"}
DOC_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx"}
# what the baseline's sandbox needs for each kind of task: (a shell test, what to call it)
TOOLS = {
    "av": ("command -v ffmpeg && command -v ffprobe", "ffmpeg and ffprobe"),
    "docs": (
        "python3 -c 'import docx, openpyxl, pptx, pypdf' && command -v soffice",
        "python-docx, openpyxl, python-pptx, pypdf and LibreOffice",
    ),
}
DEFAULT_INPUTS = Path("tests/evals/inputs")
EXIT_GATE = 3


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("eval", help="agent eval suite: run it (uses tokens) or report on a run")
    esub = p.add_subparsers(dest="eval_command", required=True)

    r = esub.add_parser("run", help="run the tasks through the agent (uses tokens)")
    r.add_argument("--suite", choices=SUITES, default="image", help="which task file to use")
    r.add_argument("--tasks", type=Path, help="a task file (overrides --suite)")
    r.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    r.add_argument("--split", choices=SPLITS)
    r.add_argument("--kind", choices=KINDS)
    r.add_argument("--id", dest="ids", action="append", default=[], metavar="TASK_ID")
    r.add_argument("--limit", type=int)
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--effort", choices=EFFORTS, default="high")
    r.add_argument(
        "--judge", action="store_true", help="also run the LLM rubric grader (more tokens)"
    )
    r.add_argument("--out", type=Path, default=Path("eval-report.json"))
    r.add_argument("--gate", action="store_true", help=f"exit {EXIT_GATE} if the exit gate fails")
    r.set_defaults(fn=cmd_eval_run)

    b = esub.add_parser(
        "baseline",
        help="the same tasks done by a general coding agent in a Docker sandbox (uses tokens)",
    )
    b.add_argument("--suite", choices=SUITES, default="image", help="which task file to use")
    b.add_argument("--tasks", type=Path, help="a task file (overrides --suite)")
    b.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    b.add_argument("--split", choices=SPLITS)
    b.add_argument("--kind", choices=KINDS)
    b.add_argument("--id", dest="ids", action="append", default=[], metavar="TASK_ID")
    b.add_argument("--limit", type=int)
    b.add_argument("--model", default=DEFAULT_MODEL)
    b.add_argument("--effort", choices=EFFORTS, default="high")
    b.add_argument(
        "--image", default=DEFAULT_IMAGE, help="sandbox image (docker/baseline.Dockerfile)"
    )
    b.add_argument("--judge", action="store_true", help="also run the LLM rubric grader")
    b.add_argument("--out", type=Path, default=Path("baseline-report.json"))
    b.set_defaults(fn=cmd_eval_baseline)

    q = esub.add_parser("report", help="summarize a saved run and evaluate the gate (free)")
    q.add_argument("report", type=Path)
    q.add_argument("--split", choices=SPLITS, default="test")
    q.add_argument("--baseline", type=Path, help="a baseline run, for the token/pass-rate criteria")
    q.add_argument("--previous", type=Path, help="the last accepted run, for the acceptance rule")
    q.add_argument("--gate", action="store_true")
    q.set_defaults(fn=cmd_eval_report)


def _print_gate(checks: list[GateCheck], title: str) -> bool:
    print(f"{title}:\n{format_gate(checks)}")
    return all(c.passed is not False for c in checks)


def _tasks_path(args: argparse.Namespace) -> Path:
    return Path(args.tasks) if args.tasks else TASKS_DIR / f"{args.suite}.yaml"


def _kinds_needed(tasks: list[Task]) -> set[str]:
    """Which tool sets the tasks' input files imply (video and audio, documents)."""
    suffixes = {Path(f).suffix.lower() for t in tasks for f in t.inputs.values()}
    return {k for k, group in (("av", AV_SUFFIXES), ("docs", DOC_SUFFIXES)) if suffixes & group}


def _uses_av(tasks: list[Task]) -> bool:
    return "av" in _kinds_needed(tasks)


def _missing_tools(factory: SandboxFactory, kinds: set[str]) -> str | None:
    """The baseline must have the tools a developer would use; None when it does."""
    sandbox = factory()
    try:
        for kind in sorted(kinds):
            command, name = TOOLS[kind]
            if sandbox.run(command).exit_code != 0:
                return name
    finally:
        sandbox.close()
    return None


def _line(r: TaskResult) -> str:
    turns = r.record.turns if r.record else 0
    cost = f"{r.units:.0f}u" if r.units is not None else "-"
    mark = "PASS" if r.passed else "FAIL"
    return f"{mark}  {r.task_id:<28} {r.status:<13} {turns} turns · {cost}" + (
        "" if r.passed else f"  ← {r.failure()}"
    )


def cmd_eval_run(engine: Engine, args: argparse.Namespace) -> int:
    from atelier.cli import EXIT_FAILED, EXIT_INVALID, _fail

    try:
        tasks = TaskFilter(args.split, args.kind, tuple(args.ids), args.limit).apply(
            load_tasks(_tasks_path(args))
        )
    except (OSError, TaskError) as e:
        return _fail(f"cannot load tasks: {e}", EXIT_INVALID)
    if not tasks:
        return _fail("no tasks match those filters", EXIT_INVALID)
    config = AgentConfig(model=args.model, effort=args.effort)
    work = Engine.open(engine.home / "eval")
    judge = AnthropicMessages(fallbacks=False) if args.judge else None
    print(
        f"eval · {len(tasks)} task{'s' * (len(tasks) != 1)} · {config.model}"
        f" · effort {config.effort} · rubric judge {'on' if judge else 'off'}"
    )
    try:
        report = run_eval(
            tasks,
            engine=work,
            client=AnthropicMessages(fallbacks=config.fallbacks),
            config=config,
            inputs_dir=args.inputs,
            judge_client=judge,
            on_result=lambda r: print(_line(r), flush=True),
        )
    except MissingCredentials as e:
        return _fail(str(e), EXIT_FAILED)
    except TaskError as e:
        return _fail(str(e), EXIT_INVALID)
    report.save(args.out)
    print(f"\n{format_report(report, args.split)}\nsaved → {args.out}")
    split = args.split or "test"
    if not report.subset(split):
        return 0
    ok = _print_gate(check_gate(report, split=split), f"exit gate ({split} split)")
    return EXIT_GATE if args.gate and not ok else 0


def cmd_eval_baseline(engine: Engine, args: argparse.Namespace) -> int:
    from atelier.cli import EXIT_FAILED, EXIT_INVALID, _fail

    try:
        tasks = TaskFilter(args.split, args.kind, tuple(args.ids), args.limit).apply(
            load_tasks(_tasks_path(args))
        )
    except (OSError, TaskError) as e:
        return _fail(f"cannot load tasks: {e}", EXIT_INVALID)
    if not tasks:
        return _fail("no tasks match those filters", EXIT_INVALID)
    if not image_available(args.image):
        return _fail(
            f"sandbox image {args.image!r} not found. Build it once (needs network):\n"
            f"  docker build -f docker/baseline.Dockerfile -t {args.image} docker/",
            EXIT_FAILED,
        )
    factory = docker_factory(args.image)
    if (kinds := _kinds_needed(tasks)) and (missing := _missing_tools(factory, kinds)):
        return _fail(
            f"sandbox image {args.image!r} lacks {missing}, which these tasks need. Rebuild it "
            f"(needs network):\n  docker build -f docker/baseline.Dockerfile "
            f"-t {args.image} docker/",
            EXIT_FAILED,
        )
    config = AgentConfig(model=args.model, effort=args.effort)
    judge = AnthropicMessages(fallbacks=False) if args.judge else None
    print(
        f"baseline · {len(tasks)} task{'s' * (len(tasks) != 1)} · {config.model}"
        f" · effort {config.effort} · bash only, in Docker with no network"
    )
    try:
        report = run_baseline(
            tasks,
            client=AnthropicMessages(fallbacks=config.fallbacks),
            config=config,
            inputs_dir=args.inputs,
            engine=Engine.open(engine.home / "eval"),
            sandbox_factory=factory,
            judge_client=judge,
            on_result=lambda r: print(_line(r), flush=True),
        )
    except (MissingCredentials, SandboxError) as e:
        return _fail(str(e), EXIT_FAILED)
    except TaskError as e:
        return _fail(str(e), EXIT_INVALID)
    report.save(args.out)
    print(f"\n{format_report(report, args.split)}\nsaved → {args.out}")
    return 0


def cmd_eval_report(engine: Engine, args: argparse.Namespace) -> int:
    from atelier.cli import EXIT_INVALID, _fail

    try:
        report = EvalReport.load(args.report)
        baseline = EvalReport.load(args.baseline) if args.baseline else None
        previous = EvalReport.load(args.previous) if args.previous else None
    except (OSError, ValueError, KeyError) as e:
        return _fail(f"cannot read report: {e}", EXIT_INVALID)
    print(format_report(report, args.split))
    ok = _print_gate(check_gate(report, baseline, args.split), f"exit gate ({args.split} split)")
    if previous is not None:
        ok &= _print_gate(acceptance(report, previous, args.split), "acceptance vs previous run")
    return EXIT_GATE if args.gate and not ok else 0
