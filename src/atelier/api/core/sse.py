"""Server-sent events for a job: `GET /api/v1/jobs/<id>/events`.

An async view, so a long stream holds no worker thread while it waits. It replays the job's stored
events from `Last-Event-ID` (or `?after=`), then follows new ones until the job ends. Browsers'
EventSource cannot send an Authorization header: use `fetch` and read the stream, which also
lets the API key stay out of URLs and logs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from asgiref.sync import sync_to_async
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase

from atelier.api.core import services
from atelier.api.core.auth import find_key
from atelier.api.core.models import Job, JobEvent

POLL = 0.4
KEEPALIVE = 15.0


def _bearer(request: HttpRequest) -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def _events_after(job_id: uuid.UUID, seen: int) -> list[JobEvent]:
    return list(JobEvent.objects.filter(job_id=job_id, seq__gt=seen))


def _frame(event: JobEvent) -> str:
    body = json.dumps(services.event_dict(event), default=str)
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {body}\n\n"


async def job_stream(request: HttpRequest, job_id: uuid.UUID) -> HttpResponseBase:
    key = await sync_to_async(find_key)(_bearer(request))
    if key is None:
        return JsonResponse(
            {"error": "unauthorized", "message": "send Authorization: Bearer <key>"}, status=401
        )
    exists = await Job.objects.filter(pk=job_id, org_id=key.org_id).aexists()
    if not exists:
        return JsonResponse(
            {"error": "not_found", "message": f"no job {job_id} in this organization"}, status=404
        )
    try:
        after = int(request.headers.get("Last-Event-ID") or request.GET.get("after") or 0)
    except ValueError:
        after = 0

    async def stream() -> AsyncIterator[str]:
        seen, idle = after, 0.0
        while True:
            events = await sync_to_async(_events_after)(job_id, seen)
            for event in events:
                seen = event.seq
                yield _frame(event)
            status = await Job.objects.filter(pk=job_id).values_list("status", flat=True).aget()
            if status in Job.FINISHED and not events:
                yield "event: end\ndata: {}\n\n"
                return
            if events:
                idle = 0.0
            else:
                idle += POLL
                if idle >= KEEPALIVE:
                    idle = 0.0
                    yield ": keepalive\n\n"
            await asyncio.sleep(POLL)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
