"""Regenerate the eval suite's video, audio and caption inputs (ffmpeg lavfi; no real footage).

    uv run python tests/evals/make_av_inputs.py

The committed files are what the video tasks use; rerun only when adding an input. The "voice" is
a gated, wobbling 150 Hz tone (three seconds of speech, one of pause), which is all the tasks
need: they ask about length, loudness, silence and structure, never about what is said.

    talk.mp4          12 s 640x360, speech-like sound (0-3, 4-7, 8-11 s), a clock in the picture
    talk_portrait.mp4  8 s 360x640, the same idea, for vertical sources
    broll_a.mp4        5 s 640x360, with sound       broll_b.mp4  5 s, no sound
    broll_c.mp4        5 s 640x360, with sound
    quiet_talk.mp4    10 s 640x360, speech at a very low level
    voice.wav         11 s mono: 1.5 s silence, speech, a 2.5 s pause in the middle, 2 s of silence
    noisy_voice.wav   10 s mono speech over steady hiss
    music.m4a         20 s stereo, a soft chord (AAC)
    captions.srt / captions.vtt   three cues matching talk.mp4
"""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).parent / "inputs"
COMMON = ["-hide_banner", "-loglevel", "error", "-y"]
OUTPUT = ["-fflags", "+bitexact", "-map_metadata", "-1"]
X264 = [
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    "34",
    "-pix_fmt",
    "yuv420p",
    "-threads",
    "1",
]
AAC = ["-c:a", "aac", "-b:a", "80k", "-ar", "48000", "-ac", "2"]

SPEECH = "0.25*sin(2*PI*150*t)*(0.6+0.4*sin(2*PI*3*t))"


def speech(gate: str, level: float = 1.0) -> str:
    return f"aevalsrc='{level}*{SPEECH}*({gate})':s=48000"


def ffmpeg(*args: str, output: str, extra: tuple[str, ...] = ()) -> None:
    subprocess.run(["ffmpeg", *COMMON, *args, *OUTPUT, *extra, output], check=True)


def clip(name: str, source: str, seconds: int, sound: str | None) -> None:
    args = ["-f", "lavfi", "-i", f"{source}:duration={seconds}"]
    if sound is not None:
        args += ["-f", "lavfi", "-i", f"{sound}:d={seconds}", "-shortest"]
    args += X264 + (AAC if sound is not None else ["-an"])
    ffmpeg(*args, output=str(HERE / name), extra=("-movflags", "+faststart"))


def main() -> None:
    HERE.mkdir(exist_ok=True)
    talk_gate = "lt(mod(t,4),3)"
    clip("talk.mp4", "testsrc2=size=640x360:rate=30", 12, speech(talk_gate))
    clip("talk_portrait.mp4", "testsrc2=size=360x640:rate=30", 8, speech(talk_gate))
    clip("broll_a.mp4", "testsrc2=size=640x360:rate=30", 5, "sine=frequency=330:sample_rate=48000")
    clip("broll_b.mp4", "smptebars=size=640x360:rate=30", 5, None)
    clip(
        "broll_c.mp4", "rgbtestsrc=size=640x360:rate=30", 5, "sine=frequency=550:sample_rate=48000"
    )
    clip("quiet_talk.mp4", "testsrc2=size=640x360:rate=30", 10, speech(talk_gate, 0.05))

    gated = "between(t,1.5,4.5)+between(t,7,9)"
    ffmpeg("-f", "lavfi", "-i", f"{speech(gated)}:d=11", output=str(HERE / "voice.wav"),
           extra=("-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"))  # fmt: skip
    ffmpeg("-filter_complex",
           f"{speech('lt(mod(t,4),3)')}:d=10[v];anoisesrc=color=white:amplitude=0.03:sample_rate=48000:d=10[n];"
           "[v][n]amix=inputs=2:normalize=0[o]",
           "-map", "[o]", output=str(HERE / "noisy_voice.wav"),
           extra=("-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"))  # fmt: skip
    chord = "0.12*(sin(2*PI*261.6*t)+sin(2*PI*329.6*t)+sin(2*PI*392*t))"
    ffmpeg("-f", "lavfi", "-i", f"aevalsrc='{chord}|{chord}*0.9':s=48000:d=20",
           output=str(HERE / "music.m4a"),
           extra=("-c:a", "aac", "-b:a", "96k", "-f", "ipod"))  # fmt: skip

    cues = [(0.3, 2.8, "Welcome to the channel"), (4.2, 6.8, "Today we are testing the engine"),
            (8.3, 10.8, "Thanks for watching")]  # fmt: skip

    def stamp(t: float, comma: bool) -> str:
        h, rest = divmod(t, 3600)
        m, s = divmod(rest, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", "," if comma else ".")

    srt = "\n".join(
        f"{i}\n{stamp(a, True)} --> {stamp(b, True)}\n{text}\n"
        for i, (a, b, text) in enumerate(cues, 1)
    )
    vtt = "WEBVTT\n\n" + "\n".join(
        f"{stamp(a, False)} --> {stamp(b, False)}\n{text}\n" for a, b, text in cues
    )
    (HERE / "captions.srt").write_text(srt, encoding="utf-8")
    (HERE / "captions.vtt").write_text(vtt, encoding="utf-8")


if __name__ == "__main__":
    main()
