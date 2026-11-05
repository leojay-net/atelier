"""Locating ffmpeg/ffprobe and running them safely.

The binary is `$ATELIER_FFMPEG` / `$ATELIER_FFPROBE` if set, else the one on PATH. Its identity
(a hash of the file) is part of every ffmpeg-backed step's cache key via `resources.py`, so a
different build never returns another build's results.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

# Never let ffmpeg open anything but local files or pipes: no http, tcp, hls playlists, etc.
PROTOCOLS = "file,pipe"

# Default stack size for the tools' threads. glibc sizes each new thread's stack from
# RLIMIT_STACK (8 MB by default) and the step's RLIMIT_AS counts it: ffmpeg 7 gives every
# input its own threads, so a long captioned video (60+ inputs, 275 threads) reserved 3.85 GB
# of a 4 GB cap with 1.2 GB in use and failed ("no packets" / "Cannot allocate memory").
# At 2 MB the same render peaks at 2.4 GB (live 2026-11-05).
THREAD_STACK = 2 << 20


def _small_stacks() -> None:  # runs in the child between fork and exec
    try:
        import resource

        _soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
        limit = THREAD_STACK if hard == resource.RLIM_INFINITY else min(THREAD_STACK, hard)
        resource.setrlimit(resource.RLIMIT_STACK, (limit, hard))
    except (ImportError, ValueError, OSError):
        pass


class ToolNotFound(RuntimeError):
    pass


def find(name: str) -> str:
    override = os.environ.get(f"ATELIER_{name.upper()}")
    path = override or shutil.which(name)
    if not path or not Path(path).is_file():
        raise ToolNotFound(f"{name} not found; install ffmpeg or set ATELIER_{name.upper()}")
    return path


def run(
    argv: Sequence[str], *, timeout: float, cwd: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run a command (argv list, no shell) and capture its output.

    `OMP_NUM_THREADS=1` pins every library ffmpeg links that parallelizes with OpenMP (found via
    libvidstab: its motion search gave different transforms, and so different output bytes, from
    run to run on identical input with every ffmpeg-level thread count already at 1). ffmpeg's own
    filter and encoder threading (`-filter_threads`, `-threads`) is unrelated and set by the
    caller; this only reaches libraries OpenMP would otherwise be free to parallelize on its own.
    """
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    return subprocess.run(
        list(argv),
        capture_output=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        env=env,
        preexec_fn=_small_stacks if os.name == "posix" else None,
    )
