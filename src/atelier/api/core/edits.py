"""Hand edits (the editor, no prompt): what the user set, turned into an ordinary engine spec.

The editor works on the source video's own clock: the parts to keep, and where text, the
voiceover and sounds go. Here those times are moved onto the edited video's clock (cut parts
removed, speed applied), and the steps are the engine's own ops, so a hand edit is rendered,
cached and versioned like any other result, without the model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHAPES = {"portrait": "9:16", "square": "1:1", "landscape": "16:9"}
MIN_PART = 0.2  # seconds: shorter kept parts are dropped


ANCHORS = (
    "top_left", "top_center", "top_right", "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
)  # fmt: skip
TRANSITIONS = ("fade", "dissolve", "wipeleft", "wiperight", "slideleft", "slideright",
               "circlecrop", "fadeblack", "fadewhite")  # fmt: skip
TRANSITION_SECONDS = 0.5


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speed: float = Field(1.0, ge=0.25, le=4)  # this part only (on top of the whole-video speed)
    reverse: bool = False  # play this part backwards
    hold: float = Field(0.0, ge=0, le=10)  # freeze its last frame for this many seconds


class Adjust(BaseModel):
    """The look of the picture (filters are just named sets of these four numbers)."""

    model_config = ConfigDict(extra="forbid")
    brightness: float = Field(0.0, ge=-1, le=1)
    contrast: float = Field(1.0, ge=0, le=3)
    saturation: float = Field(1.0, ge=0, le=3)
    gamma: float = Field(1.0, ge=0.1, le=3)

    @property
    def neutral(self) -> bool:
        return (self.brightness, self.contrast, self.saturation, self.gamma) == (0, 1, 1, 1)


class CaptionsItem(BaseModel):
    """Captions written from the speech in the edited video, burned in."""

    model_config = ConfigDict(extra="forbid")
    style: Literal["clean", "bold_pop", "minimal_lower"] = "bold_pop"
    position: Literal["auto", "top", "upper_safe", "middle", "lower_safe", "bottom"] = "auto"


class OverlayItem(BaseModel):
    """A picture (sticker, logo, photo) laid over the video for a while."""

    model_config = ConfigDict(extra="forbid")
    handle: str
    anchor: Literal[*ANCHORS] = "top_right"
    scale: float = Field(0.2, gt=0, le=1)  # width as a fraction of the video's
    opacity: float = Field(1.0, gt=0, le=1)
    start: float = Field(0.0, ge=0)
    end: float | None = Field(None, gt=0)
    move_to: Literal[*ANCHORS] | None = None  # glide to this spot by the end (needs `end`)


class AppendItem(BaseModel):
    """Another video (or part of one) played after the edited one."""

    model_config = ConfigDict(extra="forbid")
    handle: str
    start: float = Field(0.0, ge=0)
    end: float | None = Field(None, gt=0)


class TextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    start: float = Field(0.0, ge=0)
    end: float | None = Field(None, gt=0)
    position: Literal["top", "upper_safe", "middle", "lower_safe", "bottom"] = "lower_safe"
    style: Literal["clean", "bold_pop", "minimal_lower"] = "bold_pop"
    size: float | None = Field(None, gt=0, le=0.3)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
    animate: Literal["auto", "none", "rise", "pop"] = "auto"


class SoundItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: float = Field(ge=0)
    kind: Literal["whoosh", "pop", "riser", "impact"] = "whoosh"


class VoiceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str = ""  # a recorded or uploaded clip, or the voiceover made from `text`
    text: str = Field("", max_length=4000)
    voice: str = ""
    start: float = Field(0.0, ge=0)
    volume: float = Field(1.0, ge=0, le=3)


class MusicItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str = ""  # an uploaded song, or the music composed from `compose`
    compose: str = Field("", max_length=500)
    volume: float = Field(0.35, ge=0, le=3)
    loop: bool = True


class EditIn(BaseModel):
    """Everything the editor can set. Times are on the source video's clock."""

    model_config = ConfigDict(extra="forbid")
    source: str
    parts: list[Part] = Field(default_factory=list, max_length=50)  # kept; empty = all of it
    speed: float = Field(1.0, ge=0.25, le=4)
    shape: Literal["original", "portrait", "square", "landscape"] = "original"
    focus_x: float = Field(0.5, ge=0, le=1)
    focus_y: float = Field(0.5, ge=0, le=1)
    volume: float = Field(1.0, ge=0, le=3)  # the video's own sound
    texts: list[TextItem] = Field(default_factory=list, max_length=30)
    voiceover: VoiceItem | None = None
    music: MusicItem | None = None
    sounds: list[SoundItem] = Field(default_factory=list, max_length=80)
    adjust: Adjust = Field(default_factory=Adjust)  # filters and colour
    fade_in: float = Field(0.0, ge=0, le=5)  # from black (and silence), seconds
    fade_out: float = Field(0.0, ge=0, le=5)
    transition: Literal[*TRANSITIONS] | None = None  # between cut parts
    captions: CaptionsItem | None = None
    overlays: list[OverlayItem] = Field(default_factory=list, max_length=20)
    append: list[AppendItem] = Field(default_factory=list, max_length=8)
    denoise: bool = False  # reduce noise in the video's own sound
    loudnorm: bool = False  # even out the final loudness (-14 LUFS)
    stabilize: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> EditIn:
        for p in self.parts:
            if p.end <= p.start:
                raise ValueError(f"a kept part ends ({p.end:g}s) before it starts ({p.start:g}s)")
        return self


