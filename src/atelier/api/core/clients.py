"""The model adapter the worker uses for prompt jobs (replaceable via `ATELIER_MODEL_CLIENT`,
so tests never touch the network, and a deployer can point it at whichever provider they run).

Construction and credential handling for each provider live in `agent/providers/`'s registry
(shared with the CLI's `--provider` flag); these are just the dotted-path targets Django's
`ATELIER_MODEL_CLIENT` setting names.
"""

from __future__ import annotations

from atelier.agent.providers import build_adapter
from atelier.agent.providers.base import ModelAdapter
from atelier.engine.mirror import ObjectBackend, S3Backend


def anthropic_client() -> ModelAdapter:
    return build_adapter("anthropic")


def openai_client() -> ModelAdapter:
    return build_adapter("openai")


def ollama_client() -> ModelAdapter:
    return build_adapter("ollama")


def gemini_client() -> ModelAdapter:
    return build_adapter("gemini")


def vultr_client() -> ModelAdapter:
    return build_adapter("vultr")


def object_backend() -> ObjectBackend | None:
    """The durable object store to mirror each organization's store to, if one is configured."""
    from django.conf import settings

    if not settings.ATELIER_S3_BUCKET:
        return None
    return S3Backend(settings.ATELIER_S3_BUCKET, endpoint_url=settings.ATELIER_S3_ENDPOINT or None)
