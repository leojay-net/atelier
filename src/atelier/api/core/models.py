"""The application's data: organizations, keys, assets, jobs and their progress events.

Bytes never live here. An `Asset` row says "this organization has the object with this hash";
the content-addressed store (one per organization, so one org's hashes never reveal another's
content) holds the bytes. A `Job` records what was asked and how it ended.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone

KEY_PREFIX = "atk"


class Org(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True, max_length=60)  # also the name of its store directory
    created_at = models.DateTimeField(default=timezone.now)
    # Limits per calendar month (UTC); null means unlimited.
    monthly_units_limit = models.BigIntegerField(null=True, blank=True)  # agent cost units
    monthly_compute_seconds_limit = models.FloatField(null=True, blank=True)
    max_upload_bytes = models.BigIntegerField(null=True, blank=True)  # null: the server default
    brand_kit = models.JSONField(default=dict, blank=True)
    # the user's own recorded voice for voiceovers: {handle, text, created_at} (consented)
    my_voice = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return self.slug


class Member(models.Model):
    ROLES = (("owner", "owner"), ("member", "member"))
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLES, default="member")

    class Meta:
        unique_together = (("org", "user"),)


class ApiKey(models.Model):
    """A bearer credential: `atk_<prefix>_<secret>`. Only its SHA-256 is stored."""

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="keys")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    name = models.CharField(max_length=80)
    prefix = models.CharField(max_length=12)  # shown in lists so a key can be recognized
    key_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def issue(cls, org: Org, name: str, user: Any = None) -> tuple[ApiKey, str]:
        """Create a key; the full token is returned once and never stored."""
        prefix = secrets.token_hex(4)
        token = f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(32)}"
        key = cls.objects.create(
            org=org,
            user=user,
            name=name,
            prefix=f"{KEY_PREFIX}_{prefix}",
            key_hash=cls.digest(token),
        )
        return key, token

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix}…)"


class Asset(models.Model):
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="assets")
    hash = models.CharField(max_length=64)
    media = models.CharField(max_length=12)
    ext = models.CharField(max_length=12, blank=True)
    size = models.BigIntegerField()
    filename = models.CharField(max_length=200, blank=True)  # the uploaded name, for downloads
    created_at = models.DateTimeField(default=timezone.now)
    origin_job = models.ForeignKey(
        "Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        unique_together = (("org", "hash"),)
        indexes = [models.Index(fields=["org", "-created_at"])]

    @property
    def handle(self) -> str:
        return f"asset:{self.hash}"


class Job(models.Model):
    QUEUED, RUNNING, SUCCEEDED, CHECKS_FAILED = "queued", "running", "succeeded", "checks_failed"
    NEEDS_INPUT, FAILED, CANCELLED = "needs_input", "failed", "cancelled"
    STATUSES = (QUEUED, RUNNING, SUCCEEDED, CHECKS_FAILED, NEEDS_INPUT, FAILED, CANCELLED)
    FINISHED = (SUCCEEDED, CHECKS_FAILED, NEEDS_INPUT, FAILED, CANCELLED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="jobs")
    created_by = models.ForeignKey(ApiKey, null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=8, choices=(("spec", "spec"), ("prompt", "prompt")))
    status = models.CharField(max_length=14, default=QUEUED, db_index=True)
    spec = models.JSONField(null=True, blank=True)
    prompt = models.TextField(blank=True)
    assets = models.JSONField(default=list, blank=True)  # handles the request is about
    recipe = models.ForeignKey("SavedRecipe", null=True, blank=True, on_delete=models.SET_NULL)
    # A follow-up ("refine this"): the job whose request, spec and result this one continues.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="followups"
    )
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    units = models.FloatField(default=0.0)  # agent cost in billed-token equivalents
    compute_seconds = models.FloatField(default=0.0)
    voice = models.CharField(max_length=40, blank=True)  # the voiceover voice the user chose
    chat_title = models.CharField(max_length=120, blank=True)  # the user's name for the chat
    chat_deleted = models.BooleanField(default=False)  # removed from the chat list

    class Meta:
        indexes = [models.Index(fields=["org", "-created_at"])]

    @property
    def finished(self) -> bool:
        return self.status in self.FINISHED

    def touch(self, when: datetime | None = None) -> None:
        self.heartbeat_at = when or timezone.now()
        self.save(update_fields=["heartbeat_at"])


class JobEvent(models.Model):
    """One line of a job's progress, numbered so a client can resume after `seq`."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    kind = models.CharField(max_length=24)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("job", "seq"),)
        ordering = ["seq"]


class SavedRecipe(models.Model):
    """A pipeline an organization saved to run again on new assets (docs/03 §3.6, "save as recipe").

    `template` is a full-form spec. Its inputs are the recipe's parameters: a run supplies
    handles for some or all of them; the rest keep the handles saved here (a logo, a template).
    """

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="recipes")
    name = models.SlugField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    template = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(ApiKey, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    archived = models.BooleanField(default=False)

    class Meta:
        unique_together = (("org", "name"),)


class Character(models.Model):
    """A member of the user's animation cast: a character or a place, with its design picture
    (the reference every animated scene is made from) and, for characters, a voice."""

    KINDS = (("character", "character"), ("place", "place"))

    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="characters")
    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=10, choices=KINDS, default="character")
    look = models.TextField()
    voice = models.CharField(max_length=400, blank=True)
    image = models.CharField(max_length=80)  # the design picture's asset handle
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["org", "name"])]
