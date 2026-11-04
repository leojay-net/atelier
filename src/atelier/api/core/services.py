"""What the API and the worker do with the data: stores, assets, usage, quotas, events.

Everything here is scoped by organization. An organization's engine is opened on its own home
directory, so its store, cache and telemetry are separate from every other organization's:
asset handles from another org do not resolve, and identical content is deduplicated only
within one org.
"""

from __future__ import annotations

import dataclasses
import importlib
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from atelier.api.core.models import Asset, Job, JobEvent, Org
from atelier.engine.engine import Engine
from atelier.engine.mirror import MirroredStore, ObjectBackend
from atelier.engine.runner import JobResult, RunLimits
from atelier.media import AssetInfo, MediaType

HEARTBEAT_EVERY = 5.0


class ApiError(Exception):
    """An error with an HTTP status and a stable code, rendered as JSON by the API."""

    def __init__(
        self, status: int, code: str, message: str, errors: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.errors = status, code, message, errors or []


# -- engines and stores -------------------------------------------------------------------------


def org_home(org: Org) -> Path:
    return Path(settings.ATELIER_STORE_ROOT) / org.slug


def _backend() -> ObjectBackend | None:
    module, _, name = settings.ATELIER_OBJECT_BACKEND.rpartition(".")
    factory: Callable[[], ObjectBackend | None] = getattr(importlib.import_module(module), name)
    return factory()


def engine_for(org: Org) -> Engine:
    """The organization's own engine (its store, cache and limits). With an object backend
    configured, its store is mirrored there under the organization's own prefix."""
    engine = Engine.open(
        org_home(org), run_limits=RunLimits(wall_seconds=float(settings.ATELIER_JOB_WALL_SECONDS))
    )
    backend = _backend()
    if backend is None:
        return engine
    store = MirroredStore(
        org_home(org) / "store", backend, f"{settings.ATELIER_S3_PREFIX}/{org.slug}"
    )
    return dataclasses.replace(engine, store=store)


# -- assets -------------------------------------------------------------------------------------

_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def clean_filename(name: str, fallback: str) -> str:
    """A download name that cannot break a header or a path."""
    base = _NAME.sub("_", Path(name or "").name).strip(" .")[:120]
    return base or fallback


def register_asset(
    org: Org, info: AssetInfo, filename: str = "", origin: Job | None = None
) -> Asset:
    asset, _ = Asset.objects.get_or_create(
        org=org,
        hash=info.hash,
        defaults={
            "media": info.media.value,
            "ext": info.ext,
            "size": info.size,
            "filename": clean_filename(filename, f"{info.hash[:12]}{info.ext}"),
            "origin_job": origin,
        },
    )
    return asset


def upload_limit(org: Org) -> int:
    return int(org.max_upload_bytes or settings.ATELIER_MAX_UPLOAD_BYTES)


def asset_dict(asset: Asset) -> dict[str, Any]:
    return {
        "handle": asset.handle,
        "media": asset.media,
        "ext": asset.ext,
        "size": asset.size,
        "filename": asset.filename,
        "created_at": asset.created_at,
    }


def known_media(media: str) -> bool:
    return media not in (MediaType.BINARY.value,)


# -- usage and quotas ---------------------------------------------------------------------------


def month_start(now: datetime | None = None) -> datetime:
    now = now or timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def usage(org: Org, now: datetime | None = None) -> dict[str, Any]:
    since = month_start(now)
    totals = Job.objects.filter(org=org, created_at__gte=since).aggregate(
        units=Sum("units"), seconds=Sum("compute_seconds")
    )
    jobs = Job.objects.filter(org=org, created_at__gte=since).count()
    return {
        "period_start": since,
        "jobs": jobs,
        "units": float(totals["units"] or 0.0),
        "compute_seconds": float(totals["seconds"] or 0.0),
        "units_limit": org.monthly_units_limit,
        "compute_seconds_limit": org.monthly_compute_seconds_limit,
    }


def check_rate(key: Any) -> None:
    """At most `ATELIER_JOBS_PER_MINUTE` new jobs per API key per minute."""
    limit = int(settings.ATELIER_JOBS_PER_MINUTE)
    if limit <= 0 or key is None:
        return
    since = timezone.now() - timedelta(seconds=60)
    if Job.objects.filter(created_by=key, created_at__gte=since).count() >= limit:
        raise ApiError(
            429,
            "rate_limited",
            f"this key has started {limit} jobs in the last minute; wait a moment and try again",
        )


def check_quota(org: Org) -> None:
    """Refuse a new job when the month's allowance is already spent."""
    used = usage(org)
    if org.monthly_units_limit is not None and used["units"] >= org.monthly_units_limit:
        raise ApiError(
            429,
            "quota_exceeded",
            f"this organization has used its {org.monthly_units_limit:,} model units for the "
            "month; it resets on the first of the month",
        )
    if (
        org.monthly_compute_seconds_limit is not None
        and used["compute_seconds"] >= org.monthly_compute_seconds_limit
    ):
        raise ApiError(
            429,
            "quota_exceeded",
            f"this organization has used its {org.monthly_compute_seconds_limit:,.0f} compute "
            "seconds for the month; it resets on the first of the month",
        )


# -- events and results -------------------------------------------------------------------------


def emit(job: Job, kind: str, data: Mapping[str, Any] | None = None) -> JobEvent:
    """Append a progress event (numbered from 1) and refresh the job's heartbeat."""
    with transaction.atomic():
        last = JobEvent.objects.filter(job=job).aggregate(m=Max("seq"))["m"] or 0
        event = JobEvent.objects.create(job=job, seq=last + 1, kind=kind, data=dict(data or {}))
        Job.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now())
    return event


def event_dict(event: JobEvent) -> dict[str, Any]:
    return {"seq": event.seq, "kind": event.kind, "data": event.data, "at": event.created_at}


def summarize_result(result: JobResult) -> dict[str, Any]:
    """A finished run as plain data: outputs with summaries, per-output checks, step timings."""
    return {
        "outputs": {
            out_id: {
                "handle": rep.handle,
                "media": rep.media.value,
                "ext": rep.ext,
                "size": rep.size,
                "summary": rep.summary,
            }
            for out_id, rep in result.outputs.items()
        },
        "checks": {
            out_id: [
                {"name": c.name, "status": c.status.value, "message": c.message, "hint": c.hint}
                for c in checks
            ]
            for out_id, checks in result.checks.items()
        },
        "steps": [
            {
                "id": s.id,
                "op": s.op,
                "cached": s.cached,
                "seconds": round(s.seconds, 3),
                "fused": list(s.fused),
            }
            for s in result.steps
        ],
        "passed": result.passed,
        "seconds": round(result.seconds, 3),
    }


def job_dict(job: Job, *, events: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "prompt": job.prompt,
        "assets": job.assets,
        "recipe": job.recipe.name if job.recipe else None,
        "parent": str(job.parent_id) if job.parent_id else None,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "units": job.units,
        "compute_seconds": job.compute_seconds,
    }
    if job.kind == "spec" or job.finished:
        out["spec"] = job.spec
    if events:
        out["events"] = [event_dict(e) for e in job.events.all()]
    return out
