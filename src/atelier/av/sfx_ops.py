"""`video.sfx`: sound design (whooshes on cuts, pops on titles, a riser into the hook, an impact).

The effects are synthesized by ffmpeg itself (seeded noise and tones shaped by filters), not
read from a sample pack: nothing to license, nothing to download, and the same bytes on every
run. Each hit is placed so its peak lands on its time: a whoosh swells into the cut, a riser
ends on the moment, pops and impacts start on it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from atelier.av.graph import Graph, num
from atelier.av.ops import video_op
from atelier.ops.registry import OpError, Params
from atelier.spec.types import Fraction, Timecode

SAMPLE_RATE = 48000
Kind = Literal["whoosh", "pop", "riser", "impact"]
PEAK = {"whoosh": 0.35, "pop": 0.0, "riser": 1.2, "impact": 0.0}  # seconds into the sound


class Hit(BaseModel):
    # The model often writes {"time": 2.4, "sound": "whoosh"} (live 2026-11-05); those names
    # are accepted as they are unambiguous, and the canonical form is always {at, kind}.
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: Timecode = Field(
        description="when it lands (seconds): a cut, a title appearing",
        validation_alias=AliasChoices("at", "time", "t", "start"),
    )
    kind: Kind = Field(
        "whoosh",
        description="whoosh | pop | riser | impact",
        validation_alias=AliasChoices("kind", "sound", "type", "effect"),
    )


class SfxParams(Params):
    hits: list[Hit] = Field(min_length=1, max_length=80, description="sounds and their times")
    volume: Fraction = Field(0.35, gt=0, le=1, description="effects level; 0.35 sits under music")


def _sound(g: Graph, kind: str, seed: int) -> str:
    """One synthesized effect as a stereo 48 kHz stream starting at 0."""
    if kind == "whoosh":
        (a,) = g.add(
            "anoisesrc", {"d": "0.55", "c": "pink", "seed": seed, "a": "1.0", "r": SAMPLE_RATE},
            ins=[], prefix="a",
        )  # fmt: skip
        (a,) = g.add("highpass", {"f": 500}, ins=[a], prefix="a")
        (a,) = g.add("lowpass", {"f": 7000}, ins=[a], prefix="a")
        (a,) = g.add(
            "afade", {"t": "in", "st": 0, "d": "0.35", "curve": "exp"}, ins=[a], prefix="a"
        )
        (a,) = g.add("afade", {"t": "out", "st": "0.35", "d": "0.2"}, ins=[a], prefix="a")
    elif kind == "pop":
        (a,) = g.add(
            "aevalsrc",
            {"exprs": "0.6*sin(2*PI*t*(1400-6000*t))*exp(-38*t)", "d": "0.12", "s": SAMPLE_RATE},
            ins=[], prefix="a",
        )  # fmt: skip
    elif kind == "riser":
        (a,) = g.add(
            "aevalsrc",
            {"exprs": "0.35*sin(2*PI*t*(180+520*t))", "d": "1.25", "s": SAMPLE_RATE},
            ins=[], prefix="a",
        )  # fmt: skip
        (a,) = g.add("afade", {"t": "in", "st": 0, "d": "1.1", "curve": "exp"}, ins=[a], prefix="a")
        (a,) = g.add("afade", {"t": "out", "st": "1.15", "d": "0.1"}, ins=[a], prefix="a")
    else:  # impact
        (a,) = g.add(
            "aevalsrc",
            {"exprs": "0.9*sin(2*PI*55*t)*exp(-6*t)", "d": "0.7", "s": SAMPLE_RATE},
            ins=[], prefix="a",
        )  # fmt: skip
    (a,) = g.add(
        "aformat",
        {"sample_fmts": "fltp", "sample_rates": SAMPLE_RATE, "channel_layouts": "stereo"},
        ins=[a],
        prefix="a",
    )
    return a


@video_op(
    name="video.sfx",
    version="1.0.0",
    params=SfxParams,
    description="sound effects: whooshes on cuts, pops on titles, a riser, an impact",
)
def sfx(g: Graph, p: SfxParams) -> None:
    """Mix synthesized sound effects into the video's sound at the given times: `whoosh`
    swells into a cut, `pop` marks a title or caption appearing, `riser` builds up and ends
    on its time (the hook), `impact` hits on it. `volume` sets them under the music. A silent
    video gets a silent bed first. Times past the end are ignored."""
    total = g.duration
    if total is None:
        raise OpError("the video's length is not known here, so the effects cannot be placed")
    layout = {"sample_fmts": "fltp", "sample_rates": SAMPLE_RATE, "channel_layouts": "stereo"}
    if g.audio is None:
        (base,) = g.add(
            "anullsrc", {"r": SAMPLE_RATE, "cl": "stereo", "d": num(total)}, ins=[], prefix="a"
        )
    else:
        (base,) = g.add("aformat", layout, ins=[g.audio], prefix="a")
    layers = [base]
    for i, hit in enumerate(p.hits):
        start = max(0.0, hit.at - PEAK[hit.kind])
        if start >= total:
            continue
        a = _sound(g, hit.kind, seed=1000 + i)
        (a,) = g.add("volume", {"volume": num(p.volume)}, ins=[a], prefix="a")
        ms = round(start * 1000)
        (a,) = g.add("adelay", {"delays": f"{ms}|{ms}"}, ins=[a], prefix="a")
        layers.append(a)
    if len(layers) == 1:
        raise OpError("every effect falls after the end of the video")
    (g.audio,) = g.add(
        "amix",
        {"inputs": len(layers), "duration": "first", "normalize": 0},
        ins=layers,
        prefix="a",
    )
    g.sample_rate, g.channels = SAMPLE_RATE, 2
