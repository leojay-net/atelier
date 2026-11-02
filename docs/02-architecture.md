# 2. Architecture

## Layered overview

Atelier has four layers. Each one depends only on the layers below it. **Layers 1–2 contain no AI** and are fully usable on their own through the CLI or HTTP API.

```
┌───────────────────────────────────────────────────────────────────┐
│ 4. App layer        Django · job queue · auth · web UI (later)    │
├───────────────────────────────────────────────────────────────────┤
│ 3. Agent layer      Claude Messages API · 4 tools · prompt cache  │
│                     · plan cache · budgets · telemetry            │
├───────────────────────────────────────────────────────────────────┤
│ 2. Engine           spec validation · DAG planner · runner ·      │
│                     result cache · checks · previews              │
├───────────────────────────────────────────────────────────────────┤
│ 1. Op library       image · video · audio · doc ops · presets ·   │
│                     recipes · pinned binaries & fonts             │
└───────────────────────────────────────────────────────────────────┘
          │                                          │
   Content-addressed store (local disk / S3)   Metadata DB (SQLite → Postgres)
```

### Why this layering matters

- **Testability:** layers 1–2 are pure functions of their inputs, so they can be tested exhaustively without spending tokens.
- **Zero-token paths:** a saved spec or recipe can run from the CLI, a cron job or an API call with no model involved.
- **Replaceable brain:** the agent layer is a thin client of the engine. Model upgrades, prompt changes or a different orchestration style never touch media code.

## Components

### 1. Op library (`atelier.ops`)
- Each **op** is a pure function: `(input artifacts, validated args, context) → output artifact + metadata`.
- Ops are registered with a decorator that declares a **Pydantic param model**, **version**, **determinism class**, **media types in and out** and a **summary formatter**.
- Grouped by domain: `image.*`, `video.*`, `audio.*`, `doc.*`, `pdf.*`, plus the cross-cutting `export`.
- **Presets** (`presets/*.yaml`) are versioned bundles of op arguments.
- **Recipes** (`recipes/*.yaml`) are versioned, parameterized pipeline templates.
- **Pinned resources:** static ffmpeg/ffprobe build, bundled font pack, ICC profiles and LUTs. Nothing depends on what happens to be installed on the host.

Full detail: [Engine & spec](03-engine-and-spec.md).

### 2. Engine (`atelier.engine`)

| Component | Responsibility |
|---|---|
| `spec.validate` | Parse the spec and check it against the op registry. Returns *actionable* errors (P7) |
| `planner` | Build a DAG from the steps, compute cache keys, find steps already cached, estimate cost and time |
| `runner` | Run uncached steps in topological order in isolated worker processes with resource limits |
| `cache` | Content-addressed result cache keyed by `sha256(engine version + op version + canonical args + input hashes + binary versions)` |
| `store` | Content-addressed artifact storage (`asset:<sha256>`) with a local-disk backend and an S3 backend |
| `checks` | Automated quality checks per media type (see [Verification](06-verification.md)) |
| `preview` | Makes cheap visual summaries: thumbnails, contact sheets, region crops, before/after strips, rendered doc pages |
| `inspect` | Compact metadata for any input (dimensions, duration, streams, pages, fonts, placeholders…) |

### 3. Agent layer (`atelier.agent`)
- Uses the official `anthropic` Python SDK against the **Claude Messages API** with **custom tools** (see [ADR-0003](adr/0003-claude-api-custom-loop.md)).
- 4 tools: `inspect_media`, `describe_op`, `run_pipeline`, `preview`.
- A frozen system prompt that includes the **compact op/preset/recipe catalogue**, cached with prompt caching.
- A **plan cache** that replays an exact repeat request on the same inputs with no model call.
- **Budgets:** per-job caps on turns, tokens and image count.
- **Telemetry:** usage fields logged for every request (input, output, cache read/write tokens).

Full detail: [Agent layer](04-agent-layer.md).

### 4. App layer (`atelier.api`)
- **Django** service (Django Ninja API, served over ASGI; see [ADR-0007](adr/0007-django-app-layer.md)) with these endpoints:
  - `POST /assets`: upload, returns `asset:<hash>`
  - `POST /jobs`: either `{spec}` (engine only, zero tokens) or `{prompt, assets}` (agent)
  - `GET /jobs/{id}`: status, checks, outputs, token usage
  - `GET /jobs/{id}/events`: SSE stream of progress
  - `GET /presets`, `GET /recipes`, `GET /ops`
