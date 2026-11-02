"""The job queue, behind one small interface (ADR-0007, ADR-0017).

`enqueue(job)` is all the API knows. In the default `database` mode a job is just a `queued` row,
and worker processes (`manage.py run_worker`) claim rows with an atomic update, so any number of
workers can share one database with no broker. `inline` runs the job at once in the calling
process (tests and one-shot scripts). Swapping in Celery or Redis means replacing this module.
"""

from __future__ import annotations

from django.conf import settings

from atelier.api.core.models import Job


def enqueue(job: Job) -> None:
    if settings.ATELIER_QUEUE == "inline":
        from atelier.api.core.worker import execute

        execute(job.pk)
    # "database": nothing to do, the row is the message
