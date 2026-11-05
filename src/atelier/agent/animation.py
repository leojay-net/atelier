"""Animated scenes for the agent's `animate` tool: Vultr Serverless Inference MiniMax H3.

`POST /v1/videos` starts a job (text plus up to 9 reference pictures: the cast's character and
place designs, which keep the characters looking the same in every scene); we poll it and
download the MP4. A spoken line can ride along as an audio reference: the model times the
character's mouth to it. Its own soundtrack is turned off (it invents garbled speech), and the
toolbox lays our clean line on the clip instead, so a character sounds the same in every scene.

Measured 2026-11-05: a 6 s 1344x768 clip in ~20 s, $0.06 per second. Frame-to-frame
continuation (`first_frame`) is listed by the model but refused by the API, so scenes are kept
consistent through the references alone.

Runs in the trusted worker process (it holds the inference key), never in a step sandbox.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol

SHAPES = {"landscape": "16:9", "portrait": "9:16", "square": "1:1"}
MAX_REFERENCES = 9
STYLE = (
    "Stylized 3D animated feature-film look, soft cinematic lighting, vibrant warm colors, "
    "clean shapes, expressive faces, smooth natural motion."
)


class AnimationError(Exception):
    """A scene could not be animated; the message is shown to the model as-is."""


class AnimationService(Protocol):
    def animate(
        self,
        prompt: str,
        pictures: list[bytes],
        seconds: int,
        shape: str,
        line: bytes | None = None,
        seed: int = 7,
    ) -> bytes:
        """MP4 bytes of the scene (no sound of its own)."""
        ...


def _mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


class VultrAnimation:
    def __init__(self, base_url: str, api_key: str, model: str = "minimax-h3") -> None:
        self.base, self.key, self.model = base_url.rstrip("/"), api_key, model

    def _json(self, url: str, body: dict | None = None, timeout: float = 60) -> dict:  # type: ignore[type-arg]
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                answer: dict = json.load(resp)  # type: ignore[type-arg]
                return answer
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            raise AnimationError(f"the animation service answered {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise AnimationError("the animation service could not be reached") from None

    def animate(
        self,
        prompt: str,
        pictures: list[bytes],
        seconds: int,
        shape: str,
        line: bytes | None = None,
        seed: int = 7,
    ) -> bytes:
        refs: list[dict[str, object]] = [
            {"type": "image_url", "image_url": {"url": _uri(p, _mime(p))}, "role": "reference"}
            for p in pictures[:MAX_REFERENCES]
        ]
        if line:
            refs.append({"type": "audio_url", "audio_url": {"url": _uri(line, "audio/mpeg")}})
        body = {
            "model": self.model,
            "prompt": f"{prompt.strip()} {STYLE}"[:6900],
            "duration": max(4, min(15, int(seconds))),
            "aspect_ratio": SHAPES.get(shape, "16:9"),
            "resolution": "768p",
            "generate_audio": False,
            "seed": seed,
            **({"input_references": refs} if refs else {}),
        }
        job = self._json(f"{self.base}/videos", body)
        url = job.get("polling_url") or f"{self.base}/videos/{job.get('id')}"
        deadline = time.monotonic() + 600
        status = job
        while status.get("status") not in ("completed", "succeeded", "failed", "error"):
            if time.monotonic() > deadline:
                raise AnimationError("the scene took too long to animate; try a shorter one")
            time.sleep(3)
            status = self._json(url)
        if status.get("status") not in ("completed", "succeeded"):
            reason = status.get("error") or ""
            raise AnimationError(f"the animation service could not make it: {reason}"[:300])
        content = (status.get("unsigned_urls") or [f"{url}/content?index=0"])[0]
        req = urllib.request.Request(content, headers={"Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                video: bytes = resp.read(200 << 20)
                return video
        except (urllib.error.URLError, TimeoutError):
            raise AnimationError("the finished scene could not be downloaded") from None


def from_environment() -> AnimationService | None:
    key = os.environ.get("VULTR_INFERENCE_API_KEY") or os.environ.get("VULTR_API_KEY")
    if not key:
        return None
    base = os.environ.get("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1")
    return VultrAnimation(base, key, os.environ.get("ATELIER_ANIMATION_MODEL", "minimax-h3"))
