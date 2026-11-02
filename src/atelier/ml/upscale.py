"""Upscaling images and video with FSRCNN, a small super-resolution CNN (OpenCV's `dnn_superres`).

`image.upscale` is a normal image op (one forward pass). `video.upscale` has the same problem
`video.blur_faces` does: every frame needs a different full pass through a neural network, which
no ffmpeg filter expression captures, so it cannot join the fused graph either. It reuses the
same shape: its own decode (ffmpeg -> raw BGR24 on a pipe), its own Python loop (one forward
pass per frame, no detection step needed since every pixel is transformed), its own encode (raw
BGR24 on a pipe -> ffmpeg), connected by two concurrent subprocesses this process pumps frames
between. Because the cost is a full network pass per frame (not a lightweight detector), the
source resolution is capped, not just the duration: upscaling is for small source video, not
for making an already-large one larger.
"""

from __future__ import annotations

import functools
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any, Literal

import numpy as np
from PIL import Image
from pydantic import Field

from atelier import models
from atelier.av.binaries import PROTOCOLS, find
from atelier.av.export import av_summary
from atelier.av.graph import GraphError, Source, clean_error, decode_check, num
from atelier.av.probe import DEMUXERS, probe_av
from atelier.media import MediaType
from atelier.ops.image._io import MAX_PIXELS, image_op, load, save
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op

NUM_THREADS = 4  # fixed, not derived from the machine; see _super_res()
UPSCALE_MAX_SOURCE_DIM = 640  # video only: a full network pass per frame is costly per pixel
UPSCALE_MAX_SECONDS = 60.0  # video only: every frame is decoded, upscaled and re-encoded
TIMEOUT = 3600.0


class UpscaleParams(Params):
    scale: Literal[2, 3, 4] = Field(2, description="how many times bigger, each side")


@functools.cache
def _super_res(scale: int) -> Any:
    """One instance per (worker process, scale): cheap to reuse across every frame of a video
    job, and each worker is single-use, so nothing goes stale. A *fixed* thread count, not the
    host's core count: confirmed (same count, same bytes, every time; repeats and thread counts
    1/2/4/8 all gave bit-identical results on this build, the same check made for YuNet) so this
    is not required for determinism the way onnxruntime's was, but is pinned anyway so behavior
    does not depend on what else is running on the host."""
    try:
        import cv2
    except ImportError:
        raise OpError(
            "upscaling needs the ML runtime: install it with `pip install atelier[ml]`"
        ) from None
    try:
        path = models.file_path("fsrcnn", f"FSRCNN_x{scale}.pb")
    except models.ModelError as e:
        raise OpError(str(e)) from None
    cv2.setNumThreads(NUM_THREADS)
    sr = cv2.dnn_superres.DnnSuperResImpl_create()  # type: ignore[attr-defined]
    sr.readModel(str(path))
    sr.setModel("fsrcnn", scale)
    return sr


def _check_pixels(w: int, h: int, scale: int) -> None:
    out_w, out_h = w * scale, h * scale
    if out_w * out_h > MAX_PIXELS:
        raise OpError(
            f"upscaling {w}x{h} by {scale}x would make a {out_w}x{out_h} image, over the "
            f"{MAX_PIXELS:,} pixel limit: try a smaller scale"
        )


def _parse_sar(text: str | None) -> float:
    if not text or ":" not in text:
        return 1.0
    a, _, b = text.partition(":")
    try:
        return float(a) / float(b) if float(b) else 1.0
    except ValueError:
        return 1.0


