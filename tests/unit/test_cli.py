from __future__ import annotations

import json
from pathlib import Path

import pytest

from atelier.cli import main
from conftest import FIXTURES


def _spec(tmp_path: Path, spec: object) -> str:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    return str(path)


def test_run_writes_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _spec(tmp_path, {"in": "asset:00000000", "ops": ["debug.upper"]})
    args = [
        "--home",
        str(tmp_path / "h"),
        "run",
        spec,
        "--in",
        f"in={FIXTURES / 'hello.txt'}",
        "--out",
        str(tmp_path / "out"),
    ]
    assert main(args) == 0
    assert (tmp_path / "out" / "s1.txt").read_bytes() == b"HELLO\n"
    assert "cache: 0/1 steps reused" in capsys.readouterr().out

    assert main(args) == 0
    assert "cache: 1/1 steps reused" in capsys.readouterr().out


def test_invalid_spec_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _spec(tmp_path, {"in": "/etc/passwd", "ops": ["debug.upper"]})
    assert main(["--home", str(tmp_path / "h"), "run", spec]) == 2
    assert "is not an asset handle" in capsys.readouterr().err


def test_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = _spec(tmp_path, {"in": "x", "ops": ["debug.upper"]})
    code = main(
        [
            "--home",
            str(tmp_path / "h"),
            "run",
            spec,
            "--dry-run",
            "--in",
            f"in={FIXTURES / 'hello.txt'}",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == "valid · 1 step · 0 cached · 1 to run"


def test_inspect_and_ops(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = ["--home", str(tmp_path / "h")]
    assert main([*home, "inspect", str(FIXTURES / "hello.txt")]) == 0
    assert capsys.readouterr().out.startswith("text · 1 line · 6 chars · 6B · asset:5891b5b522d5")
    assert main([*home, "ops", "debug.join"]) == 0
    assert "params:" in capsys.readouterr().out
    assert main([*home, "ops", "nope"]) == 1
