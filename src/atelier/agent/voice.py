"""Spoken voiceovers for the agent's `make_voiceover` tool (Vultr Serverless Inference TTS).

Runs in the trusted worker process, which holds the inference key, never in a step sandbox
(those get no credentials, docs/10-security.md). The speech becomes an ordinary audio asset in
the organization's store, so the engine stays deterministic: every later step consumes a
content-addressed file, and the existing ops (video.soundtrack, audio.mix with ducking) put it
under the picture.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Protocol

# Voices: an accent and a woman's or a man's voice, made with qwen3-tts "voice design" (a
# described voice) and a fixed seed, so every sentence of a voiceover is the same person.
# Live feedback: the built-in qwen speakers all sounded Asian-accented in English.
ACCENTS: dict[str, tuple[str, str]] = {  # id -> (label, how the accent is described)
    "american": ("American", "a General American accent"),
    "british": ("British", "a standard Southern British English accent"),
    "nigerian": ("Nigerian", "a natural Nigerian English accent"),
    "ghanaian": ("Ghanaian", "a natural Ghanaian English accent"),
    "kenyan": ("Kenyan", "a natural Kenyan English accent"),
    "south_african": ("South African", "a natural South African English accent"),
    "indian": ("Indian", "a natural Indian English accent"),
    "australian": ("Australian", "a natural Australian accent"),
    "irish": ("Irish", "a natural Irish accent"),
    "scottish": ("Scottish", "a natural Scottish accent"),
    "jamaican": ("Jamaican", "a natural Jamaican English accent"),
}
PEOPLE: dict[str, tuple[str, str]] = {
    "woman": ("woman", "A warm, friendly woman in her late twenties"),
    "man": ("man", "A warm, confident man in his thirties"),
}
DESIGNED: dict[str, tuple[str, str]] = {  # voice id -> (label, voice description)
    f"{a}_{g}": (
        f"{label} {who}",
        f"{person} speaking English with {accent}; clear, upbeat and natural, like a presenter.",
    )
    for a, (label, accent) in ACCENTS.items()
    for g, (who, person) in PEOPLE.items()
}
# the original qwen3-tts speakers, still accepted for earlier requests
BUILT_IN = ("serena", "vivian", "ryan", "eric", "dylan", "aiden", "uncle_fu")
VOICES: dict[str, str] = {k: label for k, (label, _) in DESIGNED.items()}
DEFAULT_VOICE = "american_woman"
SEED = 7  # one seed for every sentence: the designed voice stays the same person
MAX_CHARS = 4000  # per make_voiceover call; about 4-5 minutes of speech
CHUNK_CHARS = 600  # per TTS request, split at sentence ends


class VoiceError(Exception):
    """A voiceover could not be made; the message is shown to the model as-is."""


class VoiceService(Protocol):
    def speak(self, text: str, voice: str) -> bytes:
        """MP3 bytes of `text` read by `voice`."""
        ...


class VultrVoice:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "qwen3-tts-12hz-1.7b",
        timeout: float = 120.0,
    ) -> None:
        self.url = base_url.rstrip("/") + "/audio/speech"
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def speak(self, text: str, voice: str) -> bytes:
        fields: dict[str, object] = {"voice": voice}
        if voice in DESIGNED:
            fields = {
                "voice": "design",
                "instructions": DESIGNED[voice][1],
                "language": "English",
                "seed": SEED,
            }
        body = json.dumps(
            {"model": self.model, "input": text, "response_format": "mp3", **fields}
        ).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode(errors="replace")
                if e.code < 500 or attempt == 2:
                    raise VoiceError(
                        f"the voice service refused the request ({e.code}): {detail}"
                    ) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 2:
                    raise VoiceError(f"the voice service could not be reached: {e}") from None
        raise VoiceError("the voice service could not be reached")  # pragma: no cover


def from_environment() -> VoiceService | None:
    """The Vultr voice when an inference key is configured, else None (the tool says so)."""
    key = os.environ.get("VULTR_INFERENCE_API_KEY") or os.environ.get("VULTR_API_KEY")
    if not key:
        return None
    base = os.environ.get("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1")
    return VultrVoice(base, key, model=os.environ.get("ATELIER_TTS_MODEL", "qwen3-tts-12hz-1.7b"))


def chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split at sentence ends so no request is too long and no sentence is cut in half."""
    sentences = re.split(r"(?<=[.!?…])\s+", " ".join(text.split()))
    out: list[str] = []
    current = ""
    for s in sentences:
        while len(s) > limit:  # one very long sentence: split at the last space before the limit
            cut = s.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                out.append(current)
                current = ""
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        candidate = f"{current} {s}".strip()
        if len(candidate) > limit and current:
            out.append(current)
            current = s
        else:
            current = candidate
    if current:
        out.append(current)
    return [c for c in out if c]


