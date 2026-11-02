"""Warm standby workers: hide the import cost without weakening isolation.

Starting a worker costs ~350 ms, ~240 ms of which is importing NumPy, Pillow, pydantic and the
ops. The design (docs/10) requires a fresh, resource-limited process for every step, so
long-lived workers that are reused are out: state would leak between steps and CPU/memory
limits would accumulate. Instead a few *standby* workers are started ahead of time. Each has
already finished its imports and is blocked waiting for one job path on stdin. It runs that one
job, exits, and a replacement is started at once.

What is unchanged from a cold start:
* one process per step, never reused, so nothing leaks between steps or between jobs;
* the stripped environment: a standby is only used if it was started with exactly the
  environment the job needs, otherwise it is discarded;
* resource limits, applied by the worker when the job arrives; wall-time by the parent, which
  kills the whole process group (an op's own children die with it).

Standbys pay off when work is sequential (an agent's pipeline, one step after another): the next
worker warms up while the current step runs. When many jobs run at once (a batch) the machine is
already busy and a standby only adds work, so the pool tops itself up only when the requester is
the sole job in flight.

Standbys start in a private empty directory with `-P`, so nothing in the caller's working
directory can shadow an import. If the parent goes away its pipe closes and every standby exits.
`ATELIER_WARM_WORKERS` sets how many are kept (default 2, 0 = always cold).
"""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque

ENV_SPARES = "ATELIER_WARM_WORKERS"
DEFAULT_SPARES = 2
_SPAWN_ARGS = ("-P", "-m", "atelier.engine.worker", "--standby")

Proc = subprocess.Popen[bytes]


def configured_spares() -> int:
    try:
        return max(0, int(os.environ.get(ENV_SPARES, DEFAULT_SPARES)))
    except ValueError:
        return DEFAULT_SPARES


class WarmPool:
    def __init__(self, spares: int) -> None:
        self.spares = spares
        self._lock = threading.Lock()
        self._ready: deque[tuple[Proc, dict[str, str]]] = deque()
        self._cwd: str | None = None
        self._active = 0  # workers handed out and not yet released

    # -- starting and handing out workers ---------------------------------------------------

    def _start(self, env: dict[str, str]) -> Proc:
        if self._cwd is None:
            self._cwd = tempfile.mkdtemp(prefix="atelier-standby-")
        return subprocess.Popen(
            [sys.executable, *_SPAWN_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            start_new_session=os.name == "posix",  # its own process group, killed as one
        )

    def acquire(self, env: dict[str, str]) -> Proc:
        """A worker for exactly one job: a warm standby if one matches, else a fresh process.

        Every acquire must be paired with a `release` once the job is over.
        """
        with self._lock:
            self._active += 1
            try:
                proc = None
                while self._ready:
                    candidate, candidate_env = self._ready.popleft()
                    if candidate_env == env and candidate.poll() is None:
                        proc = candidate
                        break
                    self.kill(candidate)  # stale environment, or it died while waiting
                if proc is None:
                    proc = self._start(env)
                if self._active == 1:  # sequential work: warm the next one while this one runs
                    while len(self._ready) < self.spares:
                        self._ready.append((self._start(env), env))
            except BaseException:
                self._active -= 1  # no worker was handed out, so there is nothing to release
                raise
            return proc

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    # -- ending them --------------------------------------------------------------------------

    @staticmethod
    def kill(proc: Proc) -> None:
        """Kill the worker and anything it started, and reap it."""
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        with contextlib.suppress(subprocess.TimeoutExpired, ValueError, OSError):
            proc.communicate(timeout=5)

    def shutdown(self) -> None:
        with self._lock:
            while self._ready:
                proc, _ = self._ready.popleft()
                self.kill(proc)
            if self._cwd is not None:
                shutil.rmtree(self._cwd, ignore_errors=True)
                self._cwd = None


_pool: WarmPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> WarmPool:
    """The process-wide pool. Standbys are generic (limits arrive with the job), so every
    Engine in the process shares them."""
    global _pool
    with _pool_lock:
        if _pool is None or _pool.spares != configured_spares():
            if _pool is not None:
                _pool.shutdown()
            _pool = WarmPool(configured_spares())
            atexit.register(_pool.shutdown)
        return _pool


def shutdown() -> None:
    """Stop every standby now (servers call this on exit; tests to leave no processes)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown()
            _pool = None
