"""The layers depend one way: the application layer uses the engine and the agent, never the
other way round, and nothing below it needs Django (ADR-0007)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "atelier"


def test_the_engine_the_agent_and_the_cli_import_without_django() -> None:
    code = (
        "import sys, atelier.cli, atelier.engine.engine, atelier.agent.loop, "
        "atelier.evals.commands, atelier.docs.doc_ops\n"
        "sys.exit(1 if any(m == 'django' or m.startswith('django.') for m in sys.modules) else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr or "django was imported by a lower layer"


def test_no_module_outside_the_application_layer_imports_it() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if "api" in path.relative_to(SRC).parts[:1]:
            continue
        text = path.read_text(encoding="utf-8")
        if "atelier.api" in text or "from atelier import api" in text:
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == []
