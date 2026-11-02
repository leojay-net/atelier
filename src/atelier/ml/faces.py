"""Blurring faces in video, frame by frame, with YuNet (OpenCV Zoo).

Unlike the other video ops, this one cannot contribute to the fused ffmpeg filtergraph: the
regions to blur vary per frame, in number and position, which no filter expression captures.
So it decodes and re-encodes on its own, in two ffmpeg processes connected by raw BGR24 frames
streamed through this process (never buffered whole in memory: a minute of 1080p would be
several gigabytes). Detection runs on every frame, at a capped resolution; the blur itself is
applied at full resolution, so quality is unaffected away from a face.
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
from pydantic import Field

from atelier import models
from atelier.av.binaries import PROTOCOLS, find
from atelier.av.export import av_summary
from atelier.av.graph import GraphError, Source, clean_error, decode_check, num
from atelier.av.probe import DEMUXERS, probe_av
from atelier.media import MediaType
from atelier.ops.registry import Artifact, Determinism, OpContext, OpError, Params, op

DETECT_SIZE = 640  # longest side fed to the detector; bigger does not catch more, only costs more
FACE_MAX_SECONDS = 180.0  # every frame is decoded, detected and re-encoded: bounded for wall time
NUM_THREADS = 4  # fixed, not derived from the machine; see _detector()
TIMEOUT = 3600.0


class BlurFacesParams(Params):
    style: Literal["blur", "pixelate", "box"] = Field(
        "blur", description="blur: soften; pixelate: blocky; box: solid black fill"
    )
    expand: float = Field(
        0.25, ge=0, le=1, description="grow each detected box by this fraction of its size, "
        "on every side, for hair and chin coverage"
    )  # fmt: skip
    min_score: float = Field(
        0.6, ge=0.01, le=0.99, description="lower catches more faces, with more false positives"
    )


@functools.cache
def _detector() -> Any:
    """One detector per worker process: cheap to reuse across every frame of the job, and each
    worker is single-use, so nothing goes stale. A *fixed* thread count, not the host's core
    count: confirmed (same count, same detections, every time; repeats and thread counts 1/2/4/8
    all gave bit-identical results on this build, unlike libvidstab's OpenMP or onnxruntime's
    intra-op reduction) so this is not required for determinism the way those were, but is
    pinned anyway so behavior does not depend on what else is running on the host."""
    try:
        import cv2
    except ImportError:
        raise OpError(
            "face blurring needs the ML runtime: install it with `pip install atelier[ml]`"
        ) from None
    try:
        path = models.file_path("yunet", "face_detection_yunet_2023mar.onnx")
    except models.ModelError as e:
        raise OpError(str(e)) from None
    cv2.setNumThreads(NUM_THREADS)
    return cv2.FaceDetectorYN_create(  # type: ignore[attr-defined]
        str(path), "", (DETECT_SIZE, DETECT_SIZE)
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


def _treat(frame: np.ndarray, box: tuple[int, int, int, int], style: str) -> None:
    """Blur/pixelate/black out `box` (x0, y0, x1, y1) of `frame`, in place."""
    import cv2

    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return
    roi = frame[y0:y1, x0:x1]
    if style == "box":
        roi[:] = 0
    elif style == "pixelate":
        h, w = roi.shape[:2]
        cell = max(1, min(h, w) // 12)
        tiny_size = (max(1, w // cell), max(1, h // cell))
        tiny = cv2.resize(roi, tiny_size, interpolation=cv2.INTER_LINEAR)
        roi[:] = cv2.resize(tiny, (w, h), interpolation=cv2.INTER_NEAREST)
    else:  # blur
        k = max(3, (min(roi.shape[0], roi.shape[1]) // 4) | 1)
        roi[:] = cv2.GaussianBlur(roi, (k, k), 0)


def _boxes(
    detector: Any, small: np.ndarray, scale_x: float, scale_y: float, expand: float, w: int, h: int
) -> list[tuple[int, int, int, int]]:
    _, faces = detector.detect(small)
    if faces is None:
        return []
    out = []
    for f in faces:
        x, y = float(f[0]) * scale_x, float(f[1]) * scale_y
        bw, bh = float(f[2]) * scale_x, float(f[3]) * scale_y
        ex, ey = bw * expand, bh * expand
        x0, y0 = max(0, int(x - ex)), max(0, int(y - ey))
        x1, y1 = min(w, int(x + bw + ex)), min(h, int(y + bh + ey))
        out.append((x0, y0, x1, y1))
    return out


def _cv2_resize(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2

    return cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)


def _run_pipeline(
    src: Artifact,
    out: Path,
    p: BlurFacesParams,
    *,
    width: int,
    height: int,
    fps: float,
    has_audio: bool,
) -> tuple[int, int]:
    """Decode `src` to raw BGR24, blur faces frame by frame, encode `out`. Returns
    (frames, frames_with_a_face)."""
    detector = _detector()
    detector.setScoreThreshold(p.min_score)
    detect_scale = min(1.0, DETECT_SIZE / max(width, height))
    dw, dh = max(1, round(width * detect_scale)), max(1, round(height * detect_scale))
    detector.setInputSize((dw, dh))
    scale_x, scale_y = width / dw, height / dh
    native_detect_size = dw == width and dh == height

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
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps}", "-i", "-",
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
    decode_err = work / "blur_decode.log"
    encode_err = work / "blur_encode.log"
    frame_size = width * height * 3
    deadline = time.monotonic() + TIMEOUT
    frames = faced = 0

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
                    raise GraphError("face blur timed out")
                buf = _read_exact(decode.stdout, frame_size)
                if buf is None:
                    break
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3).copy()
                small = frame if native_detect_size else _cv2_resize(frame, dw, dh)
                boxes = _boxes(detector, small, scale_x, scale_y, p.expand, width, height)
                for box in boxes:
                    _treat(frame, box, p.style)
                frames += 1
                faced += int(bool(boxes))
                encode.stdin.write(frame.tobytes())
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
            raise GraphError("face blur timed out") from None

    fake_source = Source(src.path, MediaType.VIDEO, src.ext, {})
    if decode.returncode != 0:
        raise GraphError(clean_error(decode_err.read_bytes(), work, out, [fake_source]))
    if encode.returncode != 0:
        raise GraphError(clean_error(encode_err.read_bytes(), work, out, [fake_source]))
    return frames, faced


@op(
    name="video.blur_faces",
    version="1.0.0",
    inputs=(MediaType.VIDEO,),
    output=MediaType.VIDEO,
    determinism=Determinism.SEEDED_ML,
    params=BlurFacesParams,
    summary=av_summary,
    description="cover every detected face in a video",
    resources=("ffmpeg", "ffprobe", "ml_runtime", "model_yunet"),
)
def blur_faces(ctx: OpContext, inputs: Sequence[Artifact], p: BlurFacesParams) -> Artifact:
    """Find faces with a small neural network and cover them, frame by frame, for the whole
    clip: nobody is tracked by identity, each frame is judged on its own, so a face turned away,
    in deep shadow or at a sharp angle on a given frame may slip through on that frame even if
    caught on its neighbors. `min_score` trades false negatives for false positives; `preview`
    the result before trusting it for something sensitive. Clips up to 180s (every frame is
    decoded and run through the detector, so this is slower than a filter-based op)."""
    src = inputs[0]
    meta = src.meta if "video" in src.meta else probe_av(src.path, src.ext)
    v = meta.get("video")
    if not v:
        raise OpError("this file has no video stream to blur faces in")
    duration = float(meta.get("duration") or 0)
    if duration > FACE_MAX_SECONDS:
        raise OpError(f"face blur is limited to {FACE_MAX_SECONDS:g}s clips: trim it first")
    fps = float(v.get("fps") or 30.0)
    dw, dh = v["display"]
    sar = _parse_sar(v.get("sar"))
    width = max(2, round(dw * sar / 2) * 2) if abs(sar - 1.0) > 0.001 else dw
    height = dh
    has_audio = bool(meta.get("audio"))

    out = ctx.output_path(".mp4")
    frames, faced = _run_pipeline(
        src, out, p, width=width, height=height, fps=fps, has_audio=has_audio
    )
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
        "faces": {"frames": frames, "frames_with_a_face": faced},
    }
    return Artifact(out, MediaType.VIDEO, ".mp4", result_meta)
