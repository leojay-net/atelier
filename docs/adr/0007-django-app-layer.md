# ADR-0007: Django for the app layer (instead of FastAPI)

- **Status:** Accepted
- **Date:** 2026-09-25

## Context
The original design named FastAPI for layer 4 (HTTP API, job queue, auth, web UI). No ADR recorded that choice. The team has decided to use Django. Layer 4 (Phase 5) needs orgs, users, API keys, per-org quotas and budgets, job history, recipes, brand kits, an operator back office, database migrations and, later, a web UI. Layers 1–3 (ops, engine, agent) must stay usable with no web framework: from the CLI, cron jobs and tests.

## Options considered
1. **FastAPI** (original plan)
   - Pros: async-native; Pydantic-native request and response models; small.
   - Cons: auth, an ORM with migrations, an admin UI, sessions and permissions all have to be assembled from separate libraries.
2. **Django**
   - Pros: ORM and migrations, auth and permissions, and an admin UI suited to orgs, quotas and recipes; mature ecosystem; the team's choice.
   - Cons: heavier; progress streaming (SSE) needs an ASGI deployment; Django request models are not Pydantic.

## Decision
Option 2, scoped to **layer 4 only**.

- **Dependency direction:** the Django project imports `atelier.engine`, `atelier.ops` and `atelier.agent`. Those packages never import Django. The CLI stays Django-free.
- **API:** Django Ninja. It is Pydantic-native, so the existing spec, op-param and tool schemas are reused directly and the OpenAPI schema is generated from them.
- **Metadata DB:** the Django ORM. SQLite for development and the single-container shape, Postgres for production (unchanged from the original plan). Models refer to assets by content hash. The content-addressed store remains the only place bytes live.
- **Serving:** ASGI (uvicorn), so `GET /jobs/{id}/events` can stream SSE.
- **Workers:** engine jobs still run in separate worker processes, and each step still runs in its own resource-limited subprocess (`atelier.engine.runner`). Web requests never run media work.
- **Queue:** chosen at the start of Phase 5, behind a small `enqueue(job_id)` interface. The candidates are Django's tasks framework with a database-backed worker (fewest moving parts for the single-container shape) and Celery with Redis (for the scaled shape). This replaces the earlier "SQLite queue → arq" plan, because arq is designed around asyncio services rather than Django.

## Consequences
- Phases 0–4 are unaffected.
- Admin, auth and quotas come mostly for free in Phase 5.
- The Django project lives beside the engine package, not inside it. Its location is decided when Phase 5 starts.
- README, [Architecture](../02-architecture.md) and [Roadmap](../08-roadmap.md) now say Django.