class Clock:
    """Source time → edited time: cut parts removed, each part's own speed, the crossfades
    between parts, then the whole-video speed."""

    def __init__(
        self,
        parts: list[tuple[float, float]],
        speed: float,
        speeds: list[float] | None = None,
        overlap: float = 0.0,
        reverses: list[bool] | None = None,
        holds: list[float] | None = None,
    ) -> None:
        self.parts, self.speed = parts, speed
        self.reverses = reverses or [False] * len(parts)
        self.holds = holds or [0.0] * len(parts)
        self.speeds = speeds or [1.0] * len(parts)
        self.overlap = overlap  # seconds two neighbouring parts share in a crossfade

    @property
    def length(self) -> float:
        total = sum((b - a) / sp for (a, b), sp in zip(self.parts, self.speeds, strict=True))
        total += sum(self.holds)
        return (total - self.overlap * (len(self.parts) - 1)) / self.speed

    def __call__(self, t: float) -> float | None:
        """Where `t` lands in the edited video; a time inside a cut moves to the next kept
        part; None after the last one."""
        before = 0.0
        for i, ((a, b), sp) in enumerate(zip(self.parts, self.speeds, strict=True)):
            if t < b:
                inside = max(0.0, t - a)
                if self.reverses[i]:
                    inside = max(0.0, b - max(t, a))
                return (before + inside / sp - self.overlap * i) / self.speed
            before += (b - a) / sp + self.holds[i]
        return None


def part_speeds(edit: EditIn, parts: list[tuple[float, float]]) -> list[float]:
    """The speed of each kept part: that of the (first) source part it came from."""
    out = []
    for a, _ in parts:
        owner = next((p for p in edit.parts if p.start <= a + 1e-6 < p.end), None)
        out.append(owner.speed if owner else 1.0)
    return out


def part_flags(edit: EditIn, parts: list[tuple[float, float]]) -> tuple[list[bool], list[float]]:
    """Which kept parts play backwards, and how long each holds its last frame."""
    rev, hold = [], []
    for a, _ in parts:
        owner = next((p for p in edit.parts if p.start <= a + 1e-6 < p.end), None)
        rev.append(bool(owner and owner.reverse))
        hold.append(owner.hold if owner else 0.0)
    return rev, hold


def make_clock(edit: EditIn, parts: list[tuple[float, float]]) -> Clock:
    speeds = part_speeds(edit, parts)
    rev, hold = part_flags(edit, parts)
    return Clock(parts, edit.speed, speeds, overlap_for(edit, parts, speeds), rev, hold)


