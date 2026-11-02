"""`atelier models`: list, fetch and verify the pinned model weights the ML plugins need.

atelier models                  list models and whether they are installed
atelier models fetch NAME...    download (once) and verify; `--all` for every model
atelier models verify [NAME...] check installed files against their pinned hashes
"""

from __future__ import annotations

import argparse

from atelier import models
from atelier.engine.engine import Engine

EXIT_FAILED = 1


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("models", help="pinned model weights for the ML plugins")
    msub = p.add_subparsers(dest="models_command")
    p.set_defaults(fn=cmd_list)
    f = msub.add_parser("fetch", help="download and verify models (needs network, once)")
    f.add_argument("names", nargs="*")
    f.add_argument("--all", action="store_true")
    f.set_defaults(fn=cmd_fetch)
    v = msub.add_parser("verify", help="check installed models against their pinned hashes")
    v.add_argument("names", nargs="*")
    v.set_defaults(fn=cmd_verify)


def cmd_list(engine: Engine, args: argparse.Namespace) -> int:
    print(f"models directory: {models.models_dir()}")
    for name in models.names():
        entry = models.manifest()["models"][name]
        size = sum(f["size"] for f in entry["files"]) / 1e6
        state = "installed" if models.is_installed(name) else "not installed"
        print(
            f"{name:<14} {state:<14} {size:>7.1f} MB  {entry['license']:<22} {entry['description']}"
        )
    return 0


def _chosen(args: argparse.Namespace) -> list[str]:
    return list(models.names()) if getattr(args, "all", False) or not args.names else args.names


def cmd_fetch(engine: Engine, args: argparse.Namespace) -> int:
    if not args.names and not args.all:
        print("give model names, or --all")
        return EXIT_FAILED
    try:
        for name in _chosen(args):
            models.fetch(name, progress=print)
            print(f"{name}: ok ({models.model_id(name)})")
    except models.ModelError as e:
        print(f"error: {e}")
        return EXIT_FAILED
    return 0


def cmd_verify(engine: Engine, args: argparse.Namespace) -> int:
    bad = 0
    try:
        for name in _chosen(args):
            problems = models.verify(name)
            print(f"{name}: " + ("ok" if not problems else "; ".join(problems)))
            bad += bool(problems)
    except models.ModelError as e:
        print(f"error: {e}")
        return EXIT_FAILED
    return EXIT_FAILED if bad else 0
