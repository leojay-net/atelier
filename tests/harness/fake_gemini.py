"""A scripted stand-in for Gemini, for testing `GeminiAdapter` without the network.

`ScriptedGemini` presents the `.models.generate_content(...)` shape the real `google.genai`
client does and hands back real SDK response objects (built with `model_validate`), so the
adapter's attribute access is exercised exactly as in production.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

from google.genai import types

_ids = itertools.count(1)


def function_call(name: str, args: dict[str, Any], id: str | None = None) -> dict[str, Any]:
    return {"function_call": {"name": name, "args": args, "id": id or f"call_{next(_ids)}"}}


def text_part(t: str) -> dict[str, Any]:
    return {"text": t}


def message(
    parts: list[dict[str, Any]],
    *,
    finish_reason: str = "STOP",
    usage: tuple[int, int, int] = (100, 50, 0),
    model: str = "gemini-2.5-flash",
) -> types.GenerateContentResponse:
    """`usage` is (prompt tokens, output tokens, cached content tokens)."""
    prompt, output, cached = usage
    return types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {
                    "content": {"role": "model", "parts": parts},
                    "finish_reason": finish_reason,
                    "index": 0,
                }
            ],
            "usage_metadata": {
                "prompt_token_count": prompt,
                "candidates_token_count": output,
                "cached_content_token_count": cached or None,
                "total_token_count": prompt + output,
            },
            "model_version": model,
        }
    )


def final(
    summary: str = "Done.", warnings: tuple[str, ...] = (), question: str = "", **kw: Any
) -> types.GenerateContentResponse:
    body = json.dumps({"summary": summary, "warnings": list(warnings), "question": question})
    return message([text_part(body)], finish_reason="STOP", **kw)


def run_pipeline(spec: dict[str, Any], **kw: Any) -> types.GenerateContentResponse:
    return message([function_call("run_pipeline", {"spec": spec})], finish_reason="STOP", **kw)


def refusal(**kw: Any) -> types.GenerateContentResponse:
    return message([], finish_reason="SAFETY", **kw)


Response = types.GenerateContentResponse | Callable[[dict[str, Any]], types.GenerateContentResponse]


class _Models:
    def __init__(self, parent: ScriptedGemini) -> None:
        self._parent = parent

    def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        return self._parent._next(kwargs)


class ScriptedGemini:
    def __init__(self, *responses: Response) -> None:
        self.script = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.models = _Models(self)

    def _next(self, request: dict[str, Any]) -> types.GenerateContentResponse:
        self.requests.append(dict(request))
        if not self.script:
            raise AssertionError("the model was called more often than the test scripted")
        nxt = self.script.pop(0)
        return nxt(request) if callable(nxt) else nxt

    @property
    def calls(self) -> int:
        return len(self.requests)
