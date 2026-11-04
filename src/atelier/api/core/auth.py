"""API key authentication: `Authorization: Bearer atk_<prefix>_<secret>`.

The request gets `request.org` and `request.api_key`. Keys are compared by SHA-256 through a
unique index, so a wrong key costs one lookup and reveals nothing about which prefixes exist.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils import timezone
from ninja.security import HttpBearer

from atelier.api.core.models import KEY_PREFIX, ApiKey

_TOUCH_EVERY = 300  # seconds between last_used_at writes for one key


def find_key(token: str) -> ApiKey | None:
    if not token.startswith(f"{KEY_PREFIX}_"):
        return None
    key = ApiKey.objects.select_related("org", "user").filter(key_hash=ApiKey.digest(token)).first()
    if key is None or not key.active:
        return None
    from atelier.api.core.accounts import session_expired

    if session_expired(key):
        return None
    now = timezone.now()
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() > _TOUCH_EVERY:
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)
    return key


class ApiKeyAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Any:
        key = find_key(token)
        if key is None:
            return None
        request.org = key.org  # type: ignore[attr-defined]
        request.api_key = key  # type: ignore[attr-defined]
        return key
