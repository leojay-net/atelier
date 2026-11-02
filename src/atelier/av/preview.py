"""Video previews for the escalation ladder (docs/06 §6.2).

`preview.frames` turns a video into one small contact sheet of timestamped frames: the cheap
way to *look* at a video (a sheet of six frames costs about one image) instead of sending
frames one by one. Like every preview it is a hidden op: sandboxed, cached, small by design.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from PIL import Image
from pydantic import Field

from atelier.av.binaries import PROTOCOLS, find, run
from atelier.av.graph import num
from atelier.av.probe import DEMUXERS, probe_av
from atelier.media import MediaType
from atelier.ops.image.grid import MAX_CELLS, build_grid
from atelier.ops.preview import _encode, preview_summary
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op
from atelier.spec.types import Timecode

FRAME_TIMEOUT = 60


class FramesParams(Params):
    count: int = Field(6, ge=1, le=MAX_CELLS, description="how many evenly spaced frames")
    at: list[Timecode] | None = Field(
        None, description="exact times in seconds instead of even spacing"
    )
    columns: int | None = Field(None, ge=1, le=8, description="null = square-ish")
    cell_width: int = Field(256, ge=64, le=512, description="px per frame")


def _grab(path: Path, ext: str, t: float, width: int, out: Path) -> Image.Image:
    argv = [
        find("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        PROTOCOLS,
    ]
    if demuxer := DEMUXERS.get(ext):
        argv += ["-f", demuxer]
    argv += [
        "-ss",
        num(t),
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2:flags=lanczos",
        "-y",
        str(out),
    ]
    try:
        done = run(argv, timeout=FRAME_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise OpError("timed out reading a frame") from None
    if done.returncode != 0 or not out.exists():
        raise OpError(f"could not read the frame at {t:.2f}s")
    with Image.open(out) as im:
        im.load()
        return im.convert("RGB")


def _label(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


@op(
    name="preview.frames",
    version="1.0.0",
    inputs=[MediaType.VIDEO],
    output=MediaType.IMAGE,
    determinism=Determinism.PERCEPTUAL,
    params=FramesParams,
    summary=preview_summary,
    description="contact sheet of timestamped frames from a video (L2)",
    hidden=True,
    resources=("ffmpeg", "ffprobe", "pillow", "icc", "fonts"),
)
def frames(ctx: OpContext, inputs: Sequence[Artifact], p: FramesParams) -> Artifact:
    """One image of `count` evenly spaced frames (or the given times), each labelled with its
    timestamp. The frames are what a viewer sees: rotation is applied, and they are scaled
    down so the sheet stays cheap to look at."""
    src = inputs[0]
    meta = src.meta if src.meta.get("video") else probe_av(src.path, src.ext)
    if not meta.get("video"):
        raise OpError("this file has no video stream to preview")
    duration = float(meta.get("duration") or 0)
    if p.at is not None:
        times = list(p.at)
        if any(t > duration for t in times):
            raise OpError(f"a preview time is past the end: the video is {duration:.2f}s long")
    else:
        times = [duration * (i + 0.5) / p.count for i in range(p.count)]
    w, h = meta["video"]["display"]
    # Frames fit a cell_width x cell_width box, so a vertical video costs no more than a wide one.
    cell = max(64, round(p.cell_width * min(1.0, w / h)) // 2 * 2)
    images = [
        _grab(src.path, src.ext, t, cell, ctx.work_dir / f"frame{i}.png")
        for i, t in enumerate(times)
    ]
    sheet = build_grid(
        images,
        columns=p.columns,
        cell_width=cell,
        aspect=w / h,
        fit="contain",
        gap=0.03,
        background="#ffffffff",
        labels=[_label(t) for t in times],
    )
    return _encode(ctx, sheet)
