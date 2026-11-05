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


HEIF_EXTS = (".heic", ".heif", ".hif")


def normalize_upload(engine: Any, info: Any, filename: str) -> tuple[Any, str]:
    """iPhone photos arrive as HEIC, which the image ops cannot decode: store a JPEG instead
    (orientation and color kept), named like the original. Anything else is returned as is."""
    if info.ext.lower() not in HEIF_EXTS:
        return info, filename
    import io

    import pillow_heif
    from PIL import Image, ImageOps

    from atelier.media import MediaType

    pillow_heif.register_heif_opener()
    try:
        with Image.open(engine.store.path(info.hash)) as im:
            icc = im.info.get("icc_profile")
            rgb = ImageOps.exif_transpose(im).convert("RGB")
            out = io.BytesIO()
            extra = {"icc_profile": icc} if icc else {}
            rgb.save(out, "JPEG", quality=92, **extra)
    except Exception as e:  # any decoder failure means "unreadable"
        raise ApiError(
            415, "unreadable_media", "this iPhone photo could not be read; it may be damaged"
        ) from e
    converted = engine.store.put_bytes(out.getvalue(), media=MediaType.IMAGE, ext=".jpg")
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return converted, f"{stem or 'photo'}.jpg"


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


# -- library and conversations (the web UI's "Library" and recent chats) ------------------------

DOCUMENT_MEDIA = ("pdf", "docx", "pptx", "xlsx", "text")


def job_title(job: Job) -> str:
    """The AI's title for what a job made, else the first words of the request."""
    title = str((job.result or {}).get("title") or "").strip()
    if title:
        return title
    words = re.sub(r"https?://\S+", "", job.prompt or "").split()
    text = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
    return (text[:1].upper() + text[1:]) if text else "Untitled"


def output_handles(job: Job) -> list[str]:
    return [o["handle"] for o in ((job.result or {}).get("outputs") or {}).values()]


def latest_in_thread(job: Job) -> Job:
    """Follow the newest follow-up down to the end of the conversation."""
    seen = 0
    while seen < 100 and (nxt := job.followups.order_by("-created_at").first()) is not None:
        job, seen = nxt, seen + 1
    return job


def thread_jobs(end: Job) -> list[Job]:
    """Every job in the conversation ending at `end`, newest first."""
    chain, node = [], end
    for _ in range(100):
        if node is None:
            break
        chain.append(node)
        node = node.parent
    return chain


def delete_chat_files(org: Org, chain: list[Job]) -> int:
    """Delete what the jobs in `chain` made: their results and the extras made or found for
    them. Uploads are kept, and so is anything another chat still uses. Returns the count."""
    ids = {j.pk for j in chain}
    made = {h.removeprefix("asset:") for j in chain for h in output_handles(j)}
    made |= set(Asset.objects.filter(org=org, origin_job__in=ids).values_list("hash", flat=True))
    used = set()
    for other in Job.objects.filter(org=org, chat_deleted=False).exclude(pk__in=ids):
        used |= {h.removeprefix("asset:") for h in [*output_handles(other), *(other.assets or [])]}
    mine = (org.my_voice or {}).get("handle", "").removeprefix("asset:")
    doomed = list(  # an upload has no origin job: it is the user's own file, never deleted here
        Asset.objects.filter(org=org, hash__in=made - used - {mine}, origin_job__isnull=False)
    )
    store = engine_for(org).store
    for a in doomed:
        store.path(a.hash).unlink(missing_ok=True)
        a.delete()
    return len(doomed)


def thread_title(job: Job) -> str:
    """A conversation's name: the name the user gave it, else the newest AI title in it, else
    its first request."""
    named = next((j.chat_title for j in thread_jobs(job) if j.chat_title), "")
    if named:
        return named
    node: Job | None = job
    first = job
    for _ in range(100):
        if node is None:
            break
        if (node.result or {}).get("title"):
            return str(node.result["title"])
        first, node = node, node.parent
    return job_title(first)


def _extra_title(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    if stem.startswith("voiceover"):
        return "AI voiceover"
    for prefix, label in (("made-", "AI picture"), ("found-", "Photo from the web")):
        if stem.startswith(prefix):
            words = stem[len(prefix) :].replace("-", " ").strip()
            return f"{label}: {words}" if words else label
    return filename or "File"


def library(org: Org, section: str, media: str, limit: int, offset: int) -> dict[str, Any]:
    """Every asset of the organization in one of three sections: `created` (what a request
    delivered), `uploaded` (what people uploaded) and `extras` (what the AI fetched or made on
    the way: found photos, generated pictures, voiceovers, linked files)."""
    assets = Asset.objects.filter(org=org).select_related("origin_job").order_by("-created_at")
    outputs: dict[str, Job] = {}
    for job in Job.objects.filter(org=org).exclude(result=None).only("id", "result", "prompt"):
        for handle in output_handles(job):
            outputs.setdefault(handle.removeprefix("asset:"), job)
    counts = {"created": 0, "uploaded": 0, "extras": 0}
    items: list[dict[str, Any]] = []
    for a in assets:
        kind = (
            "created" if a.hash in outputs else "uploaded" if a.origin_job_id is None else "extras"
        )
        counts[kind] += 1
        if kind != section:
            continue
        wanted = media in ("", "all") or (
            a.media in DOCUMENT_MEDIA if media == "document" else a.media == media
        )
        if not wanted:
            continue
        job = outputs.get(a.hash) or a.origin_job
        item = asset_dict(a)
        if kind == "created" and job is not None:
            item["title"] = thread_title(job)  # "Music, this one" is a reply, not a name
        elif kind == "extras":
            item["title"] = _extra_title(a.filename)
        else:
            item["title"] = a.filename or a.handle[:18]
        item["job"] = str(job.id) if job is not None else None
        items.append(item)
    return {"counts": counts, "count": len(items), "items": items[offset : offset + limit]}


def conversations(org: Org, limit: int = 50) -> list[dict[str, Any]]:
    """Each conversation once, newest first, by its latest job (prompt jobs with no follow-up)."""
    ends = (
        Job.objects.filter(org=org, kind="prompt", followups__isnull=True, chat_deleted=False)
        .order_by("-created_at")
        .select_related("parent")[:limit]
    )
    out = []
    for job in ends:
        preview, node = None, job
        for _ in range(100):
            if node is None:
                break
            handles = output_handles(node)
            if handles:
                outs = (node.result or {}).get("outputs") or {}
                first = next(iter(outs.values()))
                preview = {"handle": first["handle"], "media": first.get("media")}
                break
            node = node.parent
        out.append(
            {
                "id": str(job.id),
                "title": thread_title(job),
                "status": job.status,
                "updated_at": job.created_at,
                "preview": preview,
            }
        )
    return out


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
    ops_by_id = {st.id: st.op.name for st in result.spec.steps}
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
                # the ops of the fused steps, so a client can say what the whole run did
                "fused_ops": [ops_by_id.get(f, "") for f in s.fused],
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
        "voice": job.voice,
    }
    if job.kind == "spec" or job.finished:
        out["spec"] = job.spec
    if events:
        out["events"] = [event_dict(e) for e in job.events.all()]
    return out
