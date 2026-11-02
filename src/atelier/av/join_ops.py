"""Ops that join several clips into one (docs/03 §3.4).

`concat` takes N videos and plays them one after another. Its inputs may be files or the
outputs of other steps (`trim` each clip, then `concat`): the runner fuses the whole tree into
one graph, so the clips are decoded once and the result is encoded once.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from atelier.av.graph import Graph, Stream, num
from atelier.av.ops import video_op
from atelier.media import MediaType
from atelier.ops.registry import OpError, Params
from atelier.spec.types import Timecode

MAX_CLIPS = 24
SAMPLE_RATE = 48000
FPS_TOLERANCE = 0.01


class ConcatParams(Params):
    mode: Literal["fit", "fill"] = Field(
        "fit",
        description="clips of another shape: fit pads them with black, fill crops them to the "
        "first clip's frame",
    )
    fade: Timecode = Field(
        0.0, ge=0, le=5, description="crossfade between clips in seconds; 0 is a hard cut"
    )


def _limit(p: ConcatParams, n: int) -> str | None:
    if n > MAX_CLIPS:
        return f"video.concat takes at most {MAX_CLIPS} clips, got {n}"
    return None


def _conform(g: Graph, clip: Stream, mode: str, size: tuple[int, int], fps: float) -> str:
    """One clip's picture in the common frame size, frame rate and pixel format."""
    assert clip.video is not None
    W, H = size
    v = clip.video
    if (clip.width, clip.height) != (W, H):
        if mode == "fit":
            (v,) = g.add(
                "scale",
                {"w": W, "h": H, "force_original_aspect_ratio": "decrease", "flags": "lanczos"},
                ins=[v],
                prefix="v",
            )
            (v,) = g.add(
                "pad", {"w": W, "h": H, "x": "(ow-iw)/2", "y": "(oh-ih)/2", "color": "black"},
                ins=[v], prefix="v",
            )  # fmt: skip
        else:
            (v,) = g.add(
                "scale",
                {"w": W, "h": H, "force_original_aspect_ratio": "increase", "flags": "lanczos"},
                ins=[v],
                prefix="v",
            )
            (v,) = g.add("crop", {"w": W, "h": H}, ins=[v], prefix="v")
    (v,) = g.add("setsar", {"sar": 1}, ins=[v], prefix="v")
    # setpts first, fps after: `xfade` insists on a known constant frame rate, and on ffmpeg 7
    # a setpts behind the fps filter leaves the link's rate unset ("current rate of 1/0")
    (v,) = g.add("setpts", {"expr": "PTS-STARTPTS"}, ins=[v], prefix="v")
    (v,) = g.add("fps", {"fps": num(fps)}, ins=[v], prefix="v")
    (v,) = g.add("format", {"pix_fmts": "yuv420p"}, ins=[v], prefix="v")
    return v


def _sound(g: Graph, clip: Stream) -> str:
    """One clip's audio in the common format; silence of the clip's length if it has none."""
    if clip.audio is None:
        assert clip.duration is not None
        (a,) = g.add(
            "anullsrc",
            {"r": SAMPLE_RATE, "cl": "stereo", "d": num(clip.duration)},
            ins=[],
            prefix="a",
        )
        return a
    (a,) = g.add(
        "aformat",
        {"sample_fmts": "fltp", "sample_rates": SAMPLE_RATE, "channel_layouts": "stereo"},
        ins=[clip.audio],
        prefix="a",
    )
    (a,) = g.add("asetpts", {"expr": "PTS-STARTPTS"}, ins=[a], prefix="a")
    return a


