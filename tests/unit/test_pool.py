"""Warm standby workers: faster, but never less isolated (src/atelier/engine/pool.py)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from atelier.engine import pool as pool_module
from atelier.engine.engine import Engine
from atelier.engine.pool import ENV_SPARES, WarmPool, configured_spares
from atelier.engine.runner import _worker_env

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")


@pytest.fixture
def pool() -> Any:
    p = WarmPool(spares=1)
    yield p
    p.shutdown()


def wait_exit(proc: subprocess.Popen[bytes], timeout: float = 30) -> int:
    return proc.wait(timeout=timeout)


def test_a_worker_runs_one_job_and_exits(pool: WarmPool) -> None:
    """Workers are single-use: nothing carries over from one step to the next."""
    env = _worker_env()
    first, second = pool.acquire(env), pool.acquire(env)
    assert first.pid != second.pid
    for proc in (first, second):
        proc.communicate(input=b"\n", timeout=30)  # an empty job path: warm up, then exit cleanly
        assert proc.returncode == 0  # and it is gone, never handed out again
    assert pool.acquire(env).pid not in (first.pid, second.pid)


def test_a_replacement_standby_is_started_after_each_use(pool: WarmPool) -> None:
    env = _worker_env()
    pool.acquire(env)
    pool.release()  # the first job is over: the next one is sequential
    assert len(pool._ready) == 1
    spare_pid = pool._ready[0][0].pid
    handed_out = pool.acquire(env)
    assert handed_out.pid == spare_pid  # the warm one is used...
    assert len(pool._ready) == 1 and pool._ready[0][0].pid != spare_pid  # ...and replaced


def test_standbys_are_only_warmed_for_sequential_work(pool: WarmPool) -> None:
    """When jobs overlap (a batch) the machine is busy and a standby only adds load."""
    pool = WarmPool(spares=2)
    env = _worker_env()
    try:
        first = pool.acquire(env)  # sole job in flight: warm two standbys
        assert len(pool._ready) == 2
        second = pool.acquire(env)  # a second job overlaps: uses a standby, adds none
        assert len(pool._ready) == 1
        third = pool.acquire(env)
        assert len(pool._ready) == 0
        for proc in (first, second, third):
            proc.communicate(input=b"\n", timeout=30)
            pool.release()
        pool.acquire(env)  # sequential again: warm up once more
        assert len(pool._ready) == 2
    finally:
        pool.shutdown()


def test_a_failed_start_does_not_leave_the_pool_thinking_a_job_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise the in-flight count drifts up and standbys are never warmed again."""
    p = WarmPool(spares=1)
    try:
        monkeypatch.setattr(p, "_start", lambda env: (_ for _ in ()).throw(OSError("no exec")))
        with pytest.raises(OSError, match="no exec"):
            p.acquire(_worker_env())
        assert p._active == 0
        monkeypatch.undo()
        proc = p.acquire(_worker_env())
        assert p._active == 1 and len(p._ready) == 1  # back to normal: sequential, so warmed
        proc.communicate(input=b"\n", timeout=30)
        p.release()
    finally:
        p.shutdown()


def test_zero_spares_means_always_cold() -> None:
    p = WarmPool(spares=0)
    try:
        proc = p.acquire(_worker_env())
        assert len(p._ready) == 0
        proc.communicate(input=b"\n", timeout=30)
        assert proc.returncode == 0
    finally:
        p.shutdown()


def test_a_standby_with_the_wrong_environment_is_discarded(pool: WarmPool) -> None:
    """A worker is only reused if it was started with exactly the environment the job needs."""
    env = _worker_env()
    pool.acquire(env)
    stale = pool._ready[0][0]
    other = {**env, "TZ": "Pacific/Auckland"}
    fresh = pool.acquire(other)
    assert stale.poll() is not None or stale.wait(timeout=10) is not None  # it was killed
    assert fresh.pid != stale.pid
    assert all(e == other for _, e in pool._ready)


def test_a_standby_that_died_while_waiting_is_replaced(pool: WarmPool) -> None:
    env = _worker_env()
    pool.acquire(env)
    dead = pool._ready[0][0]
    WarmPool.kill(dead)
    live = pool.acquire(env)
    assert live.pid != dead.pid and live.poll() is None


