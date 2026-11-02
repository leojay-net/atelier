"""A scripted stand-in for the model, for testing the agent loop without the network.

`ScriptedModel` implements `MessagesClient`: it hands back the next scripted response and keeps
a snapshot of every request. Responses are real `anthropic` SDK message objects (built with
`model_validate`), so the loop's attribute access is exercised exactly as in production.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

from anthropic.types.beta import BetaMessage

_ids = itertools.count(1)


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def thinking() -> dict[str, Any]:
    return {"type": "thinking", "thinking": "", "signature": "sig"}


def tool_use(name: str, tool_input: dict[str, Any], id: str | None = None) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": id or f"toolu_{next(_ids)}",
        "name": name,
        "input": tool_input,
    }


def fallback_marker(category: str = "cyber") -> dict[str, Any]:
    return {
        "type": "fallback",
        "from": {"model": "claude-opus-5"},
        "to": {"model": "claude-opus-4-8"},
        "trigger": {"type": "refusal", "category": category},
    }


def message(
    content: list[dict[str, Any]],
    stop_reason: str = "end_turn",
    *,
    usage: tuple[int, int, int, int] = (100, 50, 0, 0),
    stop_details: dict[str, Any] | None = None,
    model: str = "claude-opus-5",
) -> BetaMessage:
    """`usage` is (uncached input, output, cache read, cache write)."""
    inp, out, read, write = usage
    return BetaMessage.model_validate(
        {
            "id": f"msg_{next(_ids)}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": write,
            },
        }
    )


def final(
    summary: str = "Done.", warnings: tuple[str, ...] = (), question: str = "", **kw: Any
) -> BetaMessage:
    body = json.dumps({"summary": summary, "warnings": list(warnings), "question": question})
    return message([text(body)], "end_turn", **kw)


def run_pipeline(spec: dict[str, Any], **kw: Any) -> BetaMessage:
    return message([tool_use("run_pipeline", {"spec": spec})], "tool_use", **kw)


def refusal(category: str | None = "cyber", explanation: str | None = "declined") -> BetaMessage:
    return message(
        [],
        "refusal",
        stop_details={"type": "refusal", "category": category, "explanation": explanation},
    )


Response = BetaMessage | Callable[[dict[str, Any]], BetaMessage]


class ScriptedModel:
    def __init__(self, *responses: Response) -> None:
        self.script = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, request: dict[str, Any]) -> BetaMessage:
        self.requests.append(dict(request))  # messages is already a fresh list per request
        if not self.script:
            raise AssertionError("the model was called more often than the test scripted")
        nxt = self.script.pop(0)
        return nxt(request) if callable(nxt) else nxt

    @property
    def calls(self) -> int:
        return len(self.requests)
