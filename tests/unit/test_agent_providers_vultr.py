"""Unit tests for the Vultr Serverless Inference provider adapter."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from atelier.agent.client import MissingCredentials
from atelier.agent.providers import build_adapter
from atelier.agent.providers.openai_compatible import OpenAICompatibleAdapter


def test_vultr_adapter_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    monkeypatch.delenv("VULTR_API_KEY", raising=False)
    with pytest.raises(MissingCredentials, match="no Vultr credentials"):
        build_adapter("vultr")


def test_vultr_adapter_builds_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "test-vultr-api-key")
    adapter = build_adapter("vultr")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert str(adapter.client.base_url).rstrip("/") == "https://api.vultrinference.com/v1"
    assert adapter.client.api_key == "test-vultr-api-key"
    assert adapter.client.max_retries == 5  # rides out brief endpoint blips


def test_vultr_adapter_custom_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VULTR_API_KEY", "test-key-2")
    monkeypatch.setenv("VULTR_INFERENCE_BASE_URL", "https://custom.vultr.test/v1")
    adapter = build_adapter("vultr")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert str(adapter.client.base_url).rstrip("/") == "https://custom.vultr.test/v1"
    assert adapter.client.api_key == "test-key-2"
