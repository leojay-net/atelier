"""The operator back office (Django admin): organizations, quotas, keys and job history."""

from __future__ import annotations

from typing import Any

from django.contrib import admin, messages
from django.http import HttpRequest

from atelier.api.core.models import ApiKey, Asset, Job, JobEvent, Member, Org, SavedRecipe


class MemberInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Member
    extra = 0


@admin.register(Org)
class OrgAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("slug", "name", "monthly_units_limit", "monthly_compute_seconds_limit")
    inlines = [MemberInline]


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "prefix", "org", "created_at", "last_used_at", "revoked_at")
    readonly_fields = ("prefix", "key_hash", "created_at", "last_used_at")
    exclude = ("key_hash",)

    def save_model(self, request: HttpRequest, obj: ApiKey, form: Any, change: bool) -> None:
        if change:
            super().save_model(request, obj, form, change)
            return
        key, token = ApiKey.issue(obj.org, obj.name, obj.user)  # the token is shown once
        obj.pk, obj.prefix, obj.key_hash = key.pk, key.prefix, key.key_hash
        messages.warning(request, f"Copy this key now, it will not be shown again: {token}")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "org", "kind", "status", "created_at", "units", "compute_seconds")
    list_filter = ("status", "kind", "org")
    readonly_fields = tuple(f.name for f in Job._meta.fields)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("hash", "org", "media", "size", "filename", "created_at")


admin.site.register(SavedRecipe)
admin.site.register(JobEvent)