def _read_exact(stream: IO[bytes], n: int) -> bytes | None:
    """Exactly `n` bytes, or None (clean end, or a truncated trailing frame: both discarded)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


@image_op(
    name="image.upscale",
    version="1.0.0",
    params=UpscaleParams,
    description="enlarge a photo with a super-resolution model",
    determinism=Determinism.SEEDED_ML,
    resources=("pillow", "icc", "ml_runtime", "model_fsrcnn"),
)
def upscale(ctx: OpContext, inputs: Sequence[Artifact], p: UpscaleParams) -> Artifact:
    """Make a photo bigger with a small neural network trained to add plausible detail, not
    just stretch pixels: sharper than a plain resize, especially past 2x. Works best on real
    photos; a screenshot or flat graphic may pick up odd texture, so check with `preview`. For
    a quick resize with no model, use `image.resize`."""
    im = load(inputs[0])
    _check_pixels(im.width, im.height, p.scale)
    rgb = np.asarray(im.convert("RGB"))
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    up_bgr = _super_res(p.scale).upsample(bgr)
    up_rgb = np.ascontiguousarray(up_bgr[:, :, ::-1])
    out = Image.fromarray(up_rgb, "RGB")
    if im.mode in ("LA", "RGBA"):
        alpha = im.convert("RGBA").getchannel("A").resize(out.size, Image.Resampling.LANCZOS)
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return save(ctx, out)


def _run_pipeline(
    src: Artifact,
    out: Path,
    p: UpscaleParams,
    *,
    width: int,
    height: int,
    fps: float,
    has_audio: bool,
) -> int:
    """Decode `src` to raw BGR24, upscale every frame, encode `out`. Returns the frame count."""
    sr = _super_res(p.scale)
    out_width, out_height = width * p.scale, height * p.scale

    demuxer = DEMUXERS.get(src.ext, "")
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    base = [
        find("ffmpeg"),
        "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y", "-xerror",
    ]  # fmt: skip

    decode_argv = [*base, "-protocol_whitelist", PROTOCOLS]
    if demuxer:
        decode_argv += ["-f", demuxer]
    decode_argv += ["-i", str(src.path), "-map", "0:v:0"]
    decode_argv += ["-vf", f"fps={num(fps)},scale=w={width}:h={height}:flags=lanczos,setsar=1"]
    decode_argv += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]

    encode_argv = [
        *base,
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{out_width}x{out_height}", "-r", f"{fps}", "-i", "-",
    ]  # fmt: skip
    if has_audio:
        encode_argv += ["-protocol_whitelist", PROTOCOLS]
        if demuxer:
            encode_argv += ["-f", demuxer]
        encode_argv += ["-i", str(src.path), "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        encode_argv += ["-map", "0:v:0"]
    encode_argv += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
        "-pix_fmt", "yuv420p", "-threads", "4",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", "-color_range", "tv",
    ]  # fmt: skip
    encode_argv += ["-c:a", "aac", "-b:a", "192k"] if has_audio else ["-an"]
    encode_argv += [
        "-movflags", "+faststart", "-f", "mp4", "-map_metadata", "-1", "-map_chapters", "-1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", str(out),
    ]  # fmt: skip

    work = out.parent
    decode_err = work / "upscale_decode.log"
    encode_err = work / "upscale_encode.log"
    in_size = width * height * 3
    deadline = time.monotonic() + TIMEOUT
    frames = 0

    with decode_err.open("wb") as derr, encode_err.open("wb") as eerr:
        decode = subprocess.Popen(
            decode_argv, stdout=subprocess.PIPE, stderr=derr, stdin=subprocess.DEVNULL, env=env
        )
        encode = subprocess.Popen(
            encode_argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=eerr, env=env
        )
        assert decode.stdout is not None and encode.stdin is not None
        try:
            while True:
                if time.monotonic() > deadline:
                    raise GraphError("upscaling timed out")
                buf = _read_exact(decode.stdout, in_size)
                if buf is None:
                    break
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
                big = sr.upsample(frame)
                frames += 1
                encode.stdin.write(big.tobytes())
        except BrokenPipeError:
            pass  # the encoder exited early; its own log/returncode explains why
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
            raise GraphError("upscaling timed out") from None

    fake_source = Source(src.path, MediaType.VIDEO, src.ext, {})
    if decode.returncode != 0:
        raise GraphError(clean_error(decode_err.read_bytes(), work, out, [fake_source]))
    if encode.returncode != 0:
        raise GraphError(clean_error(encode_err.read_bytes(), work, out, [fake_source]))
    return frames


@op(
    name="video.upscale",
    version="1.0.0",
    inputs=(MediaType.VIDEO,),
    output=MediaType.VIDEO,
    determinism=Determinism.SEEDED_ML,
    params=UpscaleParams,
    summary=av_summary,
    description="enlarge a video with a super-resolution model",
    resources=("ffmpeg", "ffprobe", "ml_runtime", "model_fsrcnn"),
    hidden=True,  # catalogue budget (docs/08 Phase 6): the more specialized of the pair,
    # same trade as video.stabilize; still fully callable by name, and describe_op works
)
def video_upscale(ctx: OpContext, inputs: Sequence[Artifact], p: UpscaleParams) -> Artifact:
    """Make a video bigger with the same model `image.upscale` uses, one frame at a time: sharper
    than a plain resize, especially past 2x. Costs a full network pass per frame, so the source is
    limited to 640px on the longest side and 60s: upscale a small source, not an already-large
    one. For a quick resize with no model, use `video.reframe`."""
    src = inputs[0]
    meta = src.meta if "video" in src.meta else probe_av(src.path, src.ext)
    v = meta.get("video")
    if not v:
        raise OpError("this file has no video stream to upscale")
    duration = float(meta.get("duration") or 0)
    if duration > UPSCALE_MAX_SECONDS:
        raise OpError(
            f"video upscaling is limited to {UPSCALE_MAX_SECONDS:g}s clips: trim it first"
        )
    fps = float(v.get("fps") or 30.0)
    dw, dh = v["display"]
    sar = _parse_sar(v.get("sar"))
    width = max(2, round(dw * sar / 2) * 2) if abs(sar - 1.0) > 0.001 else dw
    height = dh
    if max(width, height) > UPSCALE_MAX_SOURCE_DIM:
        raise OpError(
            f"video upscaling is limited to {UPSCALE_MAX_SOURCE_DIM}px on the longest side "
            f"(this source is {width}x{height}): downscale it first"
        )
    _check_pixels(width, height, p.scale)
    has_audio = bool(meta.get("audio"))

    out = ctx.output_path(".mp4")
    frames = _run_pipeline(src, out, p, width=width, height=height, fps=fps, has_audio=has_audio)
    problem = decode_check(out)
    if problem is not None:
        raise OpError(f"the encoded output does not decode cleanly: {problem}")
    probed = probe_av(out, ".mp4")
    result_meta: dict[str, Any] = {
        **probed,
        "bytes": out.stat().st_size,
        "verified": True,
        "export": None,
        "loudness": None,
        "analysis": None,
        "boxes": [],
        "graph": None,
        "upscale": {"frames": frames, "scale": p.scale},
    }
    return Artifact(out, MediaType.VIDEO, ".mp4", result_meta)
