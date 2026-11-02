"""End to end through the worker subprocess: run, cache, limits, failures."""

from __future__ import annotations

from pathlib import Path

import pytest

from atelier.engine.engine import Engine, format_plan, format_result
from atelier.engine.runner import RunLimits, StepFailed
from conftest import two_step_spec


def test_two_op_pipeline_runs_and_caches(engine: Engine, hello: str, world: str) -> None:
    first = engine.run(two_step_spec(hello, world))
    out = first.outputs["out"]
    assert engine.store.path(out.hash).read_bytes() == b"HELLO\n-world\n"
    assert first.reused == 0

    second = engine.run(two_step_spec(hello, world))
    assert second.reused == 2
    assert second.outputs["out"].hash == out.hash
    assert format_result(second).splitlines()[-1].startswith("cache: 2/2 steps reused")


def test_changing_one_step_reuses_upstream(engine: Engine, hello: str, world: str) -> None:
    engine.run(two_step_spec(hello, world))
    changed = engine.run(two_step_spec(hello, world, sep="+"))
    assert [s.cached for s in changed.steps] == [True, False]


def test_equivalent_forms_share_cache(engine: Engine, hello: str) -> None:
    engine.run({"in": hello, "ops": ["debug.upper"]})
    full = {
        "inputs": {"clip": hello},
        "steps": [{"id": "shout", "op": "debug.upper", "in": "clip", "args": {}}],
        "outputs": ["shout"],
    }
    assert engine.run(full).reused == 1


def test_dry_run_plan(engine: Engine, hello: str, world: str) -> None:
    assert format_plan(engine.plan(two_step_spec(hello, world))) == (
        "valid · 2 steps · 0 cached · 2 to run"
    )
    engine.run(two_step_spec(hello, world))
    assert engine.plan(two_step_spec(hello, world)).cached == 2


def test_missing_object_is_a_cache_miss(engine: Engine, hello: str, world: str) -> None:
    result = engine.run(two_step_spec(hello, world))
    path = engine.store.path(result.outputs["out"].hash)
    path.chmod(0o644)
    path.unlink()
    assert [s.cached for s in engine.run(two_step_spec(hello, world)).steps] == [True, False]


def test_op_error_is_reported(engine: Engine, hello: str) -> None:
    with pytest.raises(StepFailed) as exc:
        engine.run({"in": hello, "ops": [["debug.fail", {"message": "font 'X' not bundled"}]]})
    assert str(exc.value) == "step 's1' (debug.fail) failed: font 'X' not bundled"


def test_wall_time_limit(tmp_path: Path) -> None:
    engine = Engine.open(tmp_path / "home", run_limits=RunLimits(wall_seconds=1))
    handle = engine.store.put_bytes(b"hello\n").handle
    with pytest.raises(StepFailed, match=r"timed out \(wall-time limit 1s\)"):
        engine.run({"in": handle, "ops": [["debug.sleep", {"seconds": 5}]]})


def test_scratch_dirs_are_cleaned(engine: Engine, hello: str) -> None:
    engine.run({"in": hello, "ops": ["debug.upper"]})
    with pytest.raises(StepFailed):
        engine.run({"in": hello, "ops": [["debug.fail", {"message": "x"}]]})
    assert list(engine.store.tmp.iterdir()) == []


def test_worker_env_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from atelier.engine.runner import _worker_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    env = _worker_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["TZ"] == "UTC"
