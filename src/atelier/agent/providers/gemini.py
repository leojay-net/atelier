"""The Gemini adapter, against `google-genai`'s classic `generate_content` surface.

Verified against the installed SDK this session (not assumed from training): a research pass
had claimed a new "Interactions API" (`client.interactions.create()`) as the current
recommended path, but the installed package has no `interactions` module at all — `dir(genai)`
lists `batches, caches, chats, ..., models, ...` and `Models` exposes `generate_content` /
`generate_content_stream`, the long-established surface. `types.FinishReason` is the classic
enum (`STOP`, `MAX_TOKENS`, `SAFETY`, ...; `UNEXPECTED_TOOL_CALL` / `TOO_MANY_TOOL_CALLS` /
`MALFORMED_FUNCTION_CALL` are newer additions, but the generate_content shape itself is
unchanged). `FunctionDeclaration.parameters_json_schema` accepts raw JSON Schema directly, so
`TOOLS`' `input_schema` needs no conversion to Gemini's own `Schema` type.

`output_schema` (`FINAL_SCHEMA`) is intentionally not sent: Gemini's forced-JSON response mode
(`response_mime_type`/`response_schema` or `response_json_schema`) is documented as
incompatible with function calling in one request, and every request here carries `tools`.
The loop already tolerates a non-JSON final answer (`loop._conclude`'s `except ValueError`
fallback), so the system prompt's own instruction to answer in the final JSON shape is the only
enforcement for this adapter.

No thinking (Gemini's `thinking_config` is a different, non-adaptive budget knob; left unset),
no explicit prompt caching, no fallback-safety routing: those `Turn`/`AgentConfig` fields stay
inert here, same as the OpenAI-compatible adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from atelier.agent.providers.base import ToolCall, Turn, TurnUsage

if TYPE_CHECKING:
    from atelier.agent.tools import ToolOutcome

_FINISH_REASON = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refused",
    "PROHIBITED_CONTENT": "refused",
    "BLOCKLIST": "refused",
    "SPII": "refused",
    "RECITATION": "refused",
}


def _text_of(content: str | list[dict[str, Any]]) -> str:
    """A tool result's content, collapsed to plain text: Gemini's function response is a plain
    JSON object, unlike Claude's, which can carry an image block (e.g. from `preview`)."""
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


def _translate_tools(tools: list[dict[str, Any]]) -> list[Any]:
    from google.genai import types

    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters_json_schema=t["input_schema"],
                )
                for t in tools
            ]
        )
    ]


class GeminiAdapter:
    """Wraps a `google.genai.Client` as a `ModelAdapter`."""

    name = "gemini"

    def __init__(self, client: Any) -> None:
        self.client = client

    def initial_history(self, user_text: str) -> list[Any]:
        from google.genai import types

        return [types.Content(role="user", parts=[types.Part(text=user_text)])]

    def send(
        self,
        history: list[Any],
        *,
        system: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model: str,
        max_tokens: int,
        effort: str,
    ) -> Turn:
        from google.genai import types

        system_text = "\n\n".join(str(b.get("text", "")) for b in system)
        config = types.GenerateContentConfig(
            system_instruction=system_text,
            max_output_tokens=max_tokens,
            tools=_translate_tools(tools),
        )
        resp = self.client.models.generate_content(
            model=model, contents=list(history), config=config
        )
        return self._to_turn(resp, model)

    def _to_turn(self, resp: Any, requested_model: str) -> Turn:
        candidates = resp.candidates or []
        if not candidates:
            return Turn(stop_reason="error", detail="no candidates in response", raw=resp)
        candidate = candidates[0]
        finish = str(getattr(candidate.finish_reason, "value", candidate.finish_reason))
        parts = (candidate.content.parts if candidate.content else None) or []
        tool_calls = [
            ToolCall(
                id=p.function_call.id or f"call_{i}",
                name=p.function_call.name or "",
                input=dict(p.function_call.args or {}),
            )
            for i, p in enumerate(parts)
            if p.function_call is not None
        ]
        text = "".join(p.text for p in parts if p.text)
        stop_reason = "tool_use" if tool_calls else _FINISH_REASON.get(finish, "error")
        usage = resp.usage_metadata
        return Turn(
            stop_reason=stop_reason,
            tool_calls=tool_calls,
            text=text,
            usage=TurnUsage(
                input_tokens=(usage.prompt_token_count if usage else 0) or 0,
                output_tokens=(usage.candidates_token_count if usage else 0) or 0,
                cache_read_tokens=(usage.cached_content_token_count if usage else 0) or 0,
            ),
            detail=f"finish_reason {finish}" if stop_reason == "refused" else "",
            model=str(getattr(resp, "model_version", None) or requested_model),
            raw=resp,
        )

    def append_assistant(self, history: list[Any], turn: Turn) -> list[Any]:
        candidate = turn.raw.candidates[0]
        return [*history, candidate.content]

    def append_tool_results(
        self,
        history: list[Any],
        results: list[tuple[ToolCall, ToolOutcome]],
        notice: str = "",
    ) -> list[Any]:
        from google.genai import types

        # Matching back to a call is by name/position in the classic (non-streaming) API, not
        # a required id lookup, so echoing a synthesized id here (when Gemini didn't give one)
        # is harmless.
        parts = [
            types.Part(
                function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response={"result": _text_of(outcome.content)}
                )
            )
            for call, outcome in results
        ]
        grown = [*history, types.Content(role="user", parts=parts)]
        if notice:
            grown.append(types.Content(role="user", parts=[types.Part(text=notice)]))
        return grown
