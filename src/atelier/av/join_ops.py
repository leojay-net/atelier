"""Ops that join several clips into one (docs/03 §3.4).

`concat` takes N videos and plays them one after another. Its inputs may be files or the
outputs of other steps (`trim` each clip, then `concat`): the runner fuses the whole tree into
one graph, so the clips are decoded once and the result is encoded once.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from atelier.av.graph import Graph, Stream, num
from atelier.av.ops import _ANCHOR, Anchor, _even, video_op
from atelier.media import MediaType
from atelier.ops.registry import OpError, Params
from atelier.spec.types import Timecode

MAX_CLIPS = 24
SAMPLE_RATE = 48000
FPS_TOLERANCE = 0.01

TransitionStyle = Literal[
    "fade",
    "wipeleft",
    "wiperight",
    "slideleft",
    "slideright",
    "circlecrop",
    "dissolve",
    "fadeblack",
    "fadewhite",
    "smoothleft",
    "smoothright",
]


class ConcatParams(Params):
    mode: Literal["fit", "fill"] = Field(
        "fit",
        description="clips of another shape: fit pads them with black, fill crops them to the "
        "first clip's frame",
    )
    fade: Timecode = Field(
        0.0, ge=0, le=5, description="crossfade between clips in seconds; 0 is a hard cut"
    )
    transition: TransitionStyle = Field(
        "fade",
        description="transition effect when fade > 0: fade, wipeleft, wiperight, slideleft, "
        "slideright, circlecrop, dissolve, fadeblack, fadewhite, smoothleft, smoothright",
    )


def _limit(p: ConcatParams, n: int) -> str | None:
    if n > MAX_CLIPS:
        return f"video.concat takes at most {MAX_CLIPS} clips, got {n}"
    return None


def _conform(g: Graph, clip: Stream, mode: str, size: tuple[int, int], fps: float | None) -> str:
    """One clip's picture in the common frame size, frame rate and pixel format. With `fps`
    None only the picture is shaped (a still for zoompan, which makes the frames itself: the
    fps filter emits nothing for a lone frame on ffmpeg 7)."""
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
        elif mode == "blur":  # the whole picture over a blurred, enlarged copy of itself
            back, front = g.add("split", {"outputs": 2}, ins=[v], outs=2, prefix="v")
            (back,) = g.add(
                "scale",
                {"w": W, "h": H, "force_original_aspect_ratio": "increase", "flags": "bilinear"},
                ins=[back],
                prefix="v",
            )
            (back,) = g.add("crop", {"w": W, "h": H}, ins=[back], prefix="v")
            (back,) = g.add("boxblur", {"luma_radius": 28, "luma_power": 2}, ins=[back], prefix="v")
            (back,) = g.add("eq", {"brightness": -0.08}, ins=[back], prefix="v")
            (front,) = g.add(
                "scale",
                {"w": W, "h": H, "force_original_aspect_ratio": "decrease", "flags": "lanczos"},
                ins=[front],
                prefix="v",
            )
            (v,) = g.add("overlay", {"x": "(W-w)/2", "y": "(H-h)/2"}, ins=[back, front], prefix="v")
        else:
            (v,) = g.add(
                "scale",
                {"w": W, "h": H, "force_original_aspect_ratio": "increase", "flags": "lanczos"},
                ins=[v],
                prefix="v",
            )
            (v,) = g.add("crop", {"w": W, "h": H}, ins=[v], prefix="v")
    (v,) = g.add("setsar", {"sar": 1}, ins=[v], prefix="v")
    if fps is None:
        return v
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
    g: Graph,
    pictures: list[str],
    sounds: list[str],
    durations: list[float],
    fade: float,
    transition: str = "fade",
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
            {"transition": transition, "duration": num(fade), "offset": num(elapsed - fade)},
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
    total = _join(g, pictures, sounds, durations, p.fade, p.transition)
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
    mode: Literal["blur", "fit", "fill"] = Field(
        "blur",
        description="pictures of another shape: blur shows all of it over a blurred copy, fit "
        "pads with black, fill crops (can cut off heads)",
    )
    fade: Timecode = Field(
        0.0, ge=0, le=5, description="crossfade between pictures in seconds; 0 is a cut"
    )
    motion: Literal["auto", "none", "push_in", "push_out", "pan_left", "pan_right"] = Field(
        "auto",
        description="camera move on each picture: auto varies push-ins and pans; none is still",
    )
    durations: list[float] = Field(
        default_factory=list,
        description="seconds per picture (overrides `seconds`), e.g. whole beats of the music",
    )


# Camera moves: (zoom from, zoom to, focus x from, to, focus y from, to). Focus is where the
# crop window sits (0 left/top … 1 right/bottom); slightly above center keeps faces in frame.
MOVES: dict[str, tuple[float, float, float, float, float, float]] = {
    "push_in": (1.0, 1.12, 0.5, 0.5, 0.42, 0.42),
    "pan_right": (1.1, 1.1, 0.0, 1.0, 0.45, 0.45),
    "push_out": (1.12, 1.0, 0.5, 0.5, 0.42, 0.42),
    "pan_left": (1.1, 1.1, 1.0, 0.0, 0.45, 0.45),
    "drift_up": (1.06, 1.12, 0.5, 0.5, 0.62, 0.32),
}
AUTO_MOVES = ("push_in", "pan_right", "push_out", "pan_left", "drift_up")
OVERSAMPLE = 2  # the picture is prepared at 2x the frame, so slow moves glide (sub-pixel crops)


def _ease(frames: int) -> str:
    """Smoothstep progress 0 → 1 over `frames` output frames, as an ffmpeg expression."""
    p = f"min(on/{max(1, frames - 1)},1)"
    return f"({p}*{p}*(3-2*{p}))"


def _text_safe(boxes: list[dict[str, Any]]) -> tuple[float, ...] | None:
    """A centered push-in gentle enough that text drawn on the picture never leaves the frame
    (live: pans and 12 % zooms cut a tutorial slide's edge-hugging text). None: no text."""
    edges = [
        min(x, y, 1 - x - w, 1 - y - h)
        for b in boxes
        for x, y, w, h in [tuple(float(c) for c in b["box"])]
    ]
    if not edges:
        return None
    margin = max(0.0, min(edges)) * 0.6  # the crop uses 60 %: the text keeps some air
    zoom = min(MOVES["push_in"][1], 1 / max(0.5, 1 - 2 * margin))
    return (1.0, round(zoom, 4), 0.5, 0.5, 0.5, 0.5)


def _move(
    g: Graph, v: str, move: str | tuple[float, ...], seconds: float, size: tuple[int, int],
    fps: float,
) -> str:  # fmt: skip
    z0, z1, x0, x1, y0, y1 = MOVES[move] if isinstance(move, str) else move
    frames = max(1, round(seconds * fps))
    e = _ease(frames)
    (v,) = g.add(
        "zoompan",
        {
            "z": f"{z0}+({z1 - z0})*{e}",
            "x": f"(iw-iw/zoom)*({x0}+({x1 - x0})*{e})",
            "y": f"(ih-ih/zoom)*({y0}+({y1 - y0})*{e})",
            "d": frames,
            "s": f"{size[0]}x{size[1]}",
            "fps": num(fps),
        },
        ins=[v],
        prefix="v",
    )
    # zoompan already starts at 0 at the target rate. No setpts/fps after it: on ffmpeg 7 a
    # setpts here leaves the rate unset (xfade refuses "1/0") and an fps filter drops each
    # shot's last frame; this chain is frame-exact on ffmpeg 7.1 and 8.
    (v,) = g.add("setsar", {"sar": 1}, ins=[v], prefix="v")
    (v,) = g.add("format", {"pix_fmts": "yuv420p"}, ins=[v], prefix="v")
    return v


def _slides_ok(p: SlideshowParams, n: int) -> str | None:
    if p.durations and len(p.durations) != n:
        return f"durations has {len(p.durations)} values for {n} pictures"
    if p.durations and min(p.durations) < 0.5:
        return "every picture needs at least 0.5 seconds"
    shortest = min(p.durations) if p.durations else p.seconds
    if p.fade > 0 and p.fade * 2 >= shortest:
        return f"fade ({p.fade:g}s) must be under half of the shortest picture ({shortest:g}s)"
    if n > MAX_SLIDES:
        return f"video.slideshow takes at most {MAX_SLIDES} pictures, got {n}"
    return None


def _frame_size(shape: str, short: int) -> tuple[int, int]:
    long = round(short * 16 / 9 / 2) * 2
    short = short // 2 * 2
    return {"landscape": (long, short), "portrait": (short, long), "square": (short, short)}[shape]


@video_op(
    name="video.slideshow",
    version="1.3.0",
    params=SlideshowParams,
    inputs=(MediaType.IMAGE, MediaType.IMAGE),
    variadic=True,
    check_inputs=_slides_ok,
    description="turn pictures into a video, each shown for a few seconds (2 to 40)",
)
def slideshow(g: Graph, p: SlideshowParams) -> None:
    """Show the pictures in the order given, `seconds` each (or `durations`, one per picture,
    e.g. whole beats of the music), in a frame of the chosen shape. Pictures of another shape
    are shown whole over a blurred copy (`blur`), fitted with bars (`fit`) or cropped (`fill`).
    `motion` gives every picture a slow, eased camera move (push in/out, pans); `auto` varies
    them from picture to picture (a picture with text on it gets a gentle centered push that
    keeps the text in frame), `none` keeps them still. The video has no sound: add music
    with `video.soundtrack`."""
    stills = [g.snapshot(), *(g.extra_stream(k) for k in range(g.extra_count))]
    size = _frame_size(p.shape, p.short_side)
    durations = list(p.durations) or [p.seconds] * len(stills)
    pictures = []
    for k, (s, seconds) in enumerate(zip(stills, durations, strict=True)):
        assert s.origin is not None and s.video is not None
        if p.motion == "none":
            g.sources[s.origin].opts = (
                "-loop", "1", "-framerate", str(p.fps), "-t", num(seconds), "-f", "image2",
            )  # fmt: skip
            pictures.append(_conform(g, s, p.mode, size, float(p.fps)))
            continue
        # one frame in, prepared at OVERSAMPLE x the frame; zoompan makes the moving shot
        g.sources[s.origin].opts = ("-f", "image2")
        big = (size[0] * OVERSAMPLE, size[1] * OVERSAMPLE)
        v = _conform(g, s, p.mode, big, None)
        move: str | tuple[float, ...] = (
            AUTO_MOVES[k % len(AUTO_MOVES)] if p.motion == "auto" else p.motion
        )
        safe = _text_safe(g.sources[s.origin].meta.get("boxes") or [])
        if safe is not None and p.motion == "auto":
            move = safe
        pictures.append(_move(g, v, move, seconds, size, float(p.fps)))
    g.duration = _join(g, pictures, [], durations, p.fade)
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


# -- picture in picture ------------------------------------------------------------------------


class PipParams(Params):
    scale: float = Field(
        0.3,
        ge=0.05,
        le=0.9,
        description="width of the PiP video as a fraction of the main frame width",
    )
    anchor: Anchor = Field(
        "bottom_right",
        description="position anchor: top_left, top_right, bottom_left, bottom_right, center, etc.",
    )
    margin: float = Field(
        0.04,
        ge=0.0,
        le=0.25,
        description="margin from the video edges as a fraction of main frame width",
    )
    opacity: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="opacity of the PiP window (1.0 = opaque)",
    )
    start: Timecode = Field(
        0.0,
        ge=0,
        description="start timestamp in seconds when PiP appears",
    )
    end: Timecode | None = Field(
        None,
        ge=0,
        description="end timestamp in seconds when PiP disappears (None = until end of video)",
    )
    audio: Literal["mix", "main_only", "pip_only"] = Field(
        "mix",
        description="audio mode: mix both tracks, or keep only main or pip audio",
    )
    pip_volume: float = Field(
        0.8,
        ge=0.0,
        le=2.0,
        description="relative volume of the PiP audio track when mixed",
    )


