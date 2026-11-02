"""The one seam between the loop and the network.

The loop only calls `MessagesClient.create(request)`, so tests script the model with a fake
that returns real SDK message objects, and nothing else in the agent layer imports the
`anthropic` SDK. The SDK is imported lazily, when a real client is built.
"""

from __future__ import annotations

from typing import Any, Protocol

from atelier.agent.config import FALLBACK_BETA


class MissingCredentials(Exception):
    """No API key or login profile was found. Raised before any request is sent."""


class MessagesClient(Protocol):
    def create(self, request: dict[str, Any]) -> Any:
        """Send one Messages API request; return the SDK's message object."""


class AnthropicMessages:
    """`MessagesClient` backed by the Anthropic SDK.

    With fallbacks on, requests go through `client.beta.messages` (the only surface that has
    the `fallbacks` parameter) and a safety-classifier decline is re-run server-side on the
    model Anthropic recommends for that refusal category, instead of failing the job.
    """

    def __init__(self, client: Any | None = None, *, fallbacks: bool = True) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.fallbacks = fallbacks

    def create(self, request: dict[str, Any]) -> Any:
        try:
            if self.fallbacks:
                return self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **request
                )
            return self.client.messages.create(**request)
        except TypeError as e:
            # The SDK reports missing credentials as a bare TypeError, at request-build time.
            if "authentication method" in str(e):
                raise MissingCredentials(
                    "no Anthropic credentials: set ANTHROPIC_API_KEY, or run `ant auth login`"
                ) from None
            raise
