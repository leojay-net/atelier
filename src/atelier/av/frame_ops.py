"""Take one still picture out of a video (docs/03 §3.4)."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from atelier.av.preview import _grab
from atelier.av.probe import probe_av
from atelier.media import MediaType
from atelier.ops.image._io import image_summary, save
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op
from atelier.spec.types import Timecode


class FrameParams(Params):
    time: Timecode = Field(0.0, description="which moment, in seconds or [HH:]MM:SS[.mmm]")


@op(
    name="video.frame",
    version="1.0.0",
    inputs=[MediaType.VIDEO],
    output=MediaType.IMAGE,
    determinism=Determinism.PERCEPTUAL,
    params=FrameParams,
    summary=image_summary,
    description="take one frame of a video as a full-size image (a thumbnail, a poster)",
    resources=("ffmpeg", "ffprobe", "pillow", "icc"),
)
def frame(ctx: OpContext, inputs: Sequence[Artifact], p: FrameParams) -> Artifact:
    """The frame shown at `time`, at the video's own size with its rotation applied, as an
    image other image ops can use (`image.text` for a title, `export` with a thumbnail preset)."""
    src = inputs[0]
    meta = src.meta if src.meta.get("video") else probe_av(src.path, src.ext)
    if not meta.get("video"):
        raise OpError("this file has no video stream")
    duration = float(meta.get("duration") or 0)
    if p.time >= duration:
        raise OpError(f"time {p.time:g}s is past the end: the video is {duration:.2f}s long")
    width = meta["video"]["display"][0]
    im = _grab(src.path, src.ext, p.time, width, ctx.work_dir / "frame.png")
    return save(ctx, im)
