"""Content-addressed artifact storage (ADR-0005), local-disk backend.

Objects live at objects/<aa>/<sha256>, read-only, with a JSON sidecar holding the media
type and extension. The same bytes are stored once no matter how often they are added.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from atelier.canonical import sha256_file
from atelier.engine.sniff import sniff
from atelier.media import (
    MIN_PREFIX_LEN,
    AmbiguousHandle,
    AssetInfo,
    AssetNotFound,
    MediaType,
    parse_handle,
)

_CHUNK = 1 << 20


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.tmp = root / "tmp"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)

    def _object(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest

    def _sidecar(self, digest: str) -> Path:
        return self.objects / digest[:2] / f"{digest}.json"

    def path(self, digest: str) -> Path:
        return self._object(digest)

    def has(self, digest: str) -> bool:
        return self._object(digest).is_file()

    def work_dir(self, prefix: str) -> Path:
        """A private scratch directory on the store's filesystem (so outputs can be moved in)."""
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.tmp))

    def put_file(
        self,
        src: Path,
        *,
        media: MediaType | None = None,
        ext: str | None = None,
        move: bool = False,
    ) -> AssetInfo:
        """Add a file. With move=True the source (which must be under the store) is consumed."""
        if move:
            digest = sha256_file(src)
            staged = src
        else:
            digest, staged = self._copy_to_tmp(src)
        dest = self._object(digest)
        if dest.is_file():
            staged.unlink()
            return self.info(digest)
        if media is None:
            media, sniffed_ext = sniff(staged)
            ext = sniffed_ext if ext is None else ext
            if not ext:
                ext = src.suffix.lower()
        info = AssetInfo(digest, media, staged.stat().st_size, ext or "")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._sidecar(digest), json.dumps(info.to_json(), sort_keys=True))
        staged.chmod(0o444)
        os.replace(staged, dest)
        return info

    def put_bytes(
        self, data: bytes, *, media: MediaType | None = None, ext: str | None = None
    ) -> AssetInfo:
        fd, name = tempfile.mkstemp(dir=self.tmp)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return self.put_file(Path(name), media=media, ext=ext, move=True)

    def info(self, digest: str) -> AssetInfo:
        if not self.has(digest):
            raise AssetNotFound(f"asset:{digest}")
        try:
            return AssetInfo.from_json(json.loads(self._sidecar(digest).read_text()))
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            path = self._object(digest)
            media, ext = sniff(path)
            return AssetInfo(digest, media, path.stat().st_size, ext)

    def resolve(self, handle: str) -> AssetInfo:
        """Resolve a full or abbreviated (>= 8 hex digits) handle."""
        digest = parse_handle(handle)
        if len(digest) == 64:
            return self.info(digest)
        assert len(digest) >= MIN_PREFIX_LEN
        bucket = self.objects / digest[:2]
        matches = (
            sorted(p.name for p in bucket.glob(f"{digest}*") if not p.name.endswith(".json"))
            if bucket.is_dir()
            else []
        )
        if not matches:
            raise AssetNotFound(handle)
        if len(matches) > 1:
            raise AmbiguousHandle(handle, len(matches))
        return self.info(matches[0])

    def _copy_to_tmp(self, src: Path) -> tuple[str, Path]:
        h = hashlib.sha256()
        fd, name = tempfile.mkstemp(dir=self.tmp)
        with os.fdopen(fd, "wb") as out, src.open("rb") as f:
            while chunk := f.read(_CHUNK):
                h.update(chunk)
                out.write(chunk)
        return h.hexdigest(), Path(name)


def _atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(name, path)
