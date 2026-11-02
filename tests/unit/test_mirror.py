"""The mirrored store: a local working copy with a durable copy elsewhere (directory or S3)."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from atelier.canonical import sha256_hex
from atelier.engine.mirror import DirBackend, MirroredStore, S3Backend
from atelier.engine.store import LocalStore
from atelier.media import AmbiguousHandle, AssetNotFound, MediaType
from conftest import FIXTURES


class Counting(DirBackend):
    """A directory backend that remembers what was uploaded."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.uploads: list[str] = []

    def upload(self, local: Path, key: str) -> None:
        self.uploads.append(key)
        super().upload(local, key)


def two_nodes(tmp_path: Path, backend: Any = None) -> tuple[MirroredStore, MirroredStore, Any]:
    remote = backend or Counting(tmp_path / "bucket")
    return (
        MirroredStore(tmp_path / "node-a", remote, "acme"),
        MirroredStore(tmp_path / "node-b", remote, "acme"),
        remote,
    )


def test_an_asset_added_on_one_node_is_usable_on_another(tmp_path: Path) -> None:
    a, b, _ = two_nodes(tmp_path)
    info = a.put_file(FIXTURES / "photo.png")
    assert b.has(info.hash) and not b._object(info.hash).exists()  # known, not yet fetched
    got = b.info(info.hash)
    assert (got.hash, got.media, got.ext, got.size) == (
        info.hash,
        MediaType.IMAGE,
        ".png",
        info.size,
    )
    path = b.path(info.hash)
    assert path.read_bytes() == (FIXTURES / "photo.png").read_bytes()
    assert path.stat().st_mode & 0o222 == 0  # fetched objects are read-only like every object


def test_bytes_are_uploaded_once_however_often_they_are_added(tmp_path: Path) -> None:
    a, b, remote = two_nodes(tmp_path)
    a.put_file(FIXTURES / "hello.txt")
    first = len(remote.uploads)
    a.put_file(FIXTURES / "hello.txt")
    b.put_file(FIXTURES / "hello.txt")  # another node adds the same content
    assert first == 2 and len(remote.uploads) == 2  # the object and its sidecar, once


def test_a_job_output_moved_into_the_store_is_mirrored_too(tmp_path: Path) -> None:
    a, b, _ = two_nodes(tmp_path)
    work = a.work_dir("job-")
    out = work / "output.txt"
    out.write_bytes(b"a result")
    info = a.put_file(out, media=MediaType.TEXT, ext=".txt", move=True)
    assert b.path(info.hash).read_bytes() == b"a result"
    assert b.info(info.hash).media is MediaType.TEXT


def test_abbreviated_handles_resolve_through_the_backend(tmp_path: Path) -> None:
    a, b, _ = two_nodes(tmp_path)
    info = a.put_file(FIXTURES / "photo.png")
    assert b.resolve(info.handle[:20]).hash == info.hash  # a prefix, never seen on this node
    assert b.resolve(info.handle).hash == info.hash
    with pytest.raises(AssetNotFound):
        b.resolve("asset:" + "0" * 12)


def test_a_prefix_shared_by_two_assets_is_ambiguous(tmp_path: Path) -> None:
    a, b, remote = two_nodes(tmp_path)
    one = a.put_file(FIXTURES / "hello.txt")
    other_digest = one.hash[:8] + "f" * 56
    remote.upload(a._object(one.hash), f"acme/objects/{other_digest[:2]}/{other_digest}")
    with pytest.raises(AmbiguousHandle):
        b.resolve("asset:" + one.hash[:8])


def test_a_damaged_copy_in_the_backend_is_refused_not_used(tmp_path: Path) -> None:
    a, b, _ = two_nodes(tmp_path)
    info = a.put_file(FIXTURES / "hello.txt")
    (tmp_path / "bucket" / "acme" / "objects" / info.hash[:2] / info.hash).write_bytes(b"tampered")
    with pytest.raises(OSError, match="damaged"):
        b.path(info.hash)
    assert not b._object(info.hash).exists()  # nothing half-written is left behind


def test_unknown_assets_stay_unknown(tmp_path: Path) -> None:
    _, b, _ = two_nodes(tmp_path)
    digest = sha256_hex(b"never stored")
    assert not b.has(digest)
    with pytest.raises(AssetNotFound):
        b.info(digest)


def test_two_nodes_fetching_the_same_object_at_once_agree(tmp_path: Path) -> None:
    a, _, remote = two_nodes(tmp_path)
    info = a.put_file(FIXTURES / "photo.png")
    b = MirroredStore(tmp_path / "node-b", remote, "acme")
    errors: list[BaseException] = []

    def fetch() -> None:
        try:
            assert b.path(info.hash).read_bytes() == (FIXTURES / "photo.png").read_bytes()
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=fetch) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []


def test_organizations_share_a_backend_without_seeing_each_other(tmp_path: Path) -> None:
    remote = DirBackend(tmp_path / "bucket")
    acme = MirroredStore(tmp_path / "acme", remote, "acme")
    globex = MirroredStore(tmp_path / "globex", remote, "globex")
    info = acme.put_file(FIXTURES / "photo.png")
    assert not globex.has(info.hash)  # a different prefix: even the hash is not revealed
    with pytest.raises(AssetNotFound):
        globex.resolve(info.handle)


def test_the_plain_local_store_is_unchanged(tmp_path: Path) -> None:
    local = LocalStore(tmp_path / "s")
    info = local.put_file(FIXTURES / "hello.txt")
    assert local.has(info.hash) and local.path(info.hash).is_file()
    shutil.rmtree(tmp_path / "s")


# -- S3 ---------------------------------------------------------------------------------------


@pytest.fixture
def bucket() -> Any:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="atelier-test")
        yield S3Backend("atelier-test", client=client)


def test_s3_round_trip_between_two_nodes(tmp_path: Path, bucket: S3Backend) -> None:
    a, b, _ = two_nodes(tmp_path, bucket)
    info = a.put_file(FIXTURES / "photo.png")
    assert bucket.exists(f"acme/objects/{info.hash[:2]}/{info.hash}")
    assert b.resolve(info.handle[:16]).hash == info.hash
    assert b.path(info.hash).read_bytes() == (FIXTURES / "photo.png").read_bytes()
    assert not b.has(sha256_hex(b"nope"))
    assert (
        len(bucket.keys(f"acme/objects/{info.hash[:2]}/{info.hash[:8]}")) == 2
    )  # object + sidecar
