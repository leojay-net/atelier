"""Agent settings. Model and effort are configuration, never hard-coded (docs/04 §4.1, §4.6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from atelier.agent.budget import BudgetLimits

DEFAULT_MODEL = "claude-opus-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Server-side refusal fallback, "default" form: the API picks the substitute model by
# refusal category. Only exists on `client.beta.messages` (docs/04 §4.5).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class AgentConfig:
    model: str = DEFAULT_MODEL
    max_tokens: int = 16_000
    # The SDK refuses non-streaming requests that could run past ~10 minutes (max_tokens
    # above ~21k), so the one `max_tokens` retry stays below that.
    max_tokens_retry: int = 20_000
    # Effort is pinned for a whole job (changing it mid-job invalidates the cached
    # messages), and lowered per route only after the evals show no quality drop.
    effort: str = "high"
    route_effort: Mapping[str, str] = field(default_factory=dict)
    fallbacks: bool = True
    limits: BudgetLimits = BudgetLimits()

    def __post_init__(self) -> None:
        for value in (self.effort, *self.route_effort.values()):
            if value not in EFFORTS:
                raise ValueError(f"effort {value!r} is not one of {'|'.join(EFFORTS)}")
        if self.max_tokens_retry < self.max_tokens:
            raise ValueError("max_tokens_retry must be at least max_tokens")

    def effort_for(self, route: str) -> str:
        return self.route_effort.get(route, self.effort)
