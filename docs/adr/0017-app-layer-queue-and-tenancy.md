# ADR-0017: The application layer: a database queue, per-organization stores, keys, events

- **Status:** Accepted
- **Date:** 2026-09-26 (decided under delegated authority)
- **Builds on:** [ADR-0007](0007-django-app-layer.md) (Django, Ninja, ASGI, a queue behind `enqueue`), [ADR-0009](0009-warm-standby-workers.md)

## Context
ADR-0007 fixed the framework and left the queue for the start of Phase 5. The layer needs to be
runnable as one container and scale out later, keep one organization's files and hashes away from
another's, let a separately built web UI drive it, and show progress without holding a thread per
viewer.

## Decisions
1. **The queue is the database.** `enqueue(job)` is the whole interface (`api/core/queue.py`). In the
   default mode a job is a `queued` row; worker processes (`manage.py run_worker`) claim the oldest with an
   atomic `UPDATE … WHERE status = 'queued'`, so any number of workers can share one database (SQLite for a
   single machine, Postgres beyond) with no broker, and two workers can never run one job. `inline` mode runs a
   job in the calling process, for tests. Celery or Redis would replace only this module. A worker
   heartbeats through the job's events; a running job silent for 15 minutes is failed by the next worker's
   reaper ("run the job again") instead of hanging forever.
2. **Web requests never run media work.** `POST /jobs` validates a spec (a bad spec is refused up front with
   every error, and nothing is queued), checks the month's allowance, records the job and enqueues it. Each
   step still runs in its own sandboxed subprocess inside the worker, exactly as from the CLI.
3. **One store per organization.** An organization's engine is opened on its own directory
   (`<home>/api/orgs/<slug>`): its content-addressed store, result cache, telemetry and plan cache. An asset
   handle from another organization does not resolve there (a 404, and a spec using it fails validation), and
   identical bytes are deduplicated only inside one organization, so hashes never reveal that another
   organization holds a file. The database says who has what (`Asset`); the store holds the bytes.
4. **API keys, not sessions.** `Authorization: Bearer atk_<prefix>_<secret>`. Only the SHA-256 is stored; the
   token is shown once, at creation. Keys belong to an organization and can be revoked. The operator back
   office is the Django admin (sessions), which is a separate surface.
5. **Progress is stored events.** The engine runner and the agent loop call an optional `observer`
   (`plan`, `step_start`, `step_done`, `turn`, `tools`); the worker turns each into a numbered `JobEvent`.
   Clients poll `GET /jobs/{id}/events.json?after=<seq>` or read the async SSE stream `GET /jobs/{id}/events`,
   which replays from `Last-Event-ID` and follows until the job ends. The same observer stops a job between
   two steps when a cancel was requested (a step already running is not interrupted).
6. **Quotas are counted from the jobs.** A month's usage is the sum of `units` (billed-token equivalents:
   uncached 1x, cache write 1.25x, cache read 0.1x, output 5x, the measure the evals use) and
   `compute_seconds` over that month's jobs. A new job is refused (429, `quota_exceeded`, with the reset
   date) once either limit is spent; a running job is never cut off for it.
7. **Brand kits ride in the request, never in the system prompt.** The organization's colors, fonts, logo
   and tagline are added to each prompt job's text, and the logo assets are attached, so the frozen system
   prompt (and its cache) is shared by every organization.
8. **Saved recipes are specs with inputs as parameters.** "Save as recipe" stores the canonical full-form spec
   of a job; running it supplies handles for some or all of its inputs and the rest keep the saved ones (a
   logo, a template). Saving under an existing name makes a new version.
9. **Downloads are hostile until proven otherwise.** File names are reduced to a safe set; only images, PDF,
   MP4 and MP3 are shown inline, everything else is an attachment; every download carries
   `nosniff` and `Content-Security-Policy: sandbox; default-src 'none'`.
10. **A UI on another origin** works through `ATELIER_CORS_ORIGINS` (an explicit list; nothing by default).
    The API is described by the generated OpenAPI schema at `/api/v1/openapi.json`.

## Consequences
- A stopped worker never loses a job: it stays `queued`, or is failed by the reaper if it had started.
- A cancel of a running job waits for the current step to end.
- Workers need real files, so each node keeps a local working copy. With `ATELIER_S3_BUCKET` set, every
  organization's store is a `MirroredStore` (`engine/mirror.py`): each object (and its sidecar) is uploaded
  under `<prefix>/<org>/objects/…` when it is created and fetched, verified against its hash, the first time
  another node needs it, so the API and workers need no shared filesystem. Objects are immutable, so there is
  nothing to invalidate; result caches stay per node (a miss just recomputes). Any S3-compatible service works
  through `ATELIER_S3_ENDPOINT`; the object backend is a four-method protocol.
- A key may start a fixed number of jobs per minute (`ATELIER_JOBS_PER_MINUTE`); reads and uploads are not
  rate-limited yet, and the operator admin's static files are not served by the container image.
- SQLite serializes writers, so use Postgres for more than a few concurrent workers. Postgres has not been run
  under load here; the ORM code is portable but untested against it.
