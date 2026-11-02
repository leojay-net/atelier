"""Pinned resources (binaries, font packs, model weights) and their IDs for cache keys.

An op declares which resources it touches; only those IDs go into its step key, so
upgrading ffmpeg invalidates video steps but not image steps.
"""

from __future__ import annotations

import functools
import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

from atelier.canonical import sha256_file


class ResourceUnavailable(Exception):
    pass


def _binary_id(env_var: str, name: str, hint: str = "") -> str:
    path = os.environ.get(env_var) or shutil.which(name)
    if not path:
        raise ResourceUnavailable(
            hint or f"{name} not found; install the pinned build or set {env_var}"
        )
    return f"{name}:sha256:{sha256_file(Path(path))[:16]}"


def _pillow_id() -> str:
    """Pillow plus the codec/library versions that can change output bytes."""
    import PIL
    from PIL import features

    parts = [f"pillow:{PIL.__version__}"]
    for feature in ("jpg", "zlib", "webp", "avif", "freetype2", "littlecms2", "libtiff", "raqm"):
        parts.append(f"{feature}:{features.version(feature) or '-'}")
    return ",".join(parts)


def _fonts_id() -> str:
    from atelier.fonts import pack_id

    return pack_id()


def _icc_id() -> str:
    from atelier.paths import SRGB_ICC

    return f"icc:sha256:{sha256_file(SRGB_ICC)[:16]}"


def _versions(label: str, *packages: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    parts = [label]
    for name in packages:
        try:
            parts.append(f"{name}:{version(name)}")
        except PackageNotFoundError:
            parts.append(f"{name}:-")
    return ",".join(parts)


def _office_id() -> str:
    """The libraries that read and write Word, Excel and PowerPoint files."""
    return _versions("office", "python-docx", "python-pptx", "openpyxl", "lxml")


def _pdf_id() -> str:
    """The libraries that read, write, render and search PDFs (qpdf is inside pikepdf)."""
    import pikepdf

    return (
        _versions("pdf", "pikepdf", "pypdfium2", "pypdf") + f",qpdf:{pikepdf.__libqpdf_version__}"
    )


def _model(name: str) -> Callable[[], str]:
    """A pinned model as a resource: its id is its manifest hashes, and it must be installed."""

    def provider() -> str:
        from atelier import models

        try:
            return models.model_id(name)
        except models.ModelError as e:
            raise ResourceUnavailable(str(e)) from None

    return provider


def _ml_runtime_id() -> str:
    """The libraries that run the models: a different version can round differently."""
    return _versions(
        "ml", "onnxruntime", "faster-whisper", "ctranslate2", "numpy",
        "opencv-contrib-python-headless",
    )  # fmt: skip


_PROVIDERS: dict[str, Callable[[], str]] = {
    "ml_runtime": _ml_runtime_id,
    "model_u2netp": _model("u2netp"),
    "model_rnnoise_bd": _model("rnnoise-bd"),
    "model_whisper_tiny": _model("whisper-tiny"),
    "model_yunet": _model("yunet"),
    "model_fsrcnn": _model("fsrcnn"),
    "libreoffice": lambda: _binary_id(
        "ATELIER_SOFFICE",
        "soffice",
        "LibreOffice is not installed here, so Word, PowerPoint and Excel files cannot be "
        "converted, previewed or recalculated (it is part of the reference image; or set "
        "ATELIER_SOFFICE)",
    ),
    "office": _office_id,
    "pdf": _pdf_id,
    "ffmpeg": lambda: _binary_id("ATELIER_FFMPEG", "ffmpeg"),
    "ffprobe": lambda: _binary_id("ATELIER_FFPROBE", "ffprobe"),
    "fonts": _fonts_id,
    "icc": _icc_id,
    "pillow": _pillow_id,
}

KNOWN_RESOURCES = frozenset(_PROVIDERS)


@functools.cache
def resource_id(name: str) -> str:
    return _PROVIDERS[name]()


def resolve(names: Iterable[str]) -> dict[str, str]:
    return {name: resource_id(name) for name in sorted(names)}
