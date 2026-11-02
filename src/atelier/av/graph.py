"""A typed ffmpeg filtergraph builder (ADR-0004, docs/03 §3.7).

Ops do not run ffmpeg. Each contributes filters to one `Graph`, and the graph is compiled into
a single ffmpeg invocation with one decode and one encode, so a chain like
trim → reframe → overlay → export is encoded once instead of four times.

Safety rules the rest of the package relies on:
* the command is an argv list, never a shell string;
* filter argument values are escaped by `esc`, the only place escaping happens;
* user text and file names never appear in a filter string. Anything file-like (fonts, text,
  overlay images) is written by us into the work directory under a name we generate, and the
  filter refers to that bare name;
* inputs are opened with a forced demuxer and `-protocol_whitelist file` only;
* threads are pinned so the same graph encodes to the same bytes on the same platform.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atelier.av.binaries import PROTOCOLS, find, run
from atelier.av.probe import DEMUXERS
from atelier.media import MediaType
from atelier.ops.registry import OpError

DEFAULT_TIMEOUT = 3600.0
_FFMPEG_MEDIA = (MediaType.VIDEO, MediaType.AUDIO, MediaType.IMAGE)
IMAGE_DEMUXER = "image2"
_SPECIAL = re.compile(r"([\\':,;\[\]=])")
_MAX_DIM = 16384


class GraphError(OpError):
    """An ffmpeg failure, phrased for the user; never contains internal paths."""


def esc(value: str) -> str:
    """Escape a filter argument value (or expression) for use inside `-filter_complex`."""
    return _SPECIAL.sub(r"\\\1", value)


def num(x: float) -> str:
    """A compact decimal with no exponent, for filter arguments and timestamps."""
    text = f"{x:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _arg(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return num(float(value)) if isinstance(value, float) else str(value)
    return esc(str(value))


@dataclass
class Source:
    """One input to the graph: an artifact on disk plus what is known about it."""

    path: Path
    media: MediaType
    ext: str
    meta: dict[str, Any] = field(default_factory=dict)
    opts: tuple[str, ...] = ()  # input options replacing the forced demuxer (raw PCM)


@dataclass(frozen=True)
class Node:
    """One filter in the graph, with the labels it reads and writes (so a sub-graph can be cut)."""

    text: str
    ins: tuple[str, ...]
    outs: tuple[str, ...]
    kinds: str = ""  # "v" or "a" per output; empty means judged by the label's first letter


@dataclass
class Encode:
    """How the graph's output is written. Set by `export` (or the default intermediate)."""

    ext: str
    args: list[str]
    info: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Stream:
    """A snapshot of the main streams: labels plus what is known about the picture and sound.

    An op that joins several inputs (concat) receives one per input, whether that input is a
    file or the output of an earlier step in the same fused graph."""

    video: str | None
    audio: str | None
    width: int
    height: int
    fps: float
    duration: float | None
    sample_rate: int
    channels: int
    origin: int | None = None  # the source it was opened from, while no filter has touched it

    @property
    def has_audio(self) -> bool:
        return self.audio is not None


