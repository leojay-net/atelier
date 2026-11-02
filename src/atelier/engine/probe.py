"""Metadata probes. They run inside the worker sandbox because they parse untrusted files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from atelier.media import MediaType

# Bump when probe output changes; old cached probes are then ignored.
PROBE_VERSION = "5"
_TEXT_LIMIT = 1 << 20
_ROTATED = (5, 6, 7, 8)


def _image(path: Path) -> dict[str, Any]:
    import io

    from PIL import Image, ImageCms

    from atelier.ops.image._io import MAX_PIXELS

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with Image.open(path) as im:
        orientation = int(im.getexif().get(0x0112, 1))
        w, h = im.size
        meta: dict[str, Any] = {
            "format": (im.format or "?").lower(),
            "width": w,
            "height": h,
            "mode": im.mode,
            "frames": int(getattr(im, "n_frames", 1)),
            "orientation": orientation,
            "display": [h, w] if orientation in _ROTATED else [w, h],
            "icc": None,
            "dpi": None,
        }
        if icc := im.info.get("icc_profile"):
            try:
                profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                meta["icc"] = ImageCms.getProfileDescription(profile).strip()
            except (OSError, ImageCms.PyCMSError):
                meta["icc"] = "unreadable"
        if dpi := im.info.get("dpi"):
            meta["dpi"] = round(float(dpi[0]))
    return meta


def _text(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        data = f.read(_TEXT_LIMIT + 1)
    text = data[:_TEXT_LIMIT].decode("utf-8", errors="replace")
    return {
        "lines": len(text.splitlines()),
        "chars": len(text),
        "truncated": len(data) > _TEXT_LIMIT,
    }


def probe(path: Path, media: MediaType, ext: str = "") -> dict[str, Any]:
    if media is MediaType.IMAGE:
        return _image(path)
    if media is MediaType.TEXT:
        return _text(path)
    if media in (MediaType.VIDEO, MediaType.AUDIO):
        from atelier.av.probe import probe_av

        return probe_av(path, ext)
    if media in (MediaType.PDF, MediaType.DOCX, MediaType.XLSX, MediaType.PPTX):
        from atelier.docs.probe import probe_document

        return probe_document(path, media)
    return {}
