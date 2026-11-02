"""The provider-agnostic turn shape and adapter interface (docs/04, multi-provider agent).

A `ModelAdapter` owns everything about one provider's wire format: building a request, reading
back a normalized `Turn`, and representing/growing the conversation history in whatever shape
that provider needs (so Claude's adapter can keep echoing full content blocks, including
thinking blocks, exactly as before, while a simpler adapter just grows a plain message list).
`loop.py`'s control flow only ever touches `Turn` and this Protocol; it never reads a provider's
raw response directly, and a provider's raw response never leaves its own adapter module except
as the opaque `Turn.raw`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from atelier.agent.tools import ToolOutcome


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class TurnUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    # 0 for every provider without a real equivalent to Claude's explicit prompt caching.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Turn:
    """One normalized model turn.

    `stop_reason` keeps Claude's full vocabulary ("tool_use", "end_turn", "max_tokens",
    "refused", "pause_turn", "error") so its existing loop branches are untouched; a
    non-Anthropic adapter only ever produces a subset of these, collapsing its own finish
    reasons into it (a content filter becomes "refused", anything unrecognized becomes "error").
    """

    stop_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""  # the final answer's text when stop_reason == "end_turn"
    usage: TurnUsage = field(default_factory=TurnUsage)
    detail: str = ""  # refusal category / error message
    model: str = ""  # the model that actually answered (may differ from the request on fallback)
    fallback: bool = False  # True only when Claude's server-side safety fallback fired
    raw: Any = None  # the adapter's own response object; opaque outside it


class ModelAdapter(Protocol):
    name: str

    def initial_history(self, user_text: str) -> Any:
        """The first user turn, in this provider's own history representation."""
        ...

    def send(
        self,
        history: Any,
        *,
        system: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model: str,
        max_tokens: int,
        effort: str,
    ) -> Turn:
        """One request/response round trip. `effort` is meaningful only to adapters that
        support it (Claude's adaptive thinking); others ignore it."""
        ...

    def append_assistant(self, history: Any, turn: Turn) -> Any:
        """Grow history with the assistant's turn, so the next request includes it."""
        ...

    def append_tool_results(
        self, history: Any, results: list[tuple[ToolCall, ToolOutcome]], notice: str = ""
    ) -> Any:
        """Grow history with every tool's result from one assistant turn. `notice`, when given
        (a budget warning), is an aside the model should read alongside the tool results, not a
        tool result itself."""
        ...
