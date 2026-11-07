"""Removing the background of a video, frame by frame, with the U2-Net (small) model that
`image.remove_background` already uses.

Like `video.blur_faces` this decodes and re-encodes by itself (two ffmpeg processes joined by
raw frames), because the subject differs on every frame. Each frame's mask is blended with the
last one's so the edge does not flicker. The new background is a plain colour or the original
picture blurred. Short clips only: every frame goes through the network.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageFilter
from pydantic import Field

from atelier.av.binaries import PROTOCOLS, find
from atelier.av.export import av_summary
from atelier.av.graph import GraphError, Source, clean_error, decode_check, num
from atelier.av.probe import DEMUXERS, probe_av
from atelier.media import MediaType
from atelier.ml.segment import subject_mask
from atelier.ml.upscale import _parse_sar, _read_exact
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op

MATTE_MAX_SECONDS = 30.0  # every frame goes through the network: bounded for wall time
TIMEOUT = 3600.0
SMOOTH = 0.6  # how much of the new mask (the rest is the previous one): steadies the edge


class RemoveVideoBackgroundParams(Params):
    background: Literal["color", "blur"] = Field(
        "color", description="color: a plain colour; blur: the original picture, blurred"
    )
    color: str = Field("#00b140", pattern=r"^#[0-9a-fA-F]{6}$", description="the plain colour")
    feather: float = Field(
        0.0, ge=0, le=1, description="0 is a hard edge; higher softens the cut-out edge"
    )  # fmt: skip


def _hex(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _run(src: Artifact, out: Any, p: RemoveVideoBackgroundParams, *, width: int, height: int,
         fps: float, has_audio: bool) -> int:  # fmt: skip
    demuxer = DEMUXERS.get(src.ext, "")
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    base = [find("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y", "-xerror"]  # fmt: skip
    decode_argv = [*base, "-protocol_whitelist", PROTOCOLS]
    if demuxer:
        decode_argv += ["-f", demuxer]
    decode_argv += ["-i", str(src.path), "-map", "0:v:0"]
    decode_argv += ["-vf", f"fps={num(fps)},scale=w={width}:h={height}:flags=lanczos,setsar=1"]
    decode_argv += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    encode_argv = [*base, "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", f"{fps}", "-i", "-"]  # fmt: skip
    if has_audio:
        encode_argv += ["-protocol_whitelist", PROTOCOLS]
        if demuxer:
            encode_argv += ["-f", demuxer]
        encode_argv += ["-i", str(src.path), "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        encode_argv += ["-map", "0:v:0"]
    encode_argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-threads", "4"]  # fmt: skip
    encode_argv += ["-c:a", "aac", "-b:a", "192k"] if has_audio else ["-an"]
    encode_argv += ["-movflags", "+faststart", "-f", "mp4", "-map_metadata", "-1", str(out)]

    work = out.parent
    decode_err, encode_err = work / "matte_decode.log", work / "matte_encode.log"
    frame_size = width * height * 3
    deadline = time.monotonic() + TIMEOUT
    flat = np.zeros((height, width, 3), dtype=np.uint8)
    flat[:] = _hex(p.color)
    frames = 0
    prev: np.ndarray | None = None
    with decode_err.open("wb") as derr, encode_err.open("wb") as eerr:
        decode = subprocess.Popen(decode_argv, stdout=subprocess.PIPE, stderr=derr, stdin=subprocess.DEVNULL, env=env)  # fmt: skip
        encode = subprocess.Popen(encode_argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=eerr, env=env)  # fmt: skip
        assert decode.stdout is not None and encode.stdin is not None
        try:
            while True:
                if time.monotonic() > deadline:
                    raise GraphError("background removal timed out")
                buf = _read_exact(decode.stdout, frame_size)
                if buf is None:
                    break
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
                image = Image.fromarray(frame, "RGB")
                mask = subject_mask(image)
                if p.feather > 0:
                    mask = mask.filter(ImageFilter.GaussianBlur(p.feather * max(width, height) / 100))
                m = np.asarray(mask, dtype=np.float32) / 255.0
                m = m if prev is None else SMOOTH * m + (1 - SMOOTH) * prev
                prev = m
                if p.background == "blur":
                    small = image.resize((max(2, width // 6), max(2, height // 6)))
                    back = np.asarray(
                        small.filter(ImageFilter.GaussianBlur(4)).resize((width, height), Image.Resampling.BILINEAR)
                    )
                else:
                    back = flat
                alpha = m[..., None]
                mixed = (frame * alpha + back * (1 - alpha)).astype(np.uint8)
                frames += 1
                encode.stdin.write(mixed.tobytes())
        except BrokenPipeError:
            pass
        except BaseException:
            decode.kill()
            encode.kill()
            decode.wait()
            encode.wait()
            raise
        finally:
            encode.stdin.close()
        try:
            decode.wait(timeout=TIMEOUT)
            encode.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            decode.kill()
            encode.kill()
            raise GraphError("background removal timed out") from None
    fake = Source(src.path, MediaType.VIDEO, src.ext, {})
    if decode.returncode != 0:
        raise GraphError(clean_error(decode_err.read_bytes(), work, out, [fake]))
    if encode.returncode != 0:
        raise GraphError(clean_error(encode_err.read_bytes(), work, out, [fake]))
    return frames


@op(
    name="video.remove_background",
    version="1.0.0",
    inputs=(MediaType.VIDEO,),
    output=MediaType.VIDEO,
    determinism=Determinism.SEEDED_ML,
    params=RemoveVideoBackgroundParams,
    summary=av_summary,
    description="replace the background of a video with a colour (or a blur), keeping the subject",
    resources=("ffmpeg", "ffprobe", "ml_runtime", "model_u2netp"),
)
def remove_video_background(
    ctx: OpContext, inputs: Sequence[Artifact], p: RemoveVideoBackgroundParams
) -> Artifact:
    """Find the main subject in every frame (the same small network as image.remove_background)
    and put a plain colour, or a blurred copy of the picture, behind it. Clips up to 30 s. For a
    new background picture use a green colour here and then video.chromakey with the picture."""
    src = inputs[0]
    meta = src.meta if "video" in src.meta else probe_av(src.path, src.ext)
    v = meta.get("video")
    if not v:
        raise OpError("this file has no video stream")
    if float(meta.get("duration") or 0) > MATTE_MAX_SECONDS:
        raise OpError(f"background removal is limited to {MATTE_MAX_SECONDS:g}s clips: trim it first")
    fps = float(v.get("fps") or 30.0)
    dw, dh = v["display"]
    sar = _parse_sar(v.get("sar"))
    width = max(2, round(dw * sar / 2) * 2) if abs(sar - 1.0) > 0.001 else dw
    out = ctx.output_path(".mp4")
    frames = _run(src, out, p, width=width, height=dh, fps=fps, has_audio=bool(meta.get("audio")))
    problem = decode_check(out)
    if problem is not None:
        raise OpError(f"the encoded output does not decode cleanly: {problem}")
    probed = probe_av(out, ".mp4")
    result_meta: dict[str, Any] = {
        **probed, "bytes": out.stat().st_size, "verified": True, "export": None, "loudness": None,
        "analysis": None, "boxes": [], "graph": None, "matte": {"frames": frames},
    }  # fmt: skip
    return Artifact(out, MediaType.VIDEO, ".mp4", result_meta)
