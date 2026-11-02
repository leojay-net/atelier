"""Turning speech into captions with Whisper (tiny), through faster-whisper."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from typing import Any

import numpy as np
from pydantic import Field, field_validator

from atelier import models
from atelier.av.binaries import PROTOCOLS, find, run
from atelier.av.probe import DEMUXERS, probe_av
from atelier.media import MediaType
from atelier.ops.registry import (
    AnyOf,
    Artifact,
    Determinism,
    OpContext,
    OpError,
    Params,
    op,
)

RATE = 16000
MAX_MINUTES = 30
GAP = 0.8  # a silence this long ends a caption
_LANG = re.compile(r"^[a-z]{2,3}$")


class TranscribeParams(Params):
    language: str | None = Field(
        None, description="spoken language as a two-letter code (en, fr…); omit to detect it"
    )
    max_chars: int = Field(42, ge=16, le=100, description="most characters in one caption")
    max_seconds: float = Field(6.0, ge=1, le=15, description="longest a caption stays up")

    @field_validator("language")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is not None and not _LANG.match(v):
            raise ValueError(f"{v!r} is not a language code like en or fr")
        return v


def _audio(src: Artifact) -> np.ndarray:
    """The speech track as 16 kHz mono float samples (one decode, deterministic)."""
    meta = src.meta if "audio" in src.meta or "video" in src.meta else probe_av(src.path, src.ext)
    if not meta.get("audio"):
        raise OpError("this file has no audio track to transcribe")
    if float(meta.get("duration") or 0) > MAX_MINUTES * 60:
        raise OpError(f"the recording is longer than {MAX_MINUTES} minutes: trim it first")
    argv = [
        find("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        PROTOCOLS,
    ]
    if demuxer := DEMUXERS.get(src.ext):
        argv += ["-f", demuxer]
    argv += ["-i", str(src.path), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    try:
        done = run(argv, timeout=300)
    except subprocess.TimeoutExpired:
        raise OpError("reading the audio timed out") from None
    if done.returncode != 0:
        raise OpError("the audio could not be decoded")
    return np.frombuffer(done.stdout, dtype=np.float32)


def _model() -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise OpError(
            "transcription needs the ML runtime: install it with `pip install atelier[ml]`"
        ) from None
    try:
        directory = models.require("whisper-tiny")
    except models.ModelError as e:
        raise OpError(str(e)) from None
    return WhisperModel(
        str(directory), device="cpu", compute_type="int8", cpu_threads=1, num_workers=1,
        local_files_only=True,
    )  # fmt: skip


def stamp(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_from_words(
    words: Sequence[tuple[str, float, float]], max_chars: int, max_seconds: float
) -> list[tuple[float, float, str]]:
    """Group timed words into captions: a new one at a long silence, a full line or a long time."""
    cues: list[tuple[float, float, str]] = []
    line: list[str] = []
    start = end = 0.0
    for word, w_start, w_end in words:
        text = word.strip()
        if not text:
            continue
        joined = " ".join([*line, text])
        if line and (w_start - end > GAP or len(joined) > max_chars or w_end - start > max_seconds):
            cues.append((start, end, " ".join(line)))
            line = []
        if not line:
            start = w_start
        line.append(text)
        end = w_end
    if line:
        cues.append((start, end, " ".join(line)))
    return cues


def to_srt(cues: Sequence[tuple[float, float, str]]) -> str:
    return "\n".join(
        f"{i}\n{stamp(a)} --> {stamp(max(b, a + 0.2))}\n{text}\n"
        for i, (a, b, text) in enumerate(cues, 1)
    )


def transcribe_summary(result: Artifact) -> str:
    m = result.meta
    return f"captions {m['cues']} cues · language {m['language']} · {m['lines']} lines"


@op(
    name="transcribe",
    version="1.0.0",
    inputs=[AnyOf(frozenset({MediaType.VIDEO, MediaType.AUDIO}))],
    output=MediaType.TEXT,
    determinism=Determinism.SEEDED_ML,
    params=TranscribeParams,
    summary=transcribe_summary,
    description="write captions (SRT) from the speech in a video or audio file",
    resources=("ffmpeg", "ffprobe", "ml_runtime", "model_whisper_tiny"),
)
def transcribe(ctx: OpContext, inputs: Sequence[Artifact], p: TranscribeParams) -> Artifact:
    """Listen to the speech and write a caption file: pass it to `video.captions` to burn it in.
    Uses a small model, so it is fast but makes mistakes with names, accents and noise: read the
    result (`inspect_media` on it) before burning it in. Files up to 30 minutes."""
    audio = _audio(inputs[0])
    if len(audio) < RATE // 5:
        raise OpError("there is no speech in this file")
    model = _model()
    segments, info = model.transcribe(
        audio,
        language=p.language,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        word_timestamps=True,
        vad_filter=False,
    )
    words = [(w.word, float(w.start), float(w.end)) for seg in segments for w in (seg.words or [])]
    cues = cues_from_words(words, p.max_chars, p.max_seconds)
    if not cues:
        raise OpError("no speech was recognized in this file")
    text = to_srt(cues)
    path = ctx.output_path(".srt")
    path.write_text(text, encoding="utf-8")
    lines = len(text.splitlines())
    return Artifact(
        path,
        MediaType.TEXT,
        ".srt",
        {
            "lines": lines,
            "chars": len(text),
            "truncated": False,
            "cues": len(cues),
            "language": str(info.language),
            "seconds": round(len(audio) / RATE, 2),
        },
    )
