"""CLI commands that involve the model (`ask`) or report on its use (`stats`).

Kept out of `atelier.cli` so that module's promise stays true: nothing in it costs tokens.
`register` adds these commands to the same parser.

atelier ask PROMPT [TARGET...] [--each GLOB]   a plain-language request, run by the agent
atelier stats [--last N] [--json]              tokens, turns, cache and check metrics
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from atelier.agent.client import MissingCredentials
from atelier.agent.config import DEFAULT_MODEL, EFFORTS, AgentConfig
from atelier.agent.loop import AgentResult, BatchJobResult, Job, run_batch_job, run_job
from atelier.agent.plan_cache import PlanCache
from atelier.agent.providers import PROVIDERS, build_adapter
from atelier.agent.telemetry import JobRecord, Telemetry, format_summary, summarize
from atelier.checks import summarize as summarize_checks
from atelier.engine.engine import Engine, format_result

EXIT_NEEDS_INPUT = 4
REJECTED_CREDENTIALS = "the Anthropic API rejected the credentials; check ANTHROPIC_API_KEY"


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("ask", help="plain-language request, run by the agent (uses tokens)")
    p.add_argument("prompt")
    p.add_argument("targets", nargs="*", help="files or asset handles the request is about")
    p.add_argument(
        "--each",
        metavar="GLOB",
        help="run the request on every matching file: one agent run on the first, "
        "then the engine on all (TARGETs are then shared by every file, e.g. a logo)",
    )
    p.add_argument("--out", default="out", help="directory for outputs (default: ./out)")
    p.add_argument("--provider", choices=PROVIDERS, default="anthropic")
    p.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"default is Claude's {DEFAULT_MODEL!r}; pass "
        "a model name that matches --provider for anything else"
    )
    p.add_argument("--effort", choices=EFFORTS, default="high")
    p.add_argument("--no-fallbacks", action="store_true", help="do not re-run refusals elsewhere")
    p.add_argument("--no-plan-cache", action="store_true", help="always ask the model")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("stats", help="token, turn, cache and check metrics from past jobs")
    p.add_argument("--last", type=int, default=None, metavar="N", help="only the last N jobs")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_stats)


def _usage_line(rec: JobRecord) -> str:
    cost = rec.cost()
    return (
        f"{rec.turns} turn{'s' * (rec.turns != 1)} · "
        f"in {rec.total('input_tokens')} · "
        f"cache read/write {rec.total('cache_read_tokens')}/{rec.total('cache_write_tokens')} · "
        f"out {rec.total('output_tokens')}" + (f" · ${cost:.4f}" if cost is not None else "")
    )


def _headline(res: AgentResult) -> None:
    rec = res.record
    head = res.status + (f" · {res.detail}" if res.detail else "")
    if rec is not None:
        head += " · plan cache hit (0 tokens)" if rec.plan_cache_hit else f" · {_usage_line(rec)}"
    print(head)
    if res.summary:
        print(res.summary)
    for w in res.warnings:
        print(f"warning: {w}")
    if res.question:
        print(f"question: {res.question}")


def _report(engine: Engine, res: AgentResult, out_dir: Path) -> None:
    _headline(res)
    if res.engine_result is not None:
        print(format_result(res.engine_result))
        out_dir.mkdir(parents=True, exist_ok=True)
        for out_id, rep in res.engine_result.outputs.items():
            dest = out_dir / f"{out_id}{rep.ext}"
            shutil.copyfile(engine.store.path(rep.hash), dest)
            print(f"→ {dest}")


def _exit_code(res: AgentResult) -> int:
    from atelier.cli import EXIT_CHECKS, EXIT_FAILED

    return {"ok": 0, "checks_failed": EXIT_CHECKS, "needs_input": EXIT_NEEDS_INPUT}.get(
        res.status, EXIT_FAILED
    )


def _report_batch(engine: Engine, batch: BatchJobResult, files: list[Path], out_dir: Path) -> int:
    from atelier.cli import EXIT_FAILED

    _headline(batch.agent)
    if batch.error:
        print(f"batch not run: {batch.error}", file=sys.stderr)
        return EXIT_FAILED
    if not batch.items:
        return _exit_code(batch.agent)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = failed = 0
    for src, item in zip(files, batch.items, strict=True):
        if item.result is None:
            errors += 1
            print(f"error · {src}: {item.error}")
            continue
        for out_id, rep in item.result.outputs.items():
            suffix = "" if len(item.result.outputs) == 1 else f".{out_id}"
            shutil.copyfile(engine.store.path(rep.hash), out_dir / f"{src.stem}{suffix}{rep.ext}")
        if not item.result.passed:
            failed += 1
            lines = [summarize_checks(c) for c in item.result.checks.values()]
            print(f"fail · {src}: {' | '.join(lines)}")
    print(f"{len(files) - errors}/{len(files)} ran · {failed} failed checks · {errors} errors")
    return EXIT_FAILED if errors else 0


def cmd_ask(engine: Engine, args: argparse.Namespace) -> int:
    import anthropic

    from atelier.cli import EXIT_FAILED, EXIT_INVALID, _asset, _fail
    from atelier.media import HandleError

    config = AgentConfig(model=args.model, effort=args.effort, fallbacks=not args.no_fallbacks)
    telemetry = Telemetry(engine.home / "telemetry")
    plan_cache = None if args.no_plan_cache else PlanCache(engine.home / "plans")
    out_dir = Path(args.out)
    try:
        targets = [_asset(engine, t).handle for t in args.targets]
        files: list[Path] = []
        if args.each:
            files = sorted(p for p in Path().glob(args.each) if p.is_file())
            if not files:
                return _fail(f"--each {args.each!r} matched no files", EXIT_INVALID)
        adapter = build_adapter(args.provider, fallbacks=config.fallbacks)
        if args.each:
            each = [engine.store.put_file(f).handle for f in files]
            batch = run_batch_job(
                args.prompt,
                each,
                fixed=targets,
                engine=engine,
                adapter=adapter,
                config=config,
                telemetry=telemetry,
                plan_cache=plan_cache,
            )
            return _report_batch(engine, batch, files, out_dir)
        result = run_job(
            Job(args.prompt, tuple(targets)),
            engine=engine,
            adapter=adapter,
            config=config,
            telemetry=telemetry,
            plan_cache=plan_cache,
        )
    except (ValueError, HandleError) as e:
        return _fail(str(e), EXIT_INVALID)
    except MissingCredentials as e:
        return _fail(str(e), EXIT_FAILED)
    except anthropic.AuthenticationError:
        return _fail(REJECTED_CREDENTIALS, EXIT_FAILED)
    except anthropic.APIError as e:
        return _fail(f"Anthropic API error: {e}", EXIT_FAILED)
    _report(engine, result, out_dir)
    return _exit_code(result)


def cmd_stats(engine: Engine, args: argparse.Namespace) -> int:
    jobs = Telemetry(engine.home / "telemetry").load()
    if args.last:
        jobs = jobs[-args.last :]
    stats: dict[str, Any] = summarize(jobs)
    print(json.dumps(stats, indent=2) if args.json else format_summary(stats))
    return 0