def overlap_for(edit: EditIn, parts: list[tuple[float, float]], speeds: list[float]) -> float:
    """The crossfade between parts, or 0 when there is none or a part is too short for it."""
    if not edit.transition or len(parts) < 2:
        return 0.0
    if any((b - a) / sp < 2 * TRANSITION_SECONDS + 0.1 for (a, b), sp in zip(parts, speeds, strict=True)):
        return 0.0
    return TRANSITION_SECONDS


def kept_parts(edit: EditIn, duration: float) -> list[tuple[float, float]]:
    """The parts to keep, in order, clipped to the video, short scraps dropped, overlaps
    merged."""
    raw = sorted(
        (p.start, min(p.end, duration), (p.speed, p.reverse, p.hold)) for p in edit.parts
    ) or [(0.0, duration, (1.0, False, 0.0))]
    merged: list[tuple[float, float]] = []
    kinds: list[tuple[float, bool, float]] = []
    for a, b, kind in raw:
        if b - a < MIN_PART:
            continue
        # neighbours that touch or overlap are one part, unless they play differently
        if merged and a <= merged[-1][1] and kind == kinds[-1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            if merged and a < merged[-1][1]:
                a = merged[-1][1]  # no overlap between two different parts
                if b - a < MIN_PART:
                    continue
            merged.append((a, b))
            kinds.append(kind)
    if not merged:
        raise ValueError("nothing is left to keep: keep at least a fraction of a second")
    return merged


def describe(edit: EditIn, parts: list[tuple[float, float]], duration: float) -> str:
    """What was changed, in plain words, for the chat ("Edited by hand: …")."""
    done = []
    if parts != [(0.0, duration)]:
        done.append("trimmed" if len(parts) == 1 else f"cut into {len(parts)} parts")
    if edit.speed != 1:
        done.append(f"speed {edit.speed:g}x")
    if any(p.speed != 1 for p in edit.parts):
        done.append("a part at its own speed")
    if any(p.reverse for p in edit.parts):
        done.append("a part played backwards")
    if any(p.hold for p in edit.parts):
        done.append("a frozen moment")
    if edit.shape != "original":
        done.append(f"{edit.shape} shape")
    if edit.volume != 1:
        done.append(f"sound {round(edit.volume * 100)}%")
    if edit.texts:
        done.append(f"{len(edit.texts)} text{'s' if len(edit.texts) > 1 else ''}")
    if edit.voiceover:
        done.append("a voiceover")
    if edit.music:
        done.append("music")
    if edit.sounds:
        done.append(f"{len(edit.sounds)} sound effect{'s' if len(edit.sounds) > 1 else ''}")
    if not edit.adjust.neutral:
        done.append("a new look")
    if edit.captions:
        done.append("captions")
    if edit.overlays:
        done.append(f"{len(edit.overlays)} sticker{'s' if len(edit.overlays) > 1 else ''}")
    if edit.fade_in or edit.fade_out:
        done.append("fades")
    if edit.transition:
        done.append(f"{edit.transition} transitions")
    if edit.denoise:
        done.append("less noise")
    if edit.loudnorm:
        done.append("even loudness")
    if edit.stabilize:
        done.append("steadied")
    if edit.append:
        done.append(f"{len(edit.append)} more clip{'s' if len(edit.append) > 1 else ''} added")
    return "Edited by hand: " + (", ".join(done) or "no changes")


def build_spec(
    edit: EditIn,
    duration: float,
    voice_handle: str | None = None,
    music_handle: str | None = None,
    has_audio: bool = True,
    overlay_handles: list[str] | None = None,
    append_handles: list[str] | None = None,
) -> dict[str, Any]:
    """The engine spec for an edit of a video `duration` seconds long."""
    parts = kept_parts(edit, duration)
    speeds = part_speeds(edit, parts)
    overlap = overlap_for(edit, parts, speeds)
    reverses, holds = part_flags(edit, parts)
    clock = Clock(parts, edit.speed, speeds, overlap, reverses, holds)
    inputs: dict[str, str] = {"v": edit.source}
    steps: list[dict[str, Any]] = []
    last = "v"

    def add(op: str, args: dict[str, Any], extra: list[str] | None = None) -> str:
        nonlocal last
        sid = f"s{len(steps)}"
        steps.append({"id": sid, "op": op, "in": [last, *(extra or [])], "args": args})
        last = sid
        return sid

    def through_audio(*ops: tuple[str, dict[str, Any]]) -> None:
        """Pull the sound out of the video, run it through `ops`, and put it back."""
        nonlocal last
        video = last
        prev = video
        for op, args in ops:
            sid = f"a{len(steps)}"
            steps.append({"id": sid, "op": op, "in": [prev], "args": args})
            prev = sid
        sid = f"s{len(steps)}"
        steps.append({"id": sid, "op": "video.soundtrack", "in": [video, prev],
                      "args": {"mode": "replace", "fade_out": 0}})  # fmt: skip
        last = sid

    whole = (
        len(parts) == 1 and parts[0] == (0.0, duration) and speeds == [1.0]
        and not reverses[0] and not holds[0]
    )  # fmt: skip
    if not whole:
        if len(parts) == 1:
            add("video.trim", {"start": round(parts[0][0], 3), "end": round(parts[0][1], 3)})
            if speeds[0] != 1:
                add("video.speed", {"factor": speeds[0]})
            if reverses[0]:
                add("video.reverse", {})
            if holds[0]:
                add("video.freeze", {"at": round(max(0.0, (parts[0][1] - parts[0][0]) / speeds[0] - 0.1), 3),
                                     "seconds": holds[0]})  # fmt: skip
        else:
            pieces = []
            for i, ((a, b), sp) in enumerate(zip(parts, speeds, strict=True)):
                steps.append(
                    {"id": f"p{i}", "op": "video.trim", "in": "v",
                     "args": {"start": round(a, 3), "end": round(b, 3)}}
                )  # fmt: skip
                piece = f"p{i}"
                if sp != 1:
                    steps.append({"id": f"q{i}", "op": "video.speed", "in": piece,
                                  "args": {"factor": sp}})  # fmt: skip
                    piece = f"q{i}"
                if reverses[i]:
                    steps.append({"id": f"r{i}", "op": "video.reverse", "in": piece, "args": {}})
                    piece = f"r{i}"
                if holds[i]:
                    at = round(max(0.0, (b - a) / sp - 0.1), 3)
                    steps.append({"id": f"h{i}", "op": "video.freeze", "in": piece,
                                  "args": {"at": at, "seconds": holds[i]}})  # fmt: skip
                    piece = f"h{i}"
                pieces.append(piece)
            join: dict[str, Any] = {}
            if overlap:
                join = {"fade": overlap, "transition": edit.transition}
            steps.append({"id": "joined", "op": "video.concat", "in": pieces, "args": join})
            last = "joined"
    if edit.speed != 1:
        add("video.speed", {"factor": edit.speed})
    if edit.stabilize:
        add("video.stabilize", {})
    if edit.shape != "original":
        add(
            "video.reframe",
            {"aspect": SHAPES[edit.shape], "mode": "fill",
             "focus_x": edit.focus_x, "focus_y": edit.focus_y},
        )  # fmt: skip
    if not edit.adjust.neutral:
        add("video.color", edit.adjust.model_dump())
    if edit.append and append_handles:
        pieces = [last]
        for i, item in enumerate(edit.append[: len(append_handles)]):
            inputs[f"ap{i}"] = append_handles[i]
            piece = f"ap{i}"
            if item.start > 0 or item.end is not None:
                trim: dict[str, Any] = {"start": round(item.start, 3)}
                if item.end is not None:
                    trim["end"] = round(item.end, 3)
                steps.append({"id": f"at{i}", "op": "video.trim", "in": piece, "args": trim})
                piece = f"at{i}"
            pieces.append(piece)
        steps.append({"id": "appended", "op": "video.concat", "in": pieces, "args": {"mode": "fill"}})
        last = "appended"
    if edit.captions:
        # written from the speech of the video as it is now, so cuts and speed are already in it
        steps.append({"id": "heard", "op": "transcribe", "in": [last], "args": {}})
        cap: dict[str, Any] = {"style": edit.captions.style}
        if edit.captions.position != "auto":
            cap["position"] = edit.captions.position
        add("video.captions", cap, ["heard"])
    for i, ov in enumerate(edit.overlays):
        if i >= len(overlay_handles or []):
            break
        start = clock(ov.start)
        if start is None:
            continue
        inputs[f"ov{i}"] = (overlay_handles or [])[i]
        oargs: dict[str, Any] = {"anchor": ov.anchor, "scale": ov.scale,
                                 "opacity": ov.opacity, "start": round(start, 3)}  # fmt: skip
        if ov.end is not None and (end := clock(ov.end)) is not None and end > start:
            oargs["end"] = round(end, 3)
            if ov.move_to:
                oargs["move_to"] = ov.move_to
        add("video.overlay", oargs, [f"ov{i}"])
    for item in edit.texts:
        start = clock(item.start)
        if start is None:
            continue
        args: dict[str, Any] = {
            "text": item.text, "start": round(start, 3), "position": item.position,
            "style": item.style,
        }  # fmt: skip
        if item.end is not None and (end := clock(item.end)) is not None and end > start:
            args["end"] = round(end, 3)
        if item.size:
            args["size"] = item.size
        if item.color:
            args["color"] = item.color
        if item.animate != "auto":
            args["animate"] = item.animate
        add("video.text", args)
    if edit.denoise and has_audio:
        through_audio(("audio.extract", {}), ("audio.denoise", {}))
    layers, extra = [], []
    if voice_handle and edit.voiceover:
        inputs["vo"] = voice_handle
        layers.append({"kind": "voice", "start": round(clock(edit.voiceover.start) or 0.0, 3),
                       "volume": edit.voiceover.volume})  # fmt: skip
        extra.append("vo")
    if music_handle and edit.music:
        inputs["mu"] = music_handle
        layers.append({"kind": "music", "start": 0.0, "volume": edit.music.volume,
                       "loop": edit.music.loop})  # fmt: skip
        extra.append("mu")
    if layers or edit.volume != 1:
        add("video.layers", {"original": edit.volume, "layers": layers}, extra)
    hits = [
        {"at": round(t, 3), "kind": s.kind} for s in edit.sounds if (t := clock(s.at)) is not None
    ]
    if hits:
        add("video.sfx", {"hits": hits, "volume": 0.5})
    if edit.loudnorm and (has_audio or layers or hits):
        through_audio(("audio.extract", {}), ("audio.loudnorm", {"target_lufs": -14.0}))
    if edit.fade_in or edit.fade_out:
        add("video.fade", {"fade_in": edit.fade_in, "fade_out": edit.fade_out})
    add("export", {"preset": "web_h264"})
    steps[-1]["id"] = "out"
    return {"version": 1, "inputs": inputs, "steps": steps, "outputs": ["out"]}


def split_spec(source: str) -> dict[str, Any]:
    """Two results from one video: the picture alone (no sound) and its sound alone, so each can
    be muted, replaced or taken out by itself in the editor."""
    return {
        "version": 1,
        "inputs": {"v": source},
        "steps": [
            {"id": "m", "op": "video.mute", "in": ["v"], "args": {}},
            {"id": "picture", "op": "export", "in": ["m"], "args": {"preset": "web_h264"}},
            {"id": "a", "op": "audio.extract", "in": ["v"], "args": {}},
            {"id": "sound", "op": "export", "in": ["a"], "args": {"preset": "podcast_mp3"}},
        ],
        "outputs": ["picture", "sound"],
    }
