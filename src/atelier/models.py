"""Pinned model weights for the ML plugins (docs/05 §5.3, docs/08 Phase 6).

Weights are large, come from third parties and decide what an op outputs, so they are treated like
a binary: listed in a manifest with the URL, size and SHA-256 of every file, fetched ahead of time
(`atelier models fetch NAME`, never during a job: workers have no business on the network), and
verified against the manifest. The manifest's hashes are part of the cache key of every op that
uses the model, so changing a model can never return an older model's results.
"""

from __future__ import annotations

import functools
import json
import os
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from atelier.canonical import sha256_file, sha256_hex
from atelier.paths import ASSETS

MANIFEST = ASSETS / "models" / "models.json"
_CHUNK = 1 << 20


class ModelError(Exception):
    """A model problem the user can fix; the message says how."""


@functools.cache
def manifest() -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ModelError(f"model manifest missing at {MANIFEST}") from None
    return data


def names() -> tuple[str, ...]:
    return tuple(sorted(manifest()["models"]))


def _entry(name: str) -> dict[str, Any]:
    try:
        entry: dict[str, Any] = manifest()["models"][name]
    except KeyError:
        raise ModelError(
            f"no model named {name!r}; the manifest has {', '.join(names())}"
        ) from None
    return entry


def models_dir() -> Path:
    return Path(os.environ.get("ATELIER_MODELS") or Path.home() / ".atelier" / "models")


def model_dir(name: str) -> Path:
    _entry(name)
    return models_dir() / name


def is_installed(name: str) -> bool:
    """Every file is present at its listed size (a full hash check is `verify`)."""
    root = model_dir(name)
    return all(
        (root / f["path"]).is_file() and (root / f["path"]).stat().st_size == f["size"]
        for f in _entry(name)["files"]
    )


def require(name: str) -> Path:
    """The model's directory, or a ModelError telling the user how to get it."""
    if not is_installed(name):
        raise ModelError(
            f"the model {name!r} is not installed: run `atelier models fetch {name}` "
            f"(it is fetched once, then kept in {models_dir()})"
        )
    return model_dir(name)


def file_path(name: str, filename: str) -> Path:
    return require(name) / filename


def verify(name: str) -> list[str]:
    """Problems with an installed model's files (empty when every hash matches)."""
    root, problems = model_dir(name), []
    for f in _entry(name)["files"]:
        path = root / f["path"]
        if not path.is_file():
            problems.append(f"{f['path']} is missing")
        elif sha256_file(path) != f["sha256"]:
            problems.append(f"{f['path']} does not match its pinned hash")
    return problems


@functools.cache
def model_id(name: str) -> str:
    """The identity of a model for cache keys: the pinned hashes, once the files are present."""
    require(name)
    pinned = "".join(f["sha256"] for f in _entry(name)["files"])
    return f"model:{name}:{sha256_hex(pinned.encode())[:16]}"


def fetch(name: str, progress: Callable[[str], None] | None = None) -> Path:
    """Download the model's files and verify them against the manifest. Idempotent."""
    entry = _entry(name)
    root = model_dir(name)
    root.mkdir(parents=True, exist_ok=True)
    for f in entry["files"]:
        dest = root / f["path"]
        if dest.is_file() and dest.stat().st_size == f["size"] and sha256_file(dest) == f["sha256"]:
            continue
        if not str(f["url"]).startswith("https://"):
            raise ModelError(f"{f['path']}: only https downloads are allowed")
        if progress:
            progress(f"fetching {f['path']} ({f['size'] / 1e6:.1f} MB)")
        fd, tmp_name = tempfile.mkstemp(dir=root, prefix=".fetch-")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with urllib.request.urlopen(f["url"], timeout=60) as response, tmp.open("wb") as out:
                while chunk := response.read(_CHUNK):
                    out.write(chunk)
            if tmp.stat().st_size != f["size"] or sha256_file(tmp) != f["sha256"]:
                raise ModelError(
                    f"{f['path']} downloaded from {f['url']} does not match the pinned "
                    "size and hash; it was discarded"
                )
            os.replace(tmp, dest)
        except OSError as e:
            raise ModelError(f"could not download {f['path']}: {e}") from None
        finally:
            tmp.unlink(missing_ok=True)
    model_id.cache_clear()
    return root
