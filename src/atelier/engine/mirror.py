"""A content-addressed store that keeps a durable copy elsewhere (docs/02, ADR-0005, ADR-0017).

Workers need real files (ffmpeg, LibreOffice and Pillow read paths), so the working copy stays on
local disk. `MirroredStore` is a `LocalStore` that also uploads every object it creates to an
object backend (S3, or any directory for tests), and fetches an object from the backend the
first time a node needs one it does not hold. Two nodes that share a backend therefore share
their assets and results with no shared filesystem.

Objects are immutable and named by their hash, so there is nothing to invalidate: a fetch is
verified against the name it was fetched under, and two nodes racing to fetch write the same bytes.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from atelier.canonical import sha256_file
from atelier.engine.store import LocalStore
from atelier.media import MIN_PREFIX_LEN, AmbiguousHandle, AssetInfo, AssetNotFound, parse_handle


class ObjectBackend(Protocol):
    def exists(self, key: str) -> bool: ...

    def upload(self, local: Path, key: str) -> None: ...

    def download(self, key: str, local: Path) -> None: ...

    def keys(self, prefix: str) -> list[str]: ...


class DirBackend:
    """A directory standing in for a remote bucket (tests, or a network mount)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def exists(self, key: str) -> bool:
        return (self.root / key).is_file()

    def upload(self, local: Path, key: str) -> None:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        os.close(fd)
        shutil.copyfile(local, name)
        os.replace(name, target)

    def download(self, key: str, local: Path) -> None:
        shutil.copyfile(self.root / key, local)

    def keys(self, prefix: str) -> list[str]:
        base = self.root / prefix
        parent = base.parent
        if not parent.is_dir():
            return []
        return sorted(
            str(p.relative_to(self.root))
            for p in parent.iterdir()
            if p.is_file() and str(p.relative_to(self.root)).startswith(prefix)
        )


class S3Backend:
    """Amazon S3 or any S3-compatible service (MinIO, R2). Credentials come from the usual
    AWS environment or instance role; `endpoint_url` points at a compatible service."""

    def __init__(self, bucket: str, *, endpoint_url: str | None = None, client: Any = None) -> None:
        if client is None:
            import boto3  # optional: pip install "atelier[s3]"

            client = boto3.client("s3", endpoint_url=endpoint_url)
        self.bucket, self.client = bucket, client

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def upload(self, local: Path, key: str) -> None:
        self.client.upload_file(str(local), self.bucket, key)

    def download(self, key: str, local: Path) -> None:
        self.client.download_file(self.bucket, key, str(local))

    def keys(self, prefix: str) -> list[str]:
        found: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            found += [o["Key"] for o in page.get("Contents", [])]
        return sorted(found)


class MirroredStore(LocalStore):
    def __init__(self, root: Path, backend: ObjectBackend, prefix: str = "") -> None:
        super().__init__(root)
        self.backend = backend
        self.prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""

    def _key(self, digest: str, sidecar: bool = False) -> str:
        return f"{self.prefix}objects/{digest[:2]}/{digest}{'.json' if sidecar else ''}"

    # -- writing: local first, then durable ------------------------------------------------

    def put_file(self, src: Path, **kwargs: Any) -> AssetInfo:
        info = super().put_file(src, **kwargs)
        self._push(info.hash)
        return info

    def _push(self, digest: str) -> None:
        if not self.backend.exists(self._key(digest)):
            self.backend.upload(self._sidecar(digest), self._key(digest, sidecar=True))
            self.backend.upload(
                self._object(digest), self._key(digest)
            )  # object last: it marks done

    # -- reading: local first, then fetch ---------------------------------------------------

    def _fetch(self, digest: str) -> bool:
        """Make the object local. True if it is (now) here."""
        if self._object(digest).is_file():
            return True
        if not self.backend.exists(self._key(digest)):
            return False
        dest = self._object(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.tmp, prefix="fetch-")
        os.close(fd)
        tmp = Path(name)
        try:
            self.backend.download(self._key(digest), tmp)
            if sha256_file(tmp) != digest:
                raise OSError(f"the copy of asset:{digest[:12]} in the backend is damaged")
            if self.backend.exists(self._key(digest, sidecar=True)):
                fd, side_name = tempfile.mkstemp(dir=self.tmp, prefix="side-")
                os.close(fd)  # a private name: several threads may fetch the same object
                self.backend.download(self._key(digest, sidecar=True), Path(side_name))
                os.replace(side_name, self._sidecar(digest))
            tmp.chmod(0o444)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return True

    def has(self, digest: str) -> bool:
        return self._object(digest).is_file() or self.backend.exists(self._key(digest))

    def path(self, digest: str) -> Path:
        self._fetch(digest)
        return self._object(digest)

    def info(self, digest: str) -> AssetInfo:
        if not self._fetch(digest):
            raise AssetNotFound(f"asset:{digest}")
        return super().info(digest)

    def resolve(self, handle: str) -> AssetInfo:
        digest = parse_handle(handle)
        if len(digest) == 64:
            return self.info(digest)
        assert len(digest) >= MIN_PREFIX_LEN
        try:
            return super().resolve(handle)  # an abbreviation is completed from local objects…
        except AssetNotFound:
            pass
        found = {  # …then from the backend's listing
            k.rsplit("/", 1)[1]
            for k in self.backend.keys(f"{self.prefix}objects/{digest[:2]}/{digest}")
            if not k.endswith(".json")
        }
        if not found:
            raise AssetNotFound(handle)
        if len(found) > 1:
            raise AmbiguousHandle(handle, len(found))
        return self.info(next(iter(found)))
