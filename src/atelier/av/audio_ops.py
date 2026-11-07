"""Audio ops (docs/03 §3.4). Like the video ops they are graph builders: they add filters to the
fused ffmpeg graph, so `video.trim → audio.extract → export` is one decode and one encode."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypeVar

import numpy as np
from pydantic import Field, model_validator

from atelier import models
from atelier.av.ducking import ANALYSIS_RATE, CONTROL_RATE, RELEASE_S, duck_curve
from atelier.av.export import Loudness, apply_loudness, av_summary
from atelier.av.graph import Graph, Source, num
from atelier.media import MediaType
from atelier.ops.registry import Artifact, Determinism, OpError, Params, op
from atelier.spec.types import Fraction, Timecode

B = TypeVar("B", bound=Callable[[Graph, Any], None])
AUDIO = MediaType.AUDIO
RESOURCES = ("ffmpeg", "ffprobe")


def audio_op(
    *,
    name: str,
    version: str,
    params: type[Params],
    description: str,
    inputs: tuple[MediaType, ...] = (AUDIO,),
    output: MediaType = AUDIO,
    also: tuple[str, ...] = (),
    summary: Callable[[Artifact], str] = av_summary,
) -> Callable[[B], B]:
    def decorate(builder: B) -> B:
        return op(
            name=name,
            version=version,
            inputs=inputs,
            output=output,
            determinism=Determinism.PERCEPTUAL,
            params=params,
            summary=summary,
            description=description,
            resources=(*RESOURCES, *also),
            graph=builder,
            graph_media=inputs[:1],
        )(builder)

    return decorate


# -- extract -----------------------------------------------------------------------------------


class ExtractParams(Params):
    pass


@audio_op(
    name="audio.extract",
    version="1.0.0",
    params=ExtractParams,
    inputs=(MediaType.VIDEO,),
    description="take the audio track out of a video",
)
def extract(g: Graph, p: ExtractParams) -> None:
    """Keep only the sound of a video, with its timing. Combine with audio.* ops and an audio
    export preset (podcast_mp3, voice_opus, wav_master…)."""
    if not g.has_audio:
        raise OpError("this video has no audio track to extract")
    g.video = None
    g.width = g.height = 0


# -- trim --------------------------------------------------------------------------------------


class TrimParams(Params):
    start: Timecode = Field(0.0, description="where the clip starts (seconds or [HH:]MM:SS[.mmm])")
    end: Timecode | None = Field(None, description="where it ends; omit to keep the rest")

    @model_validator(mode="after")
    def _ordered(self) -> TrimParams:
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"end ({self.end:g}s) must be after start ({self.start:g}s)")
        return self


@audio_op(
    name="audio.trim",
    version="1.0.0",
    params=TrimParams,
    description="cut a time range from audio, sample-accurate",
)
def trim(g: Graph, p: TrimParams) -> None:
    """Keep only the part of the audio between `start` and `end`. The result starts at 0."""
    total = g.duration
    if total is not None and p.start >= total:
        raise OpError(f"trim start {p.start:g}s is past the end: the audio is {total:.2f}s long")
    if total is not None and p.end is not None and p.end > total + 0.001:
        raise OpError(
            f"trim end {p.end:g}s is past the end: the audio is {total:.2f}s long "
            f"(use end <= {total:.2f})"
        )
    end = p.end if p.end is not None else total
    args: dict[str, Any] = {"start": p.start}
    if p.end is not None:
        args["end"] = p.end
    g.af("atrim", args)
    g.af("asetpts", {"expr": "PTS-STARTPTS"})
    g.duration = (end - p.start) if end is not None else None


# -- fade --------------------------------------------------------------------------------------


class FadeParams(Params):
    fade_in: Timecode = Field(0.0, description="seconds of fade in from silence")
    fade_out: Timecode = Field(0.0, description="seconds of fade out to silence")

    @model_validator(mode="after")
    def _something(self) -> FadeParams:
        if self.fade_in <= 0 and self.fade_out <= 0:
            raise ValueError("set fade_in and/or fade_out to more than 0 seconds")
        return self


@audio_op(
    name="audio.fade",
    version="1.0.0",
    params=FadeParams,
    description="fade audio in from and/or out to silence",
)
def fade(g: Graph, p: FadeParams) -> None:
    """Fade the sound in at the start and/or out at the end."""
    total = g.duration
    if p.fade_out > 0 and total is None:
        raise OpError("fade_out needs a known duration; trim the audio first")
    if total is not None and p.fade_in + p.fade_out > total + 0.001:
        raise OpError(
            f"fades ({p.fade_in + p.fade_out:g}s) are longer than the audio ({total:.2f}s)"
        )
    if p.fade_in > 0:
        g.af("afade", {"t": "in", "st": 0, "d": p.fade_in})
    if p.fade_out > 0 and total is not None:
        g.af("afade", {"t": "out", "st": total - p.fade_out, "d": p.fade_out})


# -- loudnorm ----------------------------------------------------------------------------------


class LoudnormParams(Params):
    target_lufs: float = Field(-16.0, ge=-40, le=-5, description="integrated loudness target, LUFS")
    true_peak_db: float = Field(-1.0, ge=-9, le=0, description="true-peak ceiling, dBTP")
    lra: float = Field(11.0, ge=1, le=50, description="loudness range target, LU")


@audio_op(
    name="audio.loudnorm",
    version="1.0.0",
    params=LoudnormParams,
    description="normalize loudness (EBU R128, two-pass) to a target",
)
def loudnorm(g: Graph, p: LoudnormParams) -> None:
    """Bring the audio to `target_lufs` with a measured, linear gain, so dynamics are untouched.
    Silent audio is left as it is. (Export presets can also normalize, so this is only needed
    to normalize before other steps.)"""
    apply_loudness(g, Loudness(target_lufs=p.target_lufs, true_peak_db=p.true_peak_db, lra=p.lra))


# -- silence -----------------------------------------------------------------------------------


class SilenceParams(Params):
    where: Literal["edges", "all"] = Field(
        "edges", description="edges: trim the start and end; all: also cut long pauses inside"
    )
    threshold_db: float = Field(-45.0, ge=-80, le=-10, description="quieter than this is silence")
    min_pause: float = Field(
        0.5, ge=0.1, le=5.0, description="all: only pauses longer than this (s) are cut"
    )


@audio_op(
    name="audio.silence",
    version="1.0.0",
    params=SilenceParams,
    description="cut silence from the ends of a recording, or long pauses inside it",
)
def silence(g: Graph, p: SilenceParams) -> None:
    """Remove silence: `edges` trims the quiet start and end, `all` also shortens every pause
    longer than `min_pause`. Silence means quieter than `threshold_db`. The result is shorter,
    and its length is measured, so later steps (fades, trims) see the real duration."""
    th = f"{num(p.threshold_db)}dB"
    edge = {"start_periods": 1, "start_threshold": th, "start_silence": 0.1}
    g.af("silenceremove", edge)
    g.af("areverse")
    g.af("silenceremove", edge)
    g.af("areverse")
    if p.where == "all":
        g.af(
            "silenceremove",
            {"stop_periods": -1, "stop_duration": num(p.min_pause), "stop_threshold": th},
        )
    assert g.audio is not None
    pcm = g.render_pcm(g.audio, ANALYSIS_RATE)  # count what is left: the exact new length
    samples = len(pcm) // 4
    if samples == 0:
        raise OpError("the recording is silent at this threshold: nothing is left after trimming")
    g.duration = samples / ANALYSIS_RATE


# -- denoise -----------------------------------------------------------------------------------


class DenoiseParams(Params):
    strength: float = Field(12.0, ge=1, le=40, description="noise reduction in dB")
    floor_db: float = Field(-50.0, ge=-80, le=-20, description="estimated noise floor")


@audio_op(
    name="audio.denoise",
    version="1.0.0",
    params=DenoiseParams,
    description="reduce steady background noise (hiss, hum, fans)",
)
def denoise(g: Graph, p: DenoiseParams) -> None:
    """Reduce steady broadband noise with a spectral filter. `strength` is how many dB the
    noise is lowered (12 is gentle; above 25 speech starts to sound processed) and `floor_db`
    where the noise sits. It does not remove speech, music or sudden sounds."""
    g.af("afftdn", {"nr": p.strength, "nf": p.floor_db})


# -- speech denoise (RNNoise) ------------------------------------------------------------------


class SpeechDenoiseParams(Params):
    mix: Fraction = Field(
        1.0, gt=0, le=1, description="1 removes all the noise it finds; lower keeps some of it"
    )


@audio_op(
    name="audio.denoise_speech",
    version="1.0.0",
    params=SpeechDenoiseParams,
    description="remove background noise from a voice recording with a neural model (RNNoise)",
    also=("model_rnnoise_bd",),
)
def denoise_speech(g: Graph, p: SpeechDenoiseParams) -> None:
    """Suppress noise around speech (fans, traffic, hiss, keyboard) with the RNNoise model,
    which is better than `audio.denoise` on voice and worse on music. The result is 48 kHz.
    The model must be installed once with `atelier models fetch rnnoise-bd`."""
    try:
        weights = models.file_path("rnnoise-bd", "bd.rnnn")
    except models.ModelError as e:
        raise OpError(str(e)) from None
    name = g.write_file("speech_denoise.rnnn", weights.read_bytes())  # by file name, never a path
    g.af("aresample", {"sample_rate": 48000})
    g.af("arnndn", {"m": name, "mix": p.mix})
    g.sample_rate = 48000


# -- mix ---------------------------------------------------------------------------------------


class MixParams(Params):
    music_volume: Fraction = Field(0.25, ge=0.02, le=1.0, description="music level, 1 = unchanged")
    duck: bool = Field(True, description="lower the music while the first input (voice) speaks")
    duck_db: float = Field(14.0, ge=3, le=30, description="how far the music drops, in dB")
    length: Literal["voice", "music"] = Field(
        "voice", description="as long as the voice, or as the music (a short voice over a song)"
    )


@audio_op(
    name="audio.mix",
    version="2.1.0",
    params=MixParams,
    inputs=(AUDIO, AUDIO),
    description="mix music under a voice, ducking the music when the voice is present",
)
def mix(g: Graph, p: MixParams) -> None:
    """Mix the second input (music) under the first (voice). With `duck`, the music drops by
    `duck_db` while the voice speaks: it eases down just before the voice starts and back up
    slowly after it stops. The result lasts as long as the voice, or with `length` music as
    long as the music: the voice is padded with silence and the music comes back up after it.

    The ducking curve is computed from the decoded voice and applied as a gain, not by a
    compressor filter, so the result is identical on every run and every ffmpeg version."""
    assert g.audio is not None
    layout = {"sample_rates": 48000, "channel_layouts": "stereo"}
    voice_audio = g.audio
    (voice,) = g.add("aformat", {"sample_fmts": "fltp", **layout}, ins=[g.audio], prefix="a")
    total = None
    if p.length == "music":
        total = g.extra_stream(0).duration
        if not total:
            raise OpError("the music's length is not known, so the mix cannot follow it")
        (voice,) = g.add("apad", {"whole_dur": num(total)}, ins=[voice], prefix="a")
        (voice,) = g.add("atrim", {"end": num(total)}, ins=[voice], prefix="a")
    (music,) = g.add(
        "aformat", {"sample_fmts": "fltp", **layout}, ins=[g.extra_audio_label(0)], prefix="a"
    )
    (music,) = g.add("volume", {"volume": p.music_volume}, ins=[music], prefix="a")
    if p.duck:
        pcm = np.frombuffer(g.render_pcm(voice_audio, ANALYSIS_RATE), dtype=np.float32)
        gains = duck_curve(pcm, duck_db=p.duck_db)
        if total is not None:  # after the voice: ease back to full music, then hold it
            need = int(np.ceil(total * CONTROL_RATE)) + CONTROL_RATE // 10
            if need > len(gains):
                last = float(gains[-1]) if len(gains) else 1.0
                ramp = np.linspace(last, 1.0, int(RELEASE_S * CONTROL_RATE), dtype=np.float32)
                hold = np.ones(max(0, need - len(gains) - len(ramp)), dtype=np.float32)
                gains = np.concatenate([gains, ramp, hold]).astype(np.float32)[:need]
        track = g.write_file("duck0.raw", gains.tobytes())
        index = g.add_source(
            Source(
                g.work / track, MediaType.AUDIO, ".raw", {},
                opts=("-f", "f32le", "-ar", str(CONTROL_RATE), "-ac", "1"),
            )
        )  # fmt: skip
        (env,) = g.add(
            "aresample", {"sample_rate": 48000}, ins=[g.source_audio_label(index)], prefix="a"
        )
        # `pan`, not `aformat`: the upmix aformat does from mono to stereo is 3 dB down
        (env,) = g.add("pan", {"args": "stereo|c0=c0|c1=c0"}, ins=[env], prefix="a")
        (music,) = g.add("amultiply", {}, ins=[music, env], prefix="a")
    (g.audio,) = g.add(
        "amix",
        {"inputs": 2, "duration": "first", "normalize": False},
        ins=[voice, music],
        prefix="a",
    )
    g.sample_rate, g.channels = 48000, 2
    if total is not None:
        g.duration = total


# -- voice effects (a voice changer) -----------------------------------------------------------

VOICE_EFFECTS = ("deep", "high", "robot", "telephone", "echo", "whisper")


class VoiceEffectParams(Params):
    effect: Literal["deep", "high", "robot", "telephone", "echo", "whisper"] = Field(
        description="deep | high (chipmunk) | robot | telephone | echo | whisper"
    )


@audio_op(
    name="audio.voice_effect",
    version="1.0.0",
    params=VoiceEffectParams,
    description="change how a voice sounds: deep, high, robot, telephone, echo, whisper",
)
def voice_effect(g: Graph, p: VoiceEffectParams) -> None:
    """A voice changer made of plain filters. `deep` and `high` shift the pitch without
    changing the speed; `robot` rings the voice; `telephone` keeps only the middle of the sound;
    `echo` repeats it; `whisper` thins it to breath."""
    rate = g.sample_rate or 48000
    if p.effect in ("deep", "high"):
        factor = 0.8 if p.effect == "deep" else 1.3
        g.af("asetrate", {"sample_rate": round(rate * factor)})
        g.af("aresample", {"osr": rate})
        g.af("atempo", {"tempo": round(1 / factor, 4)})
    elif p.effect == "robot":
        g.af("tremolo", {"f": 70, "d": 0.95})
        g.af("aecho", {"in_gain": 0.8, "out_gain": 0.8, "delays": 12, "decays": 0.5})
    elif p.effect == "telephone":
        g.af("highpass", {"f": 450})
        g.af("lowpass", {"f": 3200})
        g.af("volume", {"volume": 1.4})
    elif p.effect == "echo":
        g.af("aecho", {"in_gain": 0.8, "out_gain": 0.88, "delays": "180|320", "decays": "0.45|0.3"})
    else:  # whisper
        g.af("highpass", {"f": 1800})
        g.af("volume", {"volume": 1.6})
