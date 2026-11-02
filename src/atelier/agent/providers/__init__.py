"""The provider registry: name -> `ModelAdapter` factory.

The one place both the Django worker (`api/core/clients.py`, selected via the
`ATELIER_MODEL_CLIENT` setting) and the CLI (`agent/commands.py`'s `--provider`) go to build an
adapter, so credential handling and SDK construction for each provider live in exactly one
spot. Each branch imports its SDK lazily; a deployment only needs the optional extra
(`openai` or `gemini`, `pyproject.toml`) for the provider it actually uses.
"""

from __future__ import annotations

import os

from atelier.agent.client import MissingCredentials
from atelier.agent.providers.anthropic import AnthropicAdapter
from atelier.agent.providers.base import ModelAdapter

PROVIDERS = ("anthropic", "openai", "gemini", "ollama")


def build_adapter(provider: str, *, fallbacks: bool = True) -> ModelAdapter:
    """`fallbacks` (Claude's server-side safety re-routing) only means anything for
    `"anthropic"`; the other providers have no equivalent and ignore it."""
    if provider == "anthropic":
        from atelier.agent.client import AnthropicMessages

        return AnthropicAdapter(AnthropicMessages(fallbacks=fallbacks))
    if provider == "openai":
        return _openai_adapter()
    if provider == "ollama":
        return _ollama_adapter()
    if provider == "gemini":
        return _gemini_adapter()
    raise ValueError(f"unknown provider {provider!r}; choose one of {PROVIDERS}")


def _openai_adapter() -> ModelAdapter:
    from atelier.agent.providers.openai_compatible import OpenAICompatibleAdapter

    try:
        import openai
    except ImportError:
        raise MissingCredentials(
            "the OpenAI provider needs the 'openai' extra: install atelier[openai]"
        ) from None
    try:
        client = openai.OpenAI()
    except openai.OpenAIError as e:
        raise MissingCredentials(f"no OpenAI credentials: set OPENAI_API_KEY ({e})") from None
    return OpenAICompatibleAdapter(client)


def _ollama_adapter() -> ModelAdapter:
    """A locally-run Ollama server, reached through its OpenAI-compatible `/v1` endpoint — no
    API key needed. `OLLAMA_BASE_URL` defaults to Ollama's default local address."""
    from atelier.agent.providers.openai_compatible import OpenAICompatibleAdapter

    try:
        import openai
    except ImportError:
        raise MissingCredentials(
            "the Ollama provider needs the 'openai' extra: install atelier[openai]"
        ) from None
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    client = openai.OpenAI(base_url=base_url, api_key="ollama")  # no real key needed
    return OpenAICompatibleAdapter(client)


def _gemini_adapter() -> ModelAdapter:
    from atelier.agent.providers.gemini import GeminiAdapter

    try:
        from google import genai
    except ImportError:
        raise MissingCredentials(
            "the Gemini provider needs the 'gemini' extra: install atelier[gemini]"
        ) from None
    try:
        client = genai.Client()
    except ValueError as e:
        raise MissingCredentials(f"no Gemini credentials: set GEMINI_API_KEY ({e})") from None
    return GeminiAdapter(client)
