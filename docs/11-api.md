# 11. HTTP API

The application layer ([ADR-0007](adr/0007-django-app-layer.md), [ADR-0017](adr/0017-app-layer-queue-and-tenancy.md)) is a Django service around the engine and the agent. The API is JSON over HTTPS at `/api/v1/`; the generated OpenAPI schema is at `/api/v1/openapi.json` (interactive docs at `/api/v1/docs`).

## Running it
```sh
uv run python manage.py migrate
uv run python manage.py create_org acme "Acme Studio" --units 2000000   # prints an API key once
ATELIER_SECRET_KEY=... uv run uvicorn atelier.api.asgi:application       # the API
uv run python manage.py run_worker                                       # one or more workers
```
Or `docker compose up` (see `docker-compose.yml`). Settings are environment variables: see `atelier/api/settings.py`. To run the API and workers on different machines, set `ATELIER_S3_BUCKET` (and `ATELIER_S3_ENDPOINT` for a compatible service such as MinIO) so every organization's store is mirrored there, and point `ATELIER_DB_*` at Postgres; install the `s3` extra (`boto3`).

## Authentication
`Authorization: Bearer atk_<prefix>_<secret>`. Keys belong to an organization; every endpoint sees only that organization's assets, jobs, recipes and brand kit. Issue and revoke keys with `manage.py create_api_key`. Errors are JSON: `{"error": "<code>", "message": "...", "errors": [...]}`.

## Endpoints
| | |
|---|---|
| `POST /auth/signup` `{email, password, password_confirm}` | No key needed. Creates an account and its own private organization; `201 {token, expires_in, user, org}`. `400` `invalid_email` / `password_mismatch` / `weak_password`, `409` `email_taken` |
| `POST /auth/login` `{email, password}` | No key needed. `200 {token, expires_in, user, org}`: a session key (an API key named `web session`, valid 30 days). `401` `invalid_credentials`; `429` after 10 failures for one email (15 min) |
| `POST /auth/logout` | Revokes the session key used for the call (`204`); other API keys are unaffected |
| `GET /me` | The organization and key you are using, and the user for a session key |
| `GET /ops`, `GET /ops/{name}` | The catalogue (ops, presets, recipes) and the detail of one, as the agent reads them |
| `POST /validate` `{spec}` | `{valid, steps, runs, cached, plan}` or `{valid: false, errors: [...]}`; runs nothing |
| `POST /assets` (multipart `file`) | Upload. `201 {handle, media, ext, size, filename}`; identical bytes give the same handle. `413` too large, `415` unsupported |
| `GET /assets`, `GET /assets/{handle}` | List; one asset with a description and its probed metadata |
| `GET /assets/{handle}/content` | Download (inline for images, PDF, MP4, MP3; otherwise an attachment) |
| `POST /jobs` | Start a job with **one of** `{spec}` (engine only, zero tokens), `{prompt, assets: [...]}` (the agent) or `{recipe_id, inputs: {name: handle}}`. `202` with the job. `422` an invalid spec with every error, `429` allowance spent |
| `GET /jobs?status=&limit=&offset=`, `GET /jobs/{id}` | History; one job with its events |
| `GET /jobs/{id}/events.json?after=<seq>` | Progress events after `seq`, for polling |
| `GET /jobs/{id}/events` | The same as server-sent events; resumes from `Last-Event-ID` |
| `POST /jobs/{id}/cancel` | Now if waiting; between two steps if running |
| `GET /recipes`, `POST /recipes`, `DELETE /recipes/{id}` | Saved recipes (a full-form spec whose inputs are its parameters) |
| `POST /jobs/{id}/save-recipe` `{name, description}` | "Save as recipe" from a finished job |
| `GET /brand-kit`, `PUT /brand-kit` | Logo, colors, bundled fonts, name and tagline; sent with every prompt job |
| `GET /usage` | This month's jobs, model units, compute seconds and the limits |

## A job
```json
{"id": "…", "kind": "spec|prompt", "status": "queued|running|succeeded|checks_failed|needs_input|failed|cancelled",
 "result": {"outputs": {"out": {"handle": "asset:…", "media": "image", "ext": ".jpg", "size": 48213, "summary": "…"}},
            "checks": {"out": [{"name": "…", "status": "pass|warn|fail", "message": "", "hint": null}]},
            "steps": [{"id": "…", "op": "…", "cached": false, "seconds": 0.4, "fused": []}],
            "passed": true, "seconds": 1.2, "summary": "…", "warnings": [], "question": ""},
 "error": "", "units": 0.0, "compute_seconds": 1.2, "spec": {…}}
```
`needs_input` means the agent asked a question (`result.question`); answer by starting a new job. `checks_failed` finished but a check still fails; the hints say how to fix it. Output handles are assets: download them from `/assets/{handle}/content`.

## Progress events
`{seq, kind, data, at}` with kinds `started`, `plan` (`groups`, `steps`), `step_start` and `step_done` (`step`, `op`, `index`, `groups`, `cached`, `seconds`, `summary`), `turn` and `tools` (agent jobs), and `finished` (`status`). The stream ends with an `end` event once the job has finished and everything was sent.

A browser `EventSource` cannot send the `Authorization` header. Read the stream with `fetch` and a body reader (`Last-Event-ID` works the same way), which also keeps the key out of URLs and logs.

## Limits and safety
Uploads are limited per organization (default 200 MB); office files and PDFs are checked before any library parses them ([ADR-0016](adr/0016-document-engine.md)); downloads are served with `nosniff` and a sandbox CSP. Each API key may start `ATELIER_JOBS_PER_MINUTE` jobs a minute (default 60; `429 rate_limited`). Use `ATELIER_CORS_ORIGINS` when a web UI is served from another origin.
