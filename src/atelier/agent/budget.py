"""Per-job budgets (docs/04 §4.5): a soft limit that tells the model to wrap up, and a hard
limit that stops the loop and returns the best completed output.

Turns are API requests. The hard limit is checked between requests, never in the middle of
executing tool calls: engine work costs no tokens, so a final `run_pipeline` that was already
requested still runs and can still become the job's result.
"""

from __future__ import annotations

from dataclasses import dataclass

SOFT_OUTPUT_FRACTION = 0.75


@dataclass(frozen=True)
class BudgetLimits:
    max_turns: int = 6
    max_output_tokens: int = 12_000
    max_images: int = 3


@dataclass
class Budget:
    limits: BudgetLimits
    turns: int = 0
    output_tokens: int = 0
    images: int = 0

    def charge(self, output_tokens: int) -> None:
        self.turns += 1
        self.output_tokens += output_tokens

    def image_allowed(self) -> bool:
        return self.images < self.limits.max_images

    @property
    def hard_exhausted(self) -> bool:
        return (
            self.turns >= self.limits.max_turns
            or self.output_tokens >= self.limits.max_output_tokens
        )

    @property
    def soft_exhausted(self) -> bool:
        """True when the next request is the last one the budget allows."""
        return (
            self.turns >= self.limits.max_turns - 1
            or self.output_tokens >= self.limits.max_output_tokens * SOFT_OUTPUT_FRACTION
        )

    def notice(self) -> str:
        return (
            f"budget: {self.turns}/{self.limits.max_turns} turns used. "
            "Finish now with your best result; make no further tool calls unless one is "
            "required to deliver it."
        )
