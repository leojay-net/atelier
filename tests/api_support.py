"""Test doubles for the application layer: the model client the worker will ask for."""

from __future__ import annotations

from typing import Any

model: Any = None  # set by a test; the worker calls `model_client()` for every prompt job


def model_client() -> Any:
    """A `ModelAdapter`, matching the real factories in `clients.py`: tests set `model` to a
    raw `MessagesClient` fake (`ScriptedModel`, or one raising `MissingCredentials`), and this
    wraps it in `AnthropicAdapter` exactly as `clients.anthropic_client()` does."""
    if model is None:
        raise AssertionError("the test did not set api_support.model")
    from atelier.agent.providers.anthropic import AnthropicAdapter

    return AnthropicAdapter(model)


backend: Any = None  # set by a test to make every organization's store mirror somewhere


def object_backend() -> Any:
    return backend


voice: Any = None  # set by a test to a fake VoiceService; None means "no voice service"


def voice_client() -> Any:
    return voice
