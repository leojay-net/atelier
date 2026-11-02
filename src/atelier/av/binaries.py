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
    )