@video_op(
    name="video.pip",
    version="1.0.0",
    params=PipParams,
    inputs=(MediaType.VIDEO, MediaType.VIDEO),
    description="overlay a second video (facecam/reaction) as picture-in-picture",
)
def pip(g: Graph, p: PipParams) -> None:
    """Overlay a second video (the second input) as a picture-in-picture window on top of
    the main video. Supports fractional scale, anchor positioning, margin, opacity, start/end
    time ranges, and audio mixing."""
    pip_stream = g.extra_stream(0)
    if pip_stream.video is None:
        raise OpError("the second input has no video stream to overlay as picture-in-picture")

    pw = _even(g.width * p.scale)
    if pip_stream.width > 0 and pip_stream.height > 0:
        ph = _even(pw * pip_stream.height / pip_stream.width)
    else:
        ph = _even(g.height * p.scale)
    pw, ph = max(2, pw), max(2, ph)

    margin = round(g.width * p.margin)
    fx, fy = _ANCHOR[p.anchor]
    x = round(fx * (g.width - pw - 2 * margin)) + margin
    y = round(fy * (g.height - ph - 2 * margin)) + margin

    v_pip = pip_stream.video
    (v_pip,) = g.add("scale", {"w": pw, "h": ph, "flags": "lanczos"}, ins=[v_pip], prefix="v")
    (v_pip,) = g.add("setsar", {"sar": 1}, ins=[v_pip], prefix="v")
    (v_pip,) = g.add("setpts", {"expr": "PTS-STARTPTS"}, ins=[v_pip], prefix="v")
    (v_pip,) = g.add("fps", {"fps": num(g.fps)}, ins=[v_pip], prefix="v")

    if p.opacity < 1.0:
        (v_pip,) = g.add("format", {"pix_fmts": "rgba"}, ins=[v_pip], prefix="v")
        (v_pip,) = g.add("colorchannelmixer", {"aa": p.opacity}, ins=[v_pip], prefix="v")
    else:
        (v_pip,) = g.add("format", {"pix_fmts": "yuv420p"}, ins=[v_pip], prefix="v")

    args: dict[str, Any] = {"x": x, "y": y}
    if p.start > 0 or p.end is not None:
        window = (
            f"gte(t,{num(p.start)})" if p.end is None else f"between(t,{num(p.start)},{num(p.end)})"
        )
        args["enable"] = window

    assert g.video is not None
    (g.video,) = g.add("overlay", args, ins=[g.video, v_pip], prefix="v")

    # Audio handling
    if p.audio == "pip_only":
        if pip_stream.audio is not None:
            g.audio = _sound(g, pip_stream)
    elif p.audio == "mix" and pip_stream.audio is not None:
        pip_a = _sound(g, pip_stream)
        if p.pip_volume != 1.0:
            (pip_a,) = g.add("volume", {"volume": p.pip_volume}, ins=[pip_a], prefix="a")
        if g.audio is not None:
            (g.audio,) = g.add(
                "amix",
                {"inputs": 2, "duration": "first", "normalize": False},
                ins=[g.audio, pip_a],
                prefix="a",
            )
        else:
            g.audio = pip_a


