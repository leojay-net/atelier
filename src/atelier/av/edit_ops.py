"""`video.layers`: the sound of a hand edit. The video's own sound at a chosen volume, with any
number of voice, music and sound clips placed on it, each at its own time and volume (the
editor's tracks). Music can loop to fill the video. The video keeps its length.

Hidden from the agent's catalogue (the agent has video.soundtrack and audio.mix); the editor
builds specs with it directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atelier.av.graph import Graph, num
from atelier.av.ops import video_op
from atelier.media import MediaType
from atelier.ops.registry import OpError, Params
from atelier.spec.types import Timecode

SAMPLE_RATE = 48000


class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: Timecode = Field(0.0, description="when the clip starts playing (seconds)")
    volume: float = Field(1.0, ge=0, le=3, description="1 = as recorded")
    loop: bool = Field(False, description="repeat it until the video ends (music)")
    kind: Literal["voice", "music", "sound"] = "sound"


class LayersParams(Params):
    original: float = Field(1.0, ge=0, le=3, description="the video's own sound; 0 mutes it")
    layers: list[Layer] = Field(default_factory=list, max_length=20)


def _check(p: LayersParams, n_inputs: int) -> str | None:
    if len(p.layers) != n_inputs - 1:
        return f"layers has {len(p.layers)} entries for {n_inputs - 1} sound clips"
    return None


@video_op(
    name="video.layers",
    version="1.0.0",
    params=LayersParams,
    inputs=(MediaType.VIDEO, MediaType.AUDIO),
    variadic=True,
    check_inputs=_check,
    hidden=True,
    description="the video's sound at a volume, with voice, music and sounds placed on it",
)
def layers(g: Graph, p: LayersParams) -> None:
    """Mix the clips (second input on) over the video's own sound at `original` volume, each
    from its `start` at its `volume`; `loop` repeats one until the end. The length is the
    video's; anything after it is cut."""
    total = g.duration
    if total is None:
        raise OpError("the video's length is not known here, so the sound cannot be laid out")
    fmt = {"sample_fmts": "fltp", "sample_rates": SAMPLE_RATE, "channel_layouts": "stereo"}
    if g.audio is None:
        (base,) = g.add(
            "anullsrc", {"r": SAMPLE_RATE, "cl": "stereo", "d": num(total)}, ins=[], prefix="a"
        )
    else:
        (base,) = g.add("aformat", fmt, ins=[g.audio], prefix="a")
        (base,) = g.add("volume", {"volume": num(p.original)}, ins=[base], prefix="a")
        (base,) = g.add("apad", {"whole_dur": num(total)}, ins=[base], prefix="a")
        (base,) = g.add("atrim", {"end": num(total)}, ins=[base], prefix="a")
    tracks = [base]
    wanted = [k for k, layer in enumerate(p.layers) if layer.start < total and layer.volume > 0]
    # the same file on two tracks (one song as voice and music) is one ffmpeg input: split it
    labels = {k: g.extra_audio_label(k) for k in wanted}
    copies: dict[str, list[str]] = {}
    for label in set(labels.values()):
        n = sum(1 for v in labels.values() if v == label)
        copies[label] = (
            g.add("asplit", {"outputs": n}, ins=[label], outs=n, prefix="a") if n > 1 else [label]
        )
    for k in wanted:
        layer = p.layers[k]
        (a,) = g.add("aformat", fmt, ins=[copies[labels[k]].pop()], prefix="a")
        if layer.loop:
            length = g.extra_stream(k).duration or total
            (a,) = g.add(
                "aloop", {"loop": -1, "size": int(length * SAMPLE_RATE) + 1}, ins=[a], prefix="a"
            )
        (a,) = g.add("atrim", {"end": num(total - layer.start)}, ins=[a], prefix="a")
        (a,) = g.add("asetpts", {"expr": "PTS-STARTPTS"}, ins=[a], prefix="a")
        (a,) = g.add("volume", {"volume": num(layer.volume)}, ins=[a], prefix="a")
        ms = round(layer.start * 1000)
        if ms:
            (a,) = g.add("adelay", {"delays": f"{ms}|{ms}"}, ins=[a], prefix="a")
        tracks.append(a)
    if len(tracks) == 1:
        g.audio = base
    else:
        (g.audio,) = g.add(
            "amix",
            {"inputs": len(tracks), "duration": "first", "normalize": 0},
            ins=tracks,
            prefix="a",
        )
    g.sample_rate, g.channels = SAMPLE_RATE, 2