class Graph:
    """The filtergraph under construction, plus the state of the main stream flowing through it.

    `video`/`audio` are the labels of the current main streams (None if absent). `width`,
    `height`, `fps` and `duration` describe the *current* video, so an op can compute from what
    the previous ops produced (a crop after a trim after a speed-up sees the right numbers).
    """

    def __init__(self, sources: Sequence[Source], work: Path) -> None:
        self.sources = list(sources)
        self.work = work
        self.nodes: list[Node] = []
        self._counter = 0
        self.video: str | None = None
        self.audio: str | None = None
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self.duration: float | None = None
        self.sample_rate = 0
        self.channels = 0
        self.origin: int | None = None
        self.encode: Encode | None = None
        self.notes: dict[str, Any] = {}  # things ops want recorded in the output's metadata
        self._extras: list[int | Stream] = []

    # -- state ------------------------------------------------------------------------------

    def add_source(self, source: Source) -> int:
        """Add an input made while building (a rendered text layer). Returns its source index."""
        self.sources.append(source)
        return len(self.sources) - 1

    def ff(self, index: int) -> int:
        """ffmpeg's input number for source `index`: only audio, video and image sources are
        opened by ffmpeg (a subtitle file is read by us, not passed to it)."""
        return sum(1 for s in self.sources[:index] if s.media in _FFMPEG_MEDIA)

    def label(self, prefix: str = "t") -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    def start(self, index: int) -> None:
        """Begin the main streams from source `index` (the first step of a fused chain)."""
        src = self.sources[index]
        v, a = src.meta.get("video"), src.meta.get("audio")
        self.video = self.audio = None
        self.origin = index
        if src.media is MediaType.IMAGE:  # a still picture: one frame, no length of its own
            self.video = f"{self.ff(index)}:v:0"
            self.width, self.height = src.meta["size"]
            self.fps, self.duration = 0.0, None
            self.sample_rate = self.channels = 0
            return
        if src.media is MediaType.VIDEO and v:
            self.video = f"{self.ff(index)}:v:0"
            self.width, self.height = v["display"]
            self.fps = float(v.get("fps") or 30.0)
            sar = _ratio(v.get("sar"))
            if abs(sar - 1.0) > 0.001:  # non-square pixels: make them square first
                self.width = max(2, round(self.width * sar / 2) * 2)
                self.video = self.vf("scale", {"w": self.width, "h": self.height})
                self.video = self.vf("setsar", {"sar": 1})
        if src.media in (MediaType.VIDEO, MediaType.AUDIO) and a:
            self.audio = f"{self.ff(index)}:a:0"
            self.sample_rate = int(a.get("sample_rate") or 48000)
            self.channels = int(a.get("channels") or 2)
        self.duration = src.meta.get("duration")

    def snapshot(self) -> Stream:
        return Stream(
            self.video, self.audio, self.width, self.height, self.fps, self.duration,
            self.sample_rate, self.channels, self.origin,
        )  # fmt: skip

    def restore(self, s: Stream) -> None:
        """Make `s` the main streams again (a step whose input is an earlier step's output)."""
        self.video, self.audio = s.video, s.audio
        self.width, self.height, self.fps, self.duration = s.width, s.height, s.fps, s.duration
        self.sample_rate, self.channels, self.origin = s.sample_rate, s.channels, s.origin

    def open_source(self, index: int) -> Stream:
        """The streams of source `index`, without making them the main ones."""
        saved = self.snapshot()
        self.start(index)
        opened = self.snapshot()
        self.restore(saved)
        return opened

    def set_extras(self, items: Sequence[int | Stream]) -> None:
        """The current step's additional inputs: source indices (overlay images…) or the
        streams of earlier steps fused into this graph."""
        self._extras = list(items)

    @property
    def extra_count(self) -> int:
        return len(self._extras)

    def extra(self, k: int) -> Source:
        item = self._extras[k]
        assert isinstance(item, int), "this input is the output of a fused step, not a file"
        return self.sources[item]

    def extra_stream(self, k: int) -> Stream:
        item = self._extras[k]
        return item if isinstance(item, Stream) else self.open_source(item)

    def extra_label(self, k: int) -> str:
        item = self._extras[k]
        if isinstance(item, Stream):
            assert item.video is not None, "no video stream"
            return item.video
        return f"{self.ff(item)}:v:0"

    def extra_audio_label(self, k: int) -> str:
        item = self._extras[k]
        if isinstance(item, Stream):
            assert item.audio is not None, "no audio stream"
            return item.audio
        return f"{self.ff(item)}:a:0"

    def source_audio_label(self, index: int) -> str:
        return f"{self.ff(index)}:a:0"

    def source_label(self, index: int) -> str:
        """The video stream label of any source (used for layers added while building)."""
        return f"{self.ff(index)}:v:0"

    # -- building ---------------------------------------------------------------------------

    def add(
        self,
        name: str,
        args: Mapping[str, Any] | None = None,
        *,
        ins: Sequence[str],
        outs: int = 1,
        prefix: str = "t",
        kinds: str = "",
    ) -> list[str]:
        """Append one filter node reading `ins`, and return its output labels."""
        labels = [self.label(prefix) for _ in range(outs)]
        body = ":".join(f"{k}={_arg(v)}" for k, v in (args or {}).items())
        head = "".join(f"[{i}]" for i in ins)
        tail = "".join(f"[{o}]" for o in labels)
        text = f"{head}{name}{'=' + body if body else ''}{tail}"
        self.nodes.append(Node(text, tuple(ins), tuple(labels), kinds))
        return labels

    def vf(self, name: str, args: Mapping[str, Any] | None = None) -> str:
        """Apply a filter to the main video and make its output the new main video."""
        assert self.video is not None, "no video stream"
        (self.video,) = self.add(name, args, ins=[self.video], prefix="v")
        self.origin = None
        return self.video

    def af(self, name: str, args: Mapping[str, Any] | None = None) -> str:
        """Apply a filter to the main audio and make its output the new main audio."""
        assert self.audio is not None, "no audio stream"
        (self.audio,) = self.add(name, args, ins=[self.audio], prefix="a")
        return self.audio

    def write_file(self, name: str, data: bytes) -> str:
        """Put a file next to the run and return the bare name filters may refer to."""
        if not re.fullmatch(r"[A-Za-z0-9_]+\.[a-z0-9]+", name):
            raise ValueError(f"unsafe work file name {name!r}")
        (self.work / name).write_bytes(data)
        return name

    # -- compiling and running --------------------------------------------------------------

    def filtergraph(self) -> str:
        """Only the nodes the output actually depends on: an earlier step's filters for a stream
        that was later dropped (audio.extract after video steps) would leave unconnected outputs,
        which ffmpeg rejects."""
        labels = [x for x in (self.video, self.audio) if x is not None]
        needed = {id(n): n for label in labels for n in self._feeding(label)}
        kept = [n for n in self.nodes if id(n) in needed]
        return ";".join(self._compile(kept, set(labels)))

    @staticmethod
    def _compile(kept: list[Node], targets: set[str]) -> list[str]:
        """The kept nodes as filter text, with a sink on every output nobody reads. A `concat`
        makes picture and sound together: when only one of them is wanted (the loudness
        measurement, a video with its sound dropped) ffmpeg refuses the other's dangling end."""
        read = {i for n in kept for i in n.ins}
        text = [n.text for n in kept]
        for n in kept:
            kinds = n.kinds or "".join("a" if o.startswith("a") else "v" for o in n.outs)
            for out, kind in zip(n.outs, kinds, strict=True):
                if out not in read and out not in targets:
                    text.append(f"[{out}]{'anullsink' if kind == 'a' else 'nullsink'}")
        return text

    def _feeding(self, label: str) -> list[Node]:
        """The nodes (in graph order) that the stream `label` depends on, and nothing else."""
        needed = {label}
        keep: list[Node] = []
        for node in reversed(self.nodes):
            if needed & set(node.outs):
                keep.append(node)
                needed.update(node.ins)
        return list(reversed(keep))

    def _base(self, loglevel: str = "error") -> list[str]:
        argv = [
            find("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-loglevel", loglevel, "-y",
            "-xerror", "-filter_threads", "1", "-filter_complex_threads", "1",
        ]  # fmt: skip
        for s in self.sources:
            if s.media in _FFMPEG_MEDIA:
                forced = list(s.opts) or ["-f", demuxer_for(s)]
                argv += ["-protocol_whitelist", PROTOCOLS, *forced, "-i", str(s.path)]
        return argv

    def render_pcm(
        self, label: str, rate: int = 48000, *, timeout: float = DEFAULT_TIMEOUT
    ) -> bytes:
        """Decode the stream `label` (through the nodes that feed it) to mono float32 PCM.

        Used to analyze the signal an op will react to (the voice for ducking). It is a
        single-input decode, so its result does not depend on thread timing."""
        out = self.label("p")
        nodes = self._compile(self._feeding(label), {label})
        fmt = f"[{label}]aformat=sample_fmts=flt:sample_rates={rate}:channel_layouts=mono[{out}]"
        argv = [*self._base(), "-filter_complex", ";".join([*nodes, fmt]), "-map", f"[{out}]"]
        argv += ["-f", "f32le", "-"]
        try:
            done = run(argv, timeout=timeout, cwd=self.work)
        except subprocess.TimeoutExpired:
            raise GraphError("reading the audio timed out") from None
        if done.returncode != 0:
            raise GraphError(clean_error(done.stderr, self.work, self.work / "pcm", self.sources))
        return done.stdout

    def sample_frames(
        self, label: str, *, fps: float, width: int, timeout: float = DEFAULT_TIMEOUT
    ) -> tuple[list[bytes], int, int]:
        """Decode `label` (through the nodes that feed it) at a low, fixed frame rate, scaled to
        `width` px wide, as raw RGB24 frames. One pass, so the result reflects any edit already
        applied earlier in the same fused chain (a trim, a crop). Returns the frames plus the
        actual (width, height) they were scaled to.

        Used by ops that need to look at a few frames before finishing the graph (following a
        subject while reframing), not for anything that ends up in the output."""
        height = max(2, round(width * self.height / max(1, self.width)) // 2 * 2)
        out = self.label("samp")
        nodes = self._compile(self._feeding(label), {label})
        chain = f"[{label}]fps={num(fps)},scale=w={width}:h={height}:flags=bilinear[{out}]"
        argv = [*self._base(), "-filter_complex", ";".join([*nodes, chain])]
        argv += ["-map", f"[{out}]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        try:
            done = run(argv, timeout=timeout, cwd=self.work)
        except subprocess.TimeoutExpired:
            raise GraphError("sampling frames timed out") from None
        if done.returncode != 0:
            raise GraphError(
                clean_error(done.stderr, self.work, self.work / "samples", self.sources)
            )
        frame_bytes = width * height * 3
        count = len(done.stdout) // frame_bytes
        frames = [done.stdout[i * frame_bytes : (i + 1) * frame_bytes] for i in range(count)]
        return frames, width, height

    def measure_loudness(
        self,
        label: str,
        target: float,
        true_peak: float,
        lra: float,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, float] | None:
        """First pass of EBU R128 normalization: measure what the stream `label` will sound like.

        Runs only the nodes that feed `label`, into `loudnorm` in measuring mode, to nowhere.
        Returns None if the audio is silent (there is nothing to normalize).
        """
        out = self.label("m")
        nodes = self._compile(self._feeding(label), {label})
        want = f"I={_arg(target)}:TP={_arg(true_peak)}:LRA={_arg(lra)}"
        measure = f"[{label}]loudnorm={want}:print_format=json[{out}]"
        argv = [*self._base("info"), "-filter_complex", ";".join([*nodes, measure])]
        argv += ["-map", f"[{out}]", "-f", "null", "-"]
        try:
            done = run(argv, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise GraphError("measuring loudness timed out") from None
        if done.returncode != 0:
            raise GraphError(clean_error(done.stderr, self.work, self.work / "null", self.sources))
        return parse_loudnorm(done.stderr)

    def stabilize_detect(
        self,
        label: str,
        *,
        shakiness: int,
        accuracy: int,
        result: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """First pass of two-pass stabilization: analyze the motion of `label` and write the
        transforms file `vidstabtransform` (pass 2, added to the main graph) will read.

        Runs only the nodes that feed `label`, into `vidstabdetect`, to nowhere. `result` is a
        bare file name written inside the job's work directory (the pass-2 filter reads it back
        by the same name, since both run with that directory as their cwd)."""
        out = self.label("s")
        nodes = self._compile(self._feeding(label), {label})
        want = f"shakiness={shakiness}:accuracy={accuracy}:result={_arg(result)}"
        detect = f"[{label}]vidstabdetect={want}[{out}]"
        argv = [*self._base(), "-filter_complex", ";".join([*nodes, detect])]
        argv += ["-map", f"[{out}]", "-f", "null", "-"]
        try:
            done = run(argv, timeout=timeout, cwd=self.work)
        except subprocess.TimeoutExpired:
            raise GraphError("analyzing motion for stabilization timed out") from None
        if done.returncode != 0:
            raise GraphError(clean_error(done.stderr, self.work, self.work / result, self.sources))

    def argv(self, out: Path) -> list[str]:
        if self.encode is None:
            raise GraphError("internal error: the graph has no output settings")
        argv = self._base()
        maps: list[str] = []
        for label in (self.video, self.audio):
            if label is not None:
                # a raw input stream specifier ("0:a:0") is mapped as is; a filter output is [x]
                maps += ["-map", label if ":" in label else f"[{label}]"]
        if not maps:
            raise GraphError("nothing to encode: the graph has neither video nor audio")
        graph = self.filtergraph()
        if graph:
            argv += ["-filter_complex", graph]
        return [*argv, *maps, *self.encode.args, str(out)]

    def execute(self, out: Path, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        argv = self.argv(out)
        try:
            done = run(argv, timeout=timeout, cwd=self.work)
        except subprocess.TimeoutExpired:
            raise GraphError("ffmpeg timed out") from None
        if done.returncode != 0:
            raise GraphError(clean_error(done.stderr, self.work, out, self.sources))


_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.S)


def parse_loudnorm(stderr: bytes) -> dict[str, float] | None:
    """The measurement loudnorm prints as JSON at the end of its run (None if silent/unreadable)."""
    blocks = _JSON_BLOCK.findall(stderr.decode(errors="replace"))
    if not blocks:
        return None
    try:
        raw = json.loads(blocks[-1])
        values = {
            "input_i": float(raw["input_i"]),
            "input_tp": float(raw["input_tp"]),
            "input_lra": float(raw["input_lra"]),
            "input_thresh": float(raw["input_thresh"]),
            "target_offset": float(raw["target_offset"]),
        }
    except (ValueError, KeyError, TypeError):
        return None
    return values if all(math.isfinite(v) for v in values.values()) else None


def measure_file(path: Path, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, float] | None:
    """Integrated loudness, true peak and range of a finished file's audio (None if silent)."""
    argv = [
        find("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-loglevel",
        "info",
        "-i",
        str(path),
    ]
    argv += ["-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    try:
        done = run(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return parse_loudnorm(done.stderr) if done.returncode == 0 else None


def demuxer_for(source: Source) -> str:
    if source.media is MediaType.IMAGE:
        return IMAGE_DEMUXER
    return DEMUXERS.get(source.ext, "")


def _ratio(text: str | None) -> float:
    if not text or ":" not in text:
        return 1.0
    a, _, b = text.partition(":")
    try:
        return float(a) / float(b) if float(b) else 1.0
    except ValueError:
        return 1.0


def clean_error(stderr: bytes, work: Path, out: Path, sources: Sequence[Source]) -> str:
    """The last meaningful ffmpeg error line, with internal paths replaced by plain words."""
    text = stderr.decode(errors="replace")
    for path, word in [(out, "the output")] + [(s.path, "the input") for s in sources]:
        text = text.replace(str(path), word)
    text = text.replace(str(work) + "/", "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return f"ffmpeg failed: {lines[-1]}" if lines else "ffmpeg failed without a message"


def decode_check(path: Path, *, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """Decode the whole output to nowhere. None if it plays cleanly, else the first problem."""
    argv = [find("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path)]
    try:
        done = run([*argv, "-f", "null", "-"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return "decoding timed out"
    if done.returncode == 0 and not done.stderr.strip():
        return None
    lines = [
        ln
        for ln in done.stderr.decode(errors="replace").replace(str(path), "the output").splitlines()
        if ln.strip()
    ]
    return lines[0].strip() if lines else "decoding failed"