def test_a_standby_exits_by_itself_when_its_parent_goes_away(pool: WarmPool) -> None:
    """Closing the pipe (as a dying parent does) makes it exit: no orphans left behind."""
    proc = pool.acquire(_worker_env())
    assert proc.stdin is not None
    proc.stdin.close()
    assert wait_exit(proc) == 0


def test_shutdown_kills_every_standby_and_removes_its_directory(pool: WarmPool) -> None:
    pool.acquire(_worker_env())
    spare = pool._ready[0][0]
    cwd = Path(pool._cwd or "")
    assert cwd.is_dir()
    pool.shutdown()
    assert spare.poll() is not None and not cwd.exists() and not pool._ready


def is_dead(pid: int) -> bool:
    """Gone, or a zombie. In a container PID 1 is not an init process, so a killed orphan is
    never reaped and `os.kill(pid, 0)` keeps succeeding for it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:  # no /proc (macOS): the process exists and os.kill just proved it
        return False
    return state == "Z"


@posix_only
def test_kill_takes_the_workers_children_with_it() -> None:
    """A timeout must not leave an op's own subprocess (ffmpeg, soon) running."""
    proc = subprocess.Popen(
        ["sh", "-c", "sleep 60 & echo $!; wait"],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None
    child = int(proc.stdout.readline())
    assert not is_dead(child)
    WarmPool.kill(proc)
    for _ in range(50):
        if is_dead(child):
            return
        time.sleep(0.1)
    pytest.fail("the worker's child survived the kill")


def test_configured_spares(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SPARES, raising=False)
    assert configured_spares() == 2
    monkeypatch.setenv(ENV_SPARES, "0")
    assert configured_spares() == 0
    monkeypatch.setenv(ENV_SPARES, "5")
    assert configured_spares() == 5
    monkeypatch.setenv(ENV_SPARES, "many")
    assert configured_spares() == 2  # garbage falls back to the default
    monkeypatch.setenv(ENV_SPARES, "-3")
    assert configured_spares() == 0


def test_the_shared_pool_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SPARES, "1")
    assert pool_module.get_pool().spares == 1
    monkeypatch.setenv(ENV_SPARES, "0")
    assert pool_module.get_pool().spares == 0
    pool_module.shutdown()


# -- end to end: warm must not change results or safety -----------------------------------------


def run_pipeline(engine: Engine, photo: str) -> str:
    spec = {
        "in": photo,
        "ops": [
            ["image.resize", {"width": 320}],
            ["image.adjust", {"contrast": 1.1}],
            ["export", {"preset": "web_jpeg"}],
        ],
    }
    return engine.run(spec).outputs["s3"].hash


def test_warm_and_cold_workers_produce_identical_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import FIXTURES

    hashes = {}
    for spares in ("0", "2"):
        monkeypatch.setenv(ENV_SPARES, spares)
        engine = Engine.open(tmp_path / f"home{spares}")
        photo = engine.store.put_file(FIXTURES / "photo.png").handle
        hashes[spares] = run_pipeline(engine, photo)
    assert hashes["0"] == hashes["2"]


def test_the_callers_working_directory_cannot_shadow_an_import(
    engine: Engine, photo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray numpy.py in the directory the caller happens to be in must not be imported."""
    trap = tmp_path / "trap"
    trap.mkdir()
    (trap / "numpy.py").write_text("raise RuntimeError('shadowed numpy was imported')")
    (trap / "PIL.py").write_text("raise RuntimeError('shadowed PIL was imported')")
    pool_module.shutdown()  # start fresh standbys from inside the trap directory
    monkeypatch.chdir(trap)
    assert run_pipeline(engine, photo)


def test_a_timeout_still_kills_the_worker_and_reports_it(tmp_path: Path) -> None:
    from atelier.engine.runner import RunLimits, StepFailed

    slow = Engine.open(tmp_path / "slow", run_limits=RunLimits(wall_seconds=1))
    handle = slow.store.put_file(Path(__file__).parents[1] / "fixtures" / "hello.txt").handle
    with pytest.raises(StepFailed, match=r"timed out \(wall-time limit 1s\)"):
        slow.run({"in": handle, "ops": [["debug.sleep", {"seconds": 30}]]})
