"""A scripted stand-in for an OpenAI-compatible model (OpenAI, Ollama, ...), for testing
`OpenAICompatibleAdapter` without the network.

`ScriptedOpenAI` presents the nested `.chat.completions.create(...)` shape the real `openai`
client does and hands back real `openai` SDK response objects (built with `model_validate`),
so the adapter's attribute access is exercised exactly as in production.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

from openai.types.chat import ChatCompletion

_ids = itertools.count(1)


def tool_call(name: str, args: dict[str, Any], id: str | None = None) -> dict[str, Any]:
    return {
        "id": id or f"call_{next(_ids)}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def message(
    *,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
    usage: tuple[int, int, int] = (100, 50, 0),
    model: str = "gpt-4o",
) -> ChatCompletion:
    """`usage` is (prompt tokens, completion tokens, cached prompt tokens)."""
    prompt, completion, cached = usage
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return ChatCompletion.model_validate(
        {
            "id": f"chatcmpl_{next(_ids)}",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": msg}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        }
    )


def final(
    summary: str = "Done.", warnings: tuple[str, ...] = (), question: str = "", **kw: Any
) -> ChatCompletion:
    body = json.dumps({"summary": summary, "warnings": list(warnings), "question": question})
    return message(content=body, finish_reason="stop", **kw)


def run_pipeline(spec: dict[str, Any], **kw: Any) -> ChatCompletion:
    calls = [tool_call("run_pipeline", {"spec": spec})]
    return message(tool_calls=calls, finish_reason="tool_calls", **kw)


def refusal(**kw: Any) -> ChatCompletion:
    return message(content=None, finish_reason="content_filter", **kw)


Response = ChatCompletion | Callable[[dict[str, Any]], ChatCompletion]


class _Completions:
    def __init__(self, parent: ScriptedOpenAI) -> None:
        self._parent = parent

    def create(self, **kwargs: Any) -> ChatCompletion:
        return self._parent._next(kwargs)


class _Chat:
    def __init__(self, parent: ScriptedOpenAI) -> None:
        self.completions = _Completions(parent)


class ScriptedOpenAI:
    def __init__(self, *responses: Response) -> None:
        self.script = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.chat = _Chat(self)

    def _next(self, request: dict[str, Any]) -> ChatCompletion:
        self.requests.append(dict(request))
        if not self.script:
            raise AssertionError("the model was called more often than the test scripted")
        nxt = self.script.pop(0)
        return nxt(request) if callable(nxt) else nxt

    @property
    def calls(self) -> int:
        return len(self.requests)
