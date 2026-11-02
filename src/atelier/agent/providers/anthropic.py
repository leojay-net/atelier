"""The Claude adapter: thinking, explicit prompt caching, and server-side safety fallbacks.

This is today's Anthropic-specific request/response handling, moved here from `loop.py`
unchanged in behavior. `client.py`'s `MessagesClient` Protocol and `AnthropicMessages` are
untouched; this module wraps a `MessagesClient` and is the only thing that reads its raw SDK
response objects, so the normalized `Turn` this produces is identical to what `loop.py` used to
compute directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from atelier.agent.client import MessagesClient
from atelier.agent.providers.base import ToolCall, Turn, TurnUsage
from atelier.agent.telemetry import RequestRecord

if TYPE_CHECKING:
    from atelier.agent.tools import ToolOutcome

_INTERNAL_BEFORE_FALLBACK = ("thinking", "redacted_thinking", "tool_use")


def _after_fallback(content: Sequence[Any]) -> tuple[int, list[Any]]:
    """Index of the last `fallback` marker (-1 if none) and the blocks that follow it."""
    last = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    return last, list(content[last + 1 :])


def echo_content(content: Sequence[Any]) -> list[Any]:
    """Blocks to send back as the assistant turn.

    After a mid-output fallback the declined attempt's thinking and tool_use blocks that come
    before the boundary must not be echoed (docs: "Echoing fallback turns back"); text and
    everything after the boundary echo normally. Without a fallback this is the content as is.
    """
    last, _ = _after_fallback(content)
    return [
        b for i, b in enumerate(content) if not (i < last and b.type in _INTERNAL_BEFORE_FALLBACK)
    ]


def live_tool_uses(content: Sequence[Any]) -> list[Any]:
    """Tool calls that are actually pending: those after any fallback boundary."""
    return [b for b in _after_fallback(content)[1] if b.type == "tool_use"]


def _refusal_detail(resp: Any) -> str:
    details = getattr(resp, "stop_details", None)
    if details is None:
        return "refusal"
    category = getattr(details, "category", None) or "uncategorized"
    explanation = getattr(details, "explanation", None)
    return f"refusal ({category})" + (f": {explanation}" if explanation else "")


def request_record(resp: Any, turn: int, model: str, seconds: float) -> RequestRecord:
    """Kept for `evals/baseline.py`, a deliberately Claude-only comparison agent that reads
    raw Anthropic responses directly rather than going through a `ModelAdapter`."""
    usage = resp.usage
    return RequestRecord(
        turn=turn,
        model=str(getattr(resp, "model", None) or model),
        stop_reason=str(resp.stop_reason),
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        fallback=any(b.type == "fallback" for b in resp.content),
        seconds=seconds,
    )


class AnthropicAdapter:
    """Wraps a `MessagesClient` (`client.py`, unchanged) as a `ModelAdapter`."""

    name = "anthropic"

    def __init__(self, client: MessagesClient) -> None:
        self.client = client

    def initial_history(self, user_text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": user_text}]

    def send(
        self,
        history: list[dict[str, Any]],
        *,
        system: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model: str,
        max_tokens: int,
        effort: str,
    ) -> Turn:
        output_config = {
            "effort": effort,
            "format": {"type": "json_schema", "schema": output_schema},
        }
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
            "system": system,  # frozen; the explicit cache breakpoint is on its last block
            "tools": tools,  # frozen and sorted
            "cache_control": {"type": "ephemeral"},  # automatic caching of the growing tail
            "messages": list(history),
        }
        resp = self.client.create(request)
        return self._to_turn(resp, model)

    def _to_turn(self, resp: Any, requested_model: str) -> Turn:
        usage = resp.usage
        stop = str(resp.stop_reason)
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=b.input) for b in live_tool_uses(resp.content)
        ]
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Turn(
            stop_reason="refused" if stop == "refusal" else stop,
            tool_calls=tool_calls,
            text=text,
            usage=TurnUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            detail=_refusal_detail(resp) if stop == "refusal" else "",
            model=str(getattr(resp, "model", None) or requested_model),
            fallback=any(b.type == "fallback" for b in resp.content),
            raw=resp,
        )

    def append_assistant(self, history: list[dict[str, Any]], turn: Turn) -> list[dict[str, Any]]:
        return [*history, {"role": "assistant", "content": echo_content(turn.raw.content)}]

    def append_tool_results(
        self,
        history: list[dict[str, Any]],
        results: list[tuple[ToolCall, ToolOutcome]],
        notice: str = "",
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [outcome.block(call.id) for call, outcome in results]
        if notice:
            blocks.append({"type": "text", "text": notice})
        return [*history, {"role": "user", "content": blocks}]
