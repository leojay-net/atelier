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


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class TextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    start: float = Field(0.0, ge=0)
    end: float | None = Field(None, gt=0)
    position: Literal["top", "upper_safe", "middle", "lower_safe", "bottom"] = "lower_safe"
    style: Literal["clean", "bold_pop", "minimal_lower"] = "bold_pop"
    size: float | None = Field(None, gt=0, le=0.3)


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

    @model_validator(mode="after")
    def _ordered(self) -> EditIn:
        for p in self.parts:
            if p.end <= p.start:
                raise ValueError(f"a kept part ends ({p.end:g}s) before it starts ({p.start:g}s)")
        return self


class Clock:
    """Source time → edited time: cut parts removed, then the speed applied."""

    def __init__(self, parts: list[tuple[float, float]], speed: float) -> None:
        self.parts, self.speed = parts, speed

    @property
    def length(self) -> float:
        return sum(b - a for a, b in self.parts) / self.speed

    def __call__(self, t: float) -> float | None:
        """Where `t` lands in the edited video; a time inside a cut moves to the next kept
        part; None after the last one."""
        before = 0.0
        for a, b in self.parts:
            if t < b:
                return (before + max(0.0, t - a)) / self.speed
            before += b - a
        return None


def kept_parts(edit: EditIn, duration: float) -> list[tuple[float, float]]:
    """The parts to keep, in order, clipped to the video, short scraps dropped, overlaps
    merged."""
    raw = sorted((p.start, min(p.end, duration)) for p in edit.parts) or [(0.0, duration)]
    merged: list[tuple[float, float]] = []
    for a, b in raw:
        if b - a < MIN_PART:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
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
    return "Edited by hand: " + (", ".join(done) or "no changes")


def build_spec(
    edit: EditIn,
    duration: float,
    voice_handle: str | None = None,
    music_handle: str | None = None,
) -> dict[str, Any]:
    """The engine spec for an edit of a video `duration` seconds long."""
    parts = kept_parts(edit, duration)
    clock = Clock(parts, edit.speed)
    inputs: dict[str, str] = {"v": edit.source}
    steps: list[dict[str, Any]] = []
    last = "v"

    def add(op: str, args: dict[str, Any], extra: list[str] | None = None) -> None:
        nonlocal last
        sid = f"s{len(steps)}"
        steps.append({"id": sid, "op": op, "in": [last, *(extra or [])], "args": args})
        last = sid

    if parts != [(0.0, duration)]:
        if len(parts) == 1:
            add("video.trim", {"start": round(parts[0][0], 3), "end": round(parts[0][1], 3)})
        else:
            pieces = []
            for i, (a, b) in enumerate(parts):
                steps.append(
                    {"id": f"p{i}", "op": "video.trim", "in": "v",
                     "args": {"start": round(a, 3), "end": round(b, 3)}}
                )  # fmt: skip
                pieces.append(f"p{i}")
            steps.append({"id": "joined", "op": "video.concat", "in": pieces, "args": {}})
            last = "joined"
    if edit.speed != 1:
        add("video.speed", {"factor": edit.speed})
    if edit.shape != "original":
        add(
            "video.reframe",
            {"aspect": SHAPES[edit.shape], "mode": "fill",
             "focus_x": edit.focus_x, "focus_y": edit.focus_y},
        )  # fmt: skip
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
        add("video.text", args)
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
    add("export", {"preset": "web_h264"})
    steps[-1]["id"] = "out"
    return {"version": 1, "inputs": inputs, "steps": steps, "outputs": ["out"]}