def _join(
    g: Graph, pictures: list[str], sounds: list[str], durations: list[float], fade: float
) -> float:
    """Play the conformed pictures (and sounds, if any) one after another; returns the length.
    Sets `g.video` and `g.audio`."""
    n = len(pictures)
    if fade == 0:
        ins: list[str] = []
        for k, v in enumerate(pictures):
            ins.append(v)
            if sounds:
                ins.append(sounds[k])
        outs = g.add(
            "concat",
            {"n": n, "v": 1, "a": 1 if sounds else 0},
            ins=ins,
            outs=2 if sounds else 1,
            prefix="c",
            kinds="va" if sounds else "v",
        )
        g.video = outs[0]
        g.audio = outs[1] if sounds else None
        return sum(durations)
    video, elapsed = pictures[0], durations[0]
    audio = sounds[0] if sounds else None
    for k in range(1, n):
        (video,) = g.add(
            "xfade",
            {"transition": "fade", "duration": num(fade), "offset": num(elapsed - fade)},
            ins=[video, pictures[k]],
            prefix="v",
        )
        if audio is not None:
            (audio,) = g.add("acrossfade", {"d": num(fade)}, ins=[audio, sounds[k]], prefix="a")
        elapsed += durations[k] - fade
    g.video, g.audio = video, audio
    return elapsed


@video_op(
    name="video.concat",
    version="1.0.0",
    params=ConcatParams,
    inputs=(MediaType.VIDEO, MediaType.VIDEO),
    variadic=True,
    check_inputs=_limit,
    description="play clips one after another (2 to 24), optional crossfade",
)
def concat(g: Graph, p: ConcatParams) -> None:
    """Join the inputs in the order given. The result has the first clip's frame size and
    frame rate; a clip of another shape is fitted (padded with black) or filled (cropped).
    A clip without sound gets silence, so the sound stays in step with the picture. With `fade`,
    neighbouring clips cross-dissolve for that long (and the video is shorter by the overlaps);
    every clip must then be longer than twice the fade."""
    clips = [g.snapshot(), *(g.extra_stream(k) for k in range(g.extra_count))]
    if any(c.video is None for c in clips) or any(c.duration is None for c in clips):
        raise OpError("every clip needs a picture and a known length to be joined")
    first = clips[0]
    size, fps = (first.width, first.height), first.fps
    if p.fade > 0:
        shortest = min(c.duration for c in clips if c.duration is not None)
        if shortest <= 2 * p.fade:
            raise OpError(
                f"a {shortest:.2f}s clip is too short for a {p.fade:g}s crossfade "
                f"(each clip must be longer than {2 * p.fade:g}s): use a shorter fade or trim less"
            )
    pictures = [_conform(g, c, p.mode, size, fps) for c in clips]
    has_sound = any(c.audio is not None for c in clips)
    sounds = [_sound(g, c) for c in clips] if has_sound else []
    durations = [c.duration or 0.0 for c in clips]
    total = _join(g, pictures, sounds, durations, p.fade)
    g.width, g.height, g.fps = size[0], size[1], fps
    g.duration = total
    g.sample_rate, g.channels = (SAMPLE_RATE, 2) if g.audio else (0, 0)


# -- slideshow ---------------------------------------------------------------------------------

MAX_SLIDES = 40
Shape = Literal["landscape", "portrait", "square"]


class SlideshowParams(Params):
    seconds: float = Field(3.0, ge=0.5, le=30, description="how long each picture shows")
    shape: Shape = Field("landscape", description="landscape 16:9, portrait 9:16 or square")
    short_side: int = Field(1080, ge=240, le=2160, description="px of the shorter side")
    fps: Literal[24, 25, 30] = Field(30, description="frames per second")
    mode: Literal["fit", "fill"] = Field(
        "fit", description="pictures of another shape: fit pads with black, fill crops"
    )
    fade: Timecode = Field(
        0.0, ge=0, le=5, description="crossfade between pictures in seconds; 0 is a cut"
    )


def _slides_ok(p: SlideshowParams, n: int) -> str | None:
    if n > MAX_SLIDES:
        return f"video.slideshow takes at most {MAX_SLIDES} pictures, got {n}"
    if p.fade > 0 and p.fade * 2 >= p.seconds:
        return f"fade ({p.fade:g}s) must be less than half of seconds ({p.seconds:g}s)"
    return None


def _frame_size(shape: str, short: int) -> tuple[int, int]:
    long = round(short * 16 / 9 / 2) * 2
    short = short // 2 * 2
    return {"landscape": (long, short), "portrait": (short, long), "square": (short, short)}[shape]