# -- transition --------------------------------------------------------------------------------


class TransitionParams(Params):
    transition: TransitionStyle = Field(
        "fade",
        description="transition effect: fade, wipeleft, wiperight, slideleft, etc.",
    )
    duration: Timecode = Field(
        1.0, ge=0.1, le=5.0, description="duration of the transition in seconds"
    )
    mode: Literal["fit", "fill"] = Field(
        "fit",
        description="how to conform clips of different frame sizes: fit pads, fill crops",
    )


@video_op(
    name="video.transition",
    version="1.0.0",
    params=TransitionParams,
    inputs=(MediaType.VIDEO, MediaType.VIDEO),
    description="transition between two clips (crossfade, wipe, slide, dissolve)",
    hidden=True,  # catalogue budget: video.concat's `transition` covers it for 2 to 24 clips
)
def transition_op(g: Graph, p: TransitionParams) -> None:
    first_stream = Stream(
        video=g.video,
        audio=g.audio,
        width=g.width,
        height=g.height,
        fps=g.fps,
        duration=g.duration,
        sample_rate=g.sample_rate,
        channels=g.channels,
    )
    second_stream = g.extra_stream(0)
    d1 = first_stream.duration or 0.0
    d2 = second_stream.duration or 0.0
    if d1 <= p.duration or d2 <= p.duration:
        raise OpError(f"both clips must be longer than transition duration ({p.duration:g}s)")

    target_size = (g.width, g.height)
    v1 = _conform(g, first_stream, p.mode, target_size, g.fps)
    v2 = _conform(g, second_stream, p.mode, target_size, g.fps)
    sounds: list[str] = []
    if first_stream.audio is not None or second_stream.audio is not None:
        sounds = [_sound(g, first_stream), _sound(g, second_stream)]

    total = _join(g, [v1, v2], sounds, [d1, d2], p.duration, p.transition)
    g.duration = total
