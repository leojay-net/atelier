"""Email + password accounts for the web UI.

Each person who signs up gets their own organization (a private workspace: its own assets, jobs,
recipes and brand kit; the API already scopes everything by organization) and is its owner.
Signing in issues an ordinary API key named `SESSION_KEY_NAME`, which the web UI keeps in an
httpOnly cookie and sends on the user's behalf; signing out revokes it. A session key stops
working `SESSION_DAYS` after it was issued.

No email verification, by design (demo deployment): the email is just the login name.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import Any

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    MinimumLengthValidator,
    NumericPasswordValidator,
    UserAttributeSimilarityValidator,
    validate_password,
)
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from atelier.api.core.models import ApiKey, Member, Org
from atelier.api.core.services import ApiError

SESSION_KEY_NAME = "web session"
SESSION_DAYS = 30
MAX_FAILED_LOGINS = 10  # per email, then a pause of LOCKOUT_SECONDS
LOCKOUT_SECONDS = 15 * 60

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VALIDATORS = [
    UserAttributeSimilarityValidator(),
    MinimumLengthValidator(8),
    CommonPasswordValidator(),
    NumericPasswordValidator(),
]


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 150 or not _EMAIL.match(email):
        raise ApiError(400, "invalid_email", "enter a valid email address")
    return email


def session_expired(key: ApiKey) -> bool:
    return key.name == SESSION_KEY_NAME and timezone.now() - key.created_at > timedelta(
        days=SESSION_DAYS
    )


def _session(user: Any, org: Org) -> dict[str, Any]:
    _, token = ApiKey.issue(org, SESSION_KEY_NAME, user=user)
    return {
        "token": token,
        "expires_in": SESSION_DAYS * 86400,
        "user": {"email": user.email},
        "org": {"slug": org.slug, "name": org.name},
    }


def signup(email: str, password: str, password_confirm: str) -> dict[str, Any]:
    email = normalize_email(email)
    if password != password_confirm:
        raise ApiError(400, "password_mismatch", "the passwords do not match")
    User = get_user_model()
    candidate = User(username=email, email=email)
    try:
        validate_password(password, candidate, password_validators=_VALIDATORS)
    except ValidationError as e:
        raise ApiError(400, "weak_password", " ".join(e.messages)) from None
    try:
        with transaction.atomic():
            if User.objects.filter(username=email).exists():
                raise ApiError(409, "email_taken", "an account with this email already exists")
            user = User.objects.create_user(username=email, email=email, password=password)
            org = Org.objects.create(
                slug=f"u-{secrets.token_hex(6)}", name=f"{email.split('@')[0]}'s workspace"
            )
            Member.objects.create(org=org, user=user, role="owner")
    except IntegrityError:  # two signups for the same email at once
        raise ApiError(409, "email_taken", "an account with this email already exists") from None
    return _session(user, org)


def login(email: str, password: str) -> dict[str, Any]:
    email = normalize_email(email)
    failures_key = f"login-failures:{email}"
    if (cache.get(failures_key) or 0) >= MAX_FAILED_LOGINS:
        raise ApiError(
            429, "too_many_attempts", "too many failed sign-ins; try again in 15 minutes"
        )
    user = authenticate(username=email, password=password)
    if user is None:
        cache.set(failures_key, (cache.get(failures_key) or 0) + 1, LOCKOUT_SECONDS)
        raise ApiError(401, "invalid_credentials", "the email or password is incorrect")
    cache.delete(failures_key)
    member = Member.objects.select_related("org").filter(user=user).order_by("pk").first()
    if member is None:  # an account made outside signup (e.g. createsuperuser): give it a home
        org = Org.objects.create(
            slug=f"u-{secrets.token_hex(6)}", name=f"{email.split('@')[0]}'s workspace"
        )
        member = Member.objects.create(org=org, user=user, role="owner")
    return _session(user, member.org)


def logout(key: ApiKey) -> None:
    """Revoke the session key the request came with (an ordinary API key is left alone)."""
    if key.name == SESSION_KEY_NAME and key.revoked_at is None:
        ApiKey.objects.filter(pk=key.pk).update(revoked_at=timezone.now())