@video_op(
    name="video.slideshow",
    version="1.0.0",
    params=SlideshowParams,
    inputs=(MediaType.IMAGE, MediaType.IMAGE),
    variadic=True,
    check_inputs=_slides_ok,
    description="turn pictures into a video, each shown for a few seconds (2 to 40)",
)
def slideshow(g: Graph, p: SlideshowParams) -> None:
    """Show the pictures in the order given, `seconds` each, in a frame of the chosen shape;
    pictures of another shape are fitted (black bars) or filled (cropped). The video has no
    sound: add music with `video.soundtrack`."""
    stills = [g.snapshot(), *(g.extra_stream(k) for k in range(g.extra_count))]
    size = _frame_size(p.shape, p.short_side)
    pictures = []
    for s in stills:
        assert s.origin is not None and s.video is not None
        g.sources[s.origin].opts = (
            "-loop", "1", "-framerate", str(p.fps), "-t", num(p.seconds), "-f", "image2",
        )  # fmt: skip
        pictures.append(_conform(g, s, p.mode, size, float(p.fps)))
    g.duration = _join(g, pictures, [], [p.seconds] * len(stills), p.fade)
    g.width, g.height, g.fps = size[0], size[1], float(p.fps)
    g.sample_rate = g.channels = 0


# -- soundtrack --------------------------------------------------------------------------------

MAX_LOOP_SECONDS = 600


class SoundtrackParams(Params):
    mode: Literal["replace", "mix"] = Field(
        "replace", description="replace the video's own sound, or mix the music under it"
    )
    volume: float = Field(1.0, ge=0.02, le=2.0, description="music level, 1 = unchanged")
    fade_out: Timecode = Field(1.0, ge=0, le=10, description="seconds of fade at the end")
    loop: bool = Field(True, description="repeat music that is shorter than the video")


@video_op(
    name="video.soundtrack",
    version="1.0.0",
    params=SoundtrackParams,
    inputs=(MediaType.VIDEO, MediaType.AUDIO),
    description="put a music track on a video, cut or looped to its length",
)
def soundtrack(g: Graph, p: SoundtrackParams) -> None:
    """Lay the second input (music) under the video, cut to the video's length (or repeated
    if it is shorter and `loop` is on) with a fade at the end. `replace` drops the video's own
    sound; `mix` keeps it and adds the music. Use `audio.mix` when the music should duck under
    a voice."""
    total = g.duration
    if total is None:
        raise OpError("the video's length is not known here, so the music cannot be fitted to it")
    music = g.extra_stream(0)
    (a,) = g.add(
        "aformat",
        {"sample_fmts": "fltp", "sample_rates": SAMPLE_RATE, "channel_layouts": "stereo"},
        ins=[g.extra_audio_label(0)],
        prefix="a",
    )
    if p.loop and music.duration and music.duration < total - 0.05:
        if music.duration > MAX_LOOP_SECONDS:
            raise OpError(f"a {music.duration:.0f}s track is too long to loop; trim it first")
        (a,) = g.add(
            "aloop", {"loop": -1, "size": round(music.duration * SAMPLE_RATE) + SAMPLE_RATE},
            ins=[a], prefix="a",
        )  # fmt: skip
    (a,) = g.add("apad", {"whole_dur": num(total)}, ins=[a], prefix="a")
    (a,) = g.add("atrim", {"end": num(total)}, ins=[a], prefix="a")
    (a,) = g.add("asetpts", {"expr": "PTS-STARTPTS"}, ins=[a], prefix="a")
    if p.volume != 1.0:
        (a,) = g.add("volume", {"volume": p.volume}, ins=[a], prefix="a")
    if 0 < p.fade_out < total:
        (a,) = g.add(
            "afade", {"t": "out", "st": num(total - p.fade_out), "d": num(p.fade_out)},
            ins=[a], prefix="a",
        )  # fmt: skip
    if p.mode == "mix" and g.audio is not None:
        (own,) = g.add(
            "aformat",
            {"sample_fmts": "fltp", "sample_rates": SAMPLE_RATE, "channel_layouts": "stereo"},
            ins=[g.audio],
            prefix="a",
        )
        (a,) = g.add(
            "amix", {"inputs": 2, "duration": "first", "normalize": False}, ins=[own, a],
            prefix="a",
        )  # fmt: skip
    g.audio = a
    g.sample_rate, g.channels = SAMPLE_RATE, 2
