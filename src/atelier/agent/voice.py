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

# qwen3-tts voices on Vultr Serverless Inference (the endpoint lists them in its 400 error).
VOICES: dict[str, str] = {
    "serena": "warm, natural female voice (default)",
    "vivian": "bright female voice",
    "ryan": "friendly male voice",
    "eric": "calm male voice",
    "dylan": "energetic male voice",
    "aiden": "young male voice",
    "uncle_fu": "older, deep male voice",
}
DEFAULT_VOICE = "serena"
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
        body = json.dumps(
            {"model": self.model, "input": text, "voice": voice, "response_format": "mp3"}
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
