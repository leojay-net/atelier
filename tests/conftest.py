from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atelier.engine.engine import Engine
from atelier.engine.runner import RunLimits

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="rewrite golden hashes (only allowed for cases whose op versions changed)",
    )


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine.open(tmp_path / "home", run_limits=RunLimits(wall_seconds=30))


@pytest.fixture
def hello(engine: Engine) -> str:
    return engine.store.put_file(FIXTURES / "hello.txt").handle


@pytest.fixture
def world(engine: Engine) -> str:
    return engine.store.put_file(FIXTURES / "world.txt").handle


@pytest.fixture
def photo(engine: Engine) -> str:
    return engine.store.put_file(FIXTURES / "photo.png").handle


@pytest.fixture
def logo(engine: Engine) -> str:
    return engine.store.put_file(FIXTURES / "logo.png").handle


def two_step_spec(a: str, b: str, sep: str = "-") -> dict[str, Any]:
    return {
        "version": 1,
        "inputs": {"a": a, "b": b},
        "steps": [
            {"id": "up", "op": "debug.upper", "in": "a"},
            {"id": "out", "op": "debug.join", "in": ["up", "b"], "args": {"sep": sep}},
        ],
        "outputs": ["out"],
    }
