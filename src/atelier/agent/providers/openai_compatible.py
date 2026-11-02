"""The OpenAI Chat Completions adapter: covers OpenAI itself, a locally-run Ollama server (its
`/v1` endpoint mirrors this exact shape), and any other OpenAI-compatible server (vLLM, LM
Studio, ...).

Deliberately targets the older, stable Chat Completions API rather than OpenAI's newer
Responses API: Ollama's compatibility endpoint mirrors Chat Completions, not Responses, so
targeting Chat Completions is what lets one adapter cover both OpenAI and every
OpenAI-compatible local server, which is the whole point of this module.

No thinking, no explicit prompt caching (OpenAI's is automatic server-side, surfaced only in
usage numbers, not a parameter this adapter needs to set), no fallback-safety routing: those
`AgentConfig`/Turn fields are simply inert here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from atelier.agent.providers.base import ToolCall, Turn, TurnUsage

if TYPE_CHECKING:
    from atelier.agent.tools import ToolOutcome

_FINISH_REASON = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",  # deprecated OpenAI alias for tool_calls
    "length": "max_tokens",
    "content_filter": "refused",
}


def _text_of(content: str | list[dict[str, Any]]) -> str:
    """A tool result's content, collapsed to plain text: OpenAI's tool-result messages are
    text-only, unlike Claude's, which can carry an image block (e.g. from `preview`)."""
    if isinstance(content, str):
        return content
    parts = []
    had_image = False
    for block in content:
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "image":
            had_image = True
    if had_image:
        parts.append("[a preview image was generated; this model does not see tool images]")
    return "\n".join(parts) if parts else "(no output)"


def _translate_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
                "strict": t.get("strict", False),
            },
        }
        for t in tools
    ]


class OpenAICompatibleAdapter:
    """Wraps an `openai.OpenAI`-shaped client (works for real OpenAI and, pointed at a custom
    `base_url`, for Ollama or any other OpenAI-compatible server)."""

    name = "openai"

    def __init__(self, client: Any) -> None:
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
        system_text = "\n\n".join(str(b.get("text", "")) for b in system)
        messages = [{"role": "system", "content": system_text}, *history]
        # Credential absence is caught at client construction time (`clients.py`), not here:
        # the OpenAI SDK's `OpenAI()` constructor itself raises `OpenAIError` immediately when
        # no key is configured, verified against the installed SDK (openai 3.28.0) this session.
        resp = self.client.chat.completions.create(
            model=model,
            # Chat Completions' long-standing parameter name; some newer reasoning models
            # (o1/o3-class) want `max_completion_tokens` instead. Not verified against a
            # live account this session; if a real deployment hits a 400 for this specific
            # field, that is the first thing to check.
            max_tokens=max_tokens,
            messages=messages,
            tools=_translate_tools(tools),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "final_answer",
                    "schema": output_schema,
                    "strict": True,
                },
            },
        )
        return self._to_turn(resp, model)

    def _to_turn(self, resp: Any, requested_model: str) -> Turn:
        choice = resp.choices[0]
        message = choice.message
        finish = str(choice.finish_reason)
        stop_reason = _FINISH_REASON.get(finish, "error")
        tool_calls = [
            ToolCall(id=c.id, name=c.function.name, input=_json_loads(c.function.arguments))
            for c in (message.tool_calls or [])
        ]
        usage = getattr(resp, "usage", None)
        cached = 0
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0 if details else 0
        return Turn(
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            text=message.content or "",
            usage=TurnUsage(
                input_tokens=(usage.prompt_tokens if usage else 0) or 0,
                output_tokens=(usage.completion_tokens if usage else 0) or 0,
                cache_read_tokens=cached,
            ),
            detail="" if stop_reason != "refused" else "content filtered",
            model=str(getattr(resp, "model", None) or requested_model),
            raw=resp,
        )

    def append_assistant(self, history: list[dict[str, Any]], turn: Turn) -> list[dict[str, Any]]:
        message = turn.raw.choices[0].message
        entry: dict[str, Any] = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in message.tool_calls
            ]
        return [*history, entry]

    def append_tool_results(
        self,
        history: list[dict[str, Any]],
        results: list[tuple[ToolCall, ToolOutcome]],
        notice: str = "",
    ) -> list[dict[str, Any]]:
        messages = [
            {"role": "tool", "tool_call_id": call.id, "content": _text_of(outcome.content)}
            for call, outcome in results
        ]
        if notice:
            messages.append({"role": "user", "content": notice})
        return [*history, *messages]


def _json_loads(text: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