- **Workers:** a separate process pool that runs engine jobs. Heavy ffmpeg work never blocks the API.
- **Queue:** chosen at the start of Phase 5 behind an `enqueue(job_id)` interface. The candidates are Django's tasks framework with a database-backed worker (single node) and Celery with Redis (scaled out).
- **Web UI (later phase):** upload, chat-style request, preview gallery, spec viewer and editor, one-click "save as recipe."

## Data flow: an agent job

```
1. User uploads clip.mp4                → store: asset:9c1e…
2. POST /jobs {prompt, assets}          → job queued
3. Agent turn 1 (system+catalogue cached)
     model → inspect_media(asset:9c1e…)
     tool  ← "video 00:48.2 · 1920x1080 · 29.97fps · h264/aac · stereo -19.8 LUFS · speech detected"
4. Agent turn 2
     model → run_pipeline({steps:[trim, captions.auto, reframe 9:16, export reels]})
     engine: validate → plan → run → checks
     tool  ← "ok asset:77ab… · 15.00s · 1080x1920 · 30fps · -14.0 LUFS · captions 23 lines in safe-area · checks 11/11"
5. Agent turn 3 → final answer (structured: outputs, summary)
Total: 3 model requests, 0 images sent, 2 tool calls.
```

When a check fails (for example, captions overlap a face region), the tool result includes the failing check and a hint. The agent can request a `preview` (contact sheet of the affected span) or fix the spec directly.

## Repository layout (target)

```
atelier/
├── pyproject.toml            # uv-managed, all deps pinned in uv.lock
├── uv.lock
├── docker/
│   └── Dockerfile            # reference platform: pinned ffmpeg, fonts, libs
├── src/atelier/
│   ├── spec/                 # Pydantic spec models, canonical JSON, validator, error formatting
│   ├── engine/               # planner, runner, cache, store, limits
│   ├── ops/
│   │   ├── registry.py       # @op decorator, determinism classes, catalogue export
│   │   ├── image/            # geometry, color, composite, text, export
│   │   ├── video/            # trim, concat, reframe, overlay, captions, speed, export
│   │   ├── audio/            # loudnorm, mix, fade, denoise, extract
│   │   ├── doc/              # docx, xlsx, pptx ops
│   │   ├── pdf/              # merge, split, stamp, forms, render
│   │   └── ffmpeg/           # filtergraph builder, argv builder, ffprobe parser
│   ├── presets/              # *.yaml (versioned)
│   ├── recipes/              # *.yaml (versioned)
│   ├── checks/               # per-domain quality checks
│   ├── preview/              # thumbnails, contact sheets, crops, page renders
│   ├── agent/
│   │   ├── loop.py           # Messages API loop, budgets, stop-reason handling
│   │   ├── tools.py          # tool schemas (strict) + dispatch into engine
│   │   ├── prompts/system.md # frozen system prompt template
│   │   ├── catalogue.py      # compact deterministic catalogue renderer
│   │   ├── plan_cache.py
│   │   └── telemetry.py
│   ├── plugins/              # optional ML ops (whisper, rembg, esrgan)
│   ├── api/                  # Django project (Phase 5): models, Ninja API, SSE
│   └── cli.py                # `atelier run spec.json`, `atelier inspect`, `atelier ask "…"`
├── assets/
│   ├── fonts/                # bundled, license-cleared font pack
│   ├── icc/                  # sRGB, Display P3, CMYK profiles
│   └── luts/
├── tests/
│   ├── unit/
│   ├── golden/               # bit-exact expected hashes (reference platform)
│   ├── perceptual/           # SSIM/PSNR-threshold tests
│   ├── fixtures/             # small sample media & docs
│   └── evals/                # agent task suite + graders
└── docs/
```

## Deployment shapes

| Shape | Use | Notes |
|---|---|---|
| **CLI** | Local power use, scripts, CI | `atelier run spec.json` / `atelier ask "…" --in a.mp4` |
| **Single container** | Small team / MVP | API + workers + SQLite + local store in one Docker image |
| **Scaled** | Production | API pods, worker pods (CPU; GPU pool for ML plugins), Postgres, S3, Redis queue |

The Docker image **is** the reference platform for determinism guarantees (see [Determinism](05-determinism.md#platform-scope)).
