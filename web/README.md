# Atelier web

A Next.js UI for the Atelier API (`docs/11-api.md`). Upload a file, describe what you want or
run a spec directly, watch the job run live, then browse assets, job history and saved recipes.

## How it talks to the API

Every request to Django goes through this app's own API routes
(`app/api/proxy/[...path]/route.ts` and `app/api/proxy/stream/[id]/route.ts`), which attach the
real `Authorization` header server-side. The bearer key lives only in `.env.local` and is never
sent to the browser (enforced at build time: `lib/server/djangoFetch.ts` and `lib/api.ts` both
carry the `server-only` import, so a client component importing them fails to build).

## Running it locally

Three processes, from the repository root unless noted:

```sh
# 1. database and a key (once)
uv run python manage.py migrate
uv run python manage.py create_org acme "Acme Studio"   # prints an API key once, copy it

# 2. the API
ATELIER_DEBUG=1 uv run uvicorn atelier.api.asgi:application

# 3. a worker (or set ATELIER_QUEUE=inline on the API process instead, to skip this and run
#    jobs synchronously inside the request, fine for solo local use)
uv run python manage.py run_worker
```

Then, in `web/`:

```sh
cp .env.local.example .env.local   # fill in the key from step 1
npm install
npm run dev
```

Open `http://localhost:3000`.

## Notes

- No API key management UI: the Django API has none (keys are CLI-only,
  `manage.py create_api_key`). The Keys page shows the configured key's identity and usage, with
  the CLI commands to rotate one.
- "Describe" posts a natural-language prompt to the agent (costs tokens). "Spec" (behind an
  Advanced disclosure) runs an engine spec directly at zero cost; there is no generated form for
  it, since `GET /ops` returns a compact text catalogue, not a JSON Schema.
- Job progress prefers Server-Sent Events and falls back to polling automatically if the stream
  drops; this is invisible in the UI, both feed the same event log.

## Not built here

A `Dockerfile`/compose service for this app alongside the repository's existing
`docker-compose.yml`, and an automated frontend test suite. Reasonable follow-ups, not part of
this build.
