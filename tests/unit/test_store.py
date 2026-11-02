from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from atelier.engine.store import LocalStore
from atelier.media import AmbiguousHandle, AssetNotFound, InvalidHandle, MediaType


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path / "store")


def test_put_is_content_addressed_and_deduped(store: LocalStore, tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.md"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    ia, ib = store.put_file(a), store.put_file(b)
    assert ia.hash == ib.hash
    assert ia.hash == hashlib.sha256(b"same").hexdigest()
    assert store.path(ia.hash).read_bytes() == b"same"
    assert a.exists()  # put_file copies unless move=True


def test_objects_are_read_only(store: LocalStore) -> None:
    info = store.put_bytes(b"x")
    assert store.path(info.hash).stat().st_mode & 0o222 == 0


def test_resolve_prefix(store: LocalStore) -> None:
    info = store.put_bytes(b"abc")
    assert store.resolve(f"asset:{info.hash[:8]}") == info
    assert store.resolve(info.handle.upper().replace("ASSET:", "asset:")) == info


def test_resolve_errors(store: LocalStore) -> None:
    with pytest.raises(InvalidHandle):
        store.resolve("asset:abc")  # too short
    with pytest.raises(InvalidHandle):
        store.resolve("file:///etc/passwd")
    with pytest.raises(AssetNotFound):
        store.resolve("asset:" + "0" * 64)


def test_ambiguous_prefix(store: LocalStore) -> None:
    # Plant a second object sharing the prefix; real collisions are too rare to construct.
    info = store.put_bytes(b"one")
    fake = info.hash[:10] + "f" * 54
    store.path(fake).write_bytes(b"planted")
    with pytest.raises(AmbiguousHandle):
        store.resolve(f"asset:{info.hash[:10]}")


def _zip(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("data", "media", "ext"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 8, MediaType.IMAGE, ".png"),
        (b"\xff\xd8\xff\xe0" + b"\0" * 8, MediaType.IMAGE, ".jpg"),
        (b"%PDF-1.7\n", MediaType.PDF, ".pdf"),
        (b"\0\0\0\x18ftypmp42" + b"\0" * 8, MediaType.VIDEO, ".mp4"),
        (b"RIFF\0\0\0\0WAVEfmt ", MediaType.AUDIO, ".wav"),
        (_zip(["word/document.xml"]), MediaType.DOCX, ".docx"),
        (_zip(["xl/workbook.xml"]), MediaType.XLSX, ".xlsx"),
        (_zip(["ppt/presentation.xml"]), MediaType.PPTX, ".pptx"),
        (_zip(["other.txt"]), MediaType.BINARY, ".zip"),
        ("héllo\n".encode(), MediaType.TEXT, ".txt"),
        (b"\x00\x01\x02", MediaType.BINARY, ""),
    ],
)
def test_sniffing(store: LocalStore, data: bytes, media: MediaType, ext: str) -> None:
    info = store.put_bytes(data)
    assert (info.media, info.ext) == (media, ext)


def test_text_cut_mid_character_at_probe_boundary(store: LocalStore) -> None:
    data = b"a" * (64 * 1024 - 1) + "é".encode() + b"tail"
    assert store.put_bytes(data).media is MediaType.TEXT
