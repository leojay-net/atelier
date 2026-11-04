"""Command-line interface. No model involved: every command here costs zero tokens.
(`ask`, `stats` and `eval`, which involve the model, live in `atelier.agent.commands`
and `atelier.evals.commands`.)

atelier put FILE...                          add files to the store, print handles
atelier inspect FILE|HANDLE... [--full]      compact metadata (parsed in the sandbox)
atelier run SPEC [--in NAME=FILE]...         validate, run, write outputs
atelier batch SPEC --each NAME=GLOB [...]    run one spec over many files
atelier preview KIND FILE|HANDLE... [...]    thumb | crop | compare | sheet
atelier ops [NAME...] [--all]                catalogue, or docs for ops/presets/recipes
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from atelier import modelcmd
from atelier.agent import commands as agent_commands
from atelier.checks import summarize
from atelier.engine.engine import Engine, bind_input, format_plan, format_result
from atelier.engine.runner import StepFailed
from atelier.evals import commands as eval_commands
from atelier.media import AssetInfo, HandleError
from atelier.ops.catalogue import catalogue, describe_name, full_catalogue
from atelier.spec.errors import SpecInvalid

EXIT_INVALID = 2
EXIT_FAILED = 1
EXIT_CHECKS = 3


def _home(arg: str | None) -> Path:
    return Path(arg or os.environ.get("ATELIER_HOME") or ".atelier").resolve()


def _fail(message: str, code: int = EXIT_FAILED) -> int:
    print(message, file=sys.stderr)
    return code


def cmd_put(engine: Engine, args: argparse.Namespace) -> int:
    for name in args.files:
        info = engine.store.put_file(Path(name))
        print(f"{info.handle}  {info.media.value}  {name}")
    return 0


def _asset(engine: Engine, target: str) -> AssetInfo:
    """A handle, or a file path (which is added to the store)."""
    if target.startswith("asset:"):
        try:
            return engine.store.resolve(target)
        except HandleError as e:
            raise ValueError(f"{target}: {type(e).__name__}: {e}") from None
    if Path(target).is_file():
        return engine.store.put_file(Path(target))
    raise ValueError(f"{target}: no such file or asset handle")


def cmd_inspect(engine: Engine, args: argparse.Namespace) -> int:
    code = 0
    for target in args.targets:
        try:
            print(engine.inspect(_asset(engine, target), full=args.full))
        except ValueError as e:
            code = _fail(str(e))
    return code


def _bind_inputs(engine: Engine, spec: Any, bindings: list[str]) -> Any:
    """`--in name=path` stores the file and substitutes its handle into the spec."""
    for binding in bindings:
        name, sep, path = binding.partition("=")
        if not sep:
            raise ValueError(f"--in expects NAME=FILE, got {binding!r}")
        spec = bind_input(spec, name, engine.store.put_file(Path(path)).handle)
    return spec


def cmd_run(engine: Engine, args: argparse.Namespace) -> int:
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        spec = _bind_inputs(engine, spec, args.inputs)
    except (OSError, ValueError) as e:
        return _fail(f"cannot read spec: {e}", EXIT_INVALID)
    try:
        if args.dry_run:
            print(format_plan(engine.plan(spec)))
            return 0
        result = engine.run(spec)
    except SpecInvalid as e:
        return _fail(str(e), EXIT_INVALID)
    except StepFailed as e:
        return _fail(str(e))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for out_id, rep in result.outputs.items():
        dest = out_dir / f"{out_id}{rep.ext}"
        shutil.copyfile(engine.store.path(rep.hash), dest)
    if args.json:
        print(
            json.dumps(
                {
                    "spec": result.spec.to_json(),
                    "outputs": {
                        k: {
                            "asset": v.handle,
                            "file": str(out_dir / f"{k}{v.ext}"),
                            "summary": v.summary,
                        }
                        for k, v in result.outputs.items()
                    },
                    "reused": result.reused,
                    "seconds": round(result.seconds, 3),
                },
                indent=2,
            )
        )
    else:
        print(format_result(result))
    return 0 if result.passed else EXIT_CHECKS


def cmd_ops(engine: Engine, args: argparse.Namespace) -> int:
    if not args.names:
        print(
            catalogue(engine.registry, include_hidden=True)
            if args.all
            else full_catalogue(engine.registry)
        )
        return 0
    code = 0
    for name in args.names:
        text = describe_name(engine.registry, name)
        if text is None:
            code = _fail(f"unknown op, preset or recipe {name!r}")
            continue
        print(text)
    return code


def cmd_batch(engine: Engine, args: argparse.Namespace) -> int:
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        spec = _bind_inputs(engine, spec, args.inputs)
    except (OSError, ValueError) as e:
        return _fail(f"cannot read spec: {e}", EXIT_INVALID)
    name, sep, pattern = args.each.partition("=")
    if not sep:
        return _fail(f"--each expects NAME=GLOB, got {args.each!r}", EXIT_INVALID)
    files = sorted(p for p in Path().glob(pattern) if p.is_file())
    if not files:
        return _fail(f"--each {pattern!r} matched no files", EXIT_INVALID)
    handles = [engine.store.put_file(f).handle for f in files]
    started = time.perf_counter()
    items = engine.run_batch(spec, name, handles, workers=args.workers)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = check_failures = 0
    for src, item in zip(files, items, strict=True):
        if item.result is None:
            errors += 1
            print(f"error · {src}: {item.error}")
            continue
        for out_id, rep in item.result.outputs.items():
            suffix = "" if len(item.result.outputs) == 1 else f".{out_id}"
            shutil.copyfile(engine.store.path(rep.hash), out_dir / f"{src.stem}{suffix}{rep.ext}")
        if not item.result.passed:
            check_failures += 1
            lines = [summarize(c) for c in item.result.checks.values()]
            print(f"fail · {src}: {' | '.join(lines)}")
    seconds = time.perf_counter() - started
    print(
        f"{len(files) - errors}/{len(files)} ran · {check_failures} failed checks · "
        f"{errors} errors · {seconds:.1f}s"
    )
    return EXIT_FAILED if errors else 0


def cmd_preview(engine: Engine, args: argparse.Namespace) -> int:
    extra: dict[str, Any] = {}
    if args.region:
        extra["region"] = [float(v) for v in args.region.split(",")]
    try:
        handles = [_asset(engine, t).handle for t in args.targets]
        result = engine.preview(args.kind, handles, extra)
    except ValueError as e:
        return _fail(str(e), EXIT_INVALID)
    except SpecInvalid as e:
        return _fail(str(e), EXIT_INVALID)
    except StepFailed as e:
        return _fail(str(e))
    rep = result.outputs["preview"]
    dest = Path(args.out or f"preview-{args.kind}{rep.ext}")
    shutil.copyfile(engine.store.path(rep.hash), dest)
    print(f"{rep.summary} → {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atelier", description="Deterministic media engine")
    parser.add_argument("--home", help="data dir (default: $ATELIER_HOME or ./.atelier)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("put", help="add files to the store")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_put)

    p = sub.add_parser("inspect", help="compact metadata for files or assets")
    p.add_argument("targets", nargs="+")
    p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("run", help="validate and run a spec")
    p.add_argument("spec")
    p.add_argument("--in", dest="inputs", action="append", default=[], metavar="NAME=FILE")
    p.add_argument("--out", default="out", help="directory for outputs (default: ./out)")
    p.add_argument("--dry-run", action="store_true", help="validate and plan only")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("batch", help="run one spec over many files")
    p.add_argument("spec")
    p.add_argument("--each", required=True, metavar="NAME=GLOB", help="input bound per file")
    p.add_argument("--in", dest="inputs", action="append", default=[], metavar="NAME=FILE")
    p.add_argument("--out", default="out", help="directory for outputs (default: ./out)")
    p.add_argument("--workers", type=int, default=None)
    p.set_defaults(fn=cmd_batch)

    p = sub.add_parser("preview", help="small preview image (thumb/crop/compare/sheet)")
    p.add_argument("kind", choices=["thumb", "crop", "compare", "sheet"])
    p.add_argument("targets", nargs="+")
    p.add_argument("--region", help="crop region x,y,w,h as fractions")
    p.add_argument("--out", help="output file")
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("ops", help="catalogue, or describe ops/presets/recipes")
    p.add_argument("names", nargs="*")
    p.add_argument("--all", action="store_true", help="include hidden debug ops")
    p.set_defaults(fn=cmd_ops)

    agent_commands.register(sub)
    eval_commands.register(sub)
    modelcmd.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = Engine.open(_home(args.home))
    code: int = args.fn(engine, args)
    return code


if __name__ == "__main__":
    sys.exit(main())