# -- word-timed captions --------------------------------------------------------------------
# The voice is spoken one sentence at a time, so each sentence's start and length are exact;
# inside a sentence, words get time in proportion to their length (plus a beat after
# punctuation). That is close enough to read as in sync, with no speech recognition needed.

SPEECH_SHARE = 0.92  # the end of each spoken sentence is a short breath, not a word
MAX_CUE_WORDS = 3
MAX_CUE_CHARS = 18


def sentences(text: str) -> list[str]:
    """Sentences to speak one by one: very short ones join the next, very long ones split."""
    raw = re.split(r"(?<=[.!?…])\s+", " ".join(text.split()))
    out: list[str] = []
    carry = ""
    for s in raw:
        s = f"{carry} {s}".strip()
        if len(s) < 25 and s is not raw[-1]:
            carry = s
            continue
        carry = ""
        out.extend(chunks(s, CHUNK_CHARS))
    if carry:
        out.append(carry)
    return [s for s in out if s]


def word_times(spoken: list[str], durations: list[float]) -> list[tuple[float, float, str]]:
    """(start, end, word) for every word, from each sentence's measured length."""
    timed: list[tuple[float, float, str]] = []
    t0 = 0.0
    for sentence, length in zip(spoken, durations, strict=True):
        words = sentence.split()
        weights = [len(w) + 1 + (3 if w[-1] in ",;:.!?…" else 0) for w in words]
        total = sum(weights) or 1
        t = t0
        for w, weight in zip(words, weights, strict=True):
            span = length * SPEECH_SHARE * weight / total
            timed.append((t, t + span, w))
            t += span
        t0 += length
    return timed


def caption_cues(words: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Groups of 1-3 words (a new group after punctuation), each shown until the next."""
    cues: list[tuple[float, float, str]] = []
    group: list[tuple[float, float, str]] = []
    for item in words:
        text = " ".join(w for _, _, w in [*group, item])
        if group and (len(group) >= MAX_CUE_WORDS or len(text) > MAX_CUE_CHARS):
            cues.append((group[0][0], group[-1][1], " ".join(w for _, _, w in group)))
            group = []
        group.append(item)
        if item[2][-1] in ",;:.!?…":
            cues.append((group[0][0], group[-1][1], " ".join(w for _, _, w in group)))
            group = []
    if group:
        cues.append((group[0][0], group[-1][1], " ".join(w for _, _, w in group)))
    # each cue stays up until the next one starts (no flicker between words)
    return [
        (start, cues[i + 1][0] if i + 1 < len(cues) and cues[i + 1][0] - end < 0.6 else end, text)
        for i, (start, end, text) in enumerate(cues)
    ]


def srt(cues: list[tuple[float, float, str]]) -> str:
    def stamp(t: float) -> str:
        ms = round(t * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    blocks = [
        f"{i}\n{stamp(a)} --> {stamp(b)}\n{text.rstrip(',;:')}"
        for i, (a, b, text) in enumerate(cues, 1)
    ]
    return "\n\n".join(blocks) + "\n"
