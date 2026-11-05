"""Original music for the agent's `make_music` tool: Vultr Serverless Inference ACE-Step 1.5.

`POST /v1/music` starts a job; we poll it and download the track. Music is made to length, at
a chosen tempo, instrumental by default, so every video can have its own royalty-free score and
its cuts can land on the beat (shot lengths in whole beats of 60 / bpm seconds).

Runs in the trusted worker process (it holds the inference key), never in a step sandbox; the
track becomes an ordinary audio asset, so the engine stays deterministic.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol


class MusicError(Exception):
    """Music could not be made; the message is shown to the model as-is."""


class MusicService(Protocol):
    def compose(self, style: str, seconds: int, bpm: int, seed: int) -> bytes:
        """MP3 bytes of an instrumental track."""
        ...


def _json(req: urllib.request.Request, timeout: float) -> dict:  # type: ignore[type-arg]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        raise MusicError(f"the music service answered {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise MusicError("the music service could not be reached") from None


class VultrMusic:
    def __init__(self, base_url: str, api_key: str, model: str = "ace-step-1.5") -> None:
        self.base, self.key, self.model = base_url.rstrip("/"), api_key, model

    def _req(self, url: str, body: dict | None = None) -> urllib.request.Request:  # type: ignore[type-arg]
        data = json.dumps(body).encode() if body is not None else None
        return urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )

    def compose(self, style: str, seconds: int, bpm: int, seed: int) -> bytes:
        job = _json(
            self._req(
                f"{self.base}/music",
                {
                    "model": self.model,
                    "task": "text2music",
                    "prompt": style[:700],
                    "duration": max(10, min(600, seconds)),
                    "bpm": bpm,
                    "instrumental": True,
                    "seed": seed,
                    "response_format": "mp3",
                },
            ),
            60,
        )
        url = job.get("polling_url") or f"{self.base}/music/{job.get('id')}"
        deadline = time.monotonic() + 240
        status = job
        while status.get("status") not in ("completed", "succeeded", "failed", "error"):
            if time.monotonic() > deadline:
                raise MusicError("the music took too long to make; try a shorter track")
            time.sleep(1.5)
            status = _json(self._req(url), 60)
        if status.get("status") not in ("completed", "succeeded"):
            raise MusicError(f"the music service could not make it: {status.get('error') or ''}")
        content = (status.get("unsigned_urls") or [f"{url}/content?index=0"])[0]
        try:
            with urllib.request.urlopen(self._req(content), timeout=120) as resp:
                return resp.read(60 << 20)
        except (urllib.error.URLError, TimeoutError):
            raise MusicError("the finished music could not be downloaded") from None


def from_environment() -> MusicService | None:
    key = os.environ.get("VULTR_INFERENCE_API_KEY") or os.environ.get("VULTR_API_KEY")
    if not key:
        return None
    base = os.environ.get("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1")
    return VultrMusic(base, key, os.environ.get("ATELIER_MUSIC_MODEL", "ace-step-1.5"))
