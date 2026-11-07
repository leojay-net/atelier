# Atelier

> **atelier** */a.tə.lje/* (French): a workshop or studio. It comes from Old French *astelier*, "carpenter's workshop," from *astele*, "plank of wood." It is the room where craftspeople turn raw material into finished work with precise tools.

Atelier is a **deterministic media engine with an AI director on top**. It edits images, video, audio and documents (Word, Excel, PowerPoint, PDF) using proven libraries (Pillow, ffmpeg, python-docx, openpyxl, pypdf…). An agent (Claude by default, or OpenAI, Gemini, or a locally-run Ollama model) turns plain-language requests into **small, validated edit specs** that the engine runs exactly the same way every time.

The core idea:

> **The model decides *what* to do. The engine decides *how*, and does it the same way every time.**

This split is what lets Atelier use far fewer tokens than a general-purpose coding agent while producing *more* consistent results. The model never writes code, never runs shell commands and never handles raw file bytes. It only writes a short JSON spec and looks at small previews when a check needs it.

---

## Status

Built and deployed for the Vultr Agent Rush hackathon. Live demo: **https://209.250.229.110.sslip.io**. Judges: start with [`docs/17-submission.md`](docs/17-submission.md) (architecture, business case, rule-by-rule evidence, demo shot list).

- **Prompt to finished piece:** video (with voice, music, captions, sound effects), animated characters, images, audio, Word/Excel/PowerPoint/PDF, through an AI director whose output is a small validated spec.
- **A CapCut-style workspace** (`web/`): history, media, timeline with layers, filters, stickers, auto captions, fades and transitions, audio clean-up, undo/redo, and an AI assistant, all saving through the same spec pipeline.
- **Runs on Vultr:** a Vultr VM (Docker Compose), Vultr Serverless Inference for every model call, optional Vultr Object Storage.
- **Safe by construction:** the model never runs code; specs are validated and rendered by a deterministic engine; shell commands (eval baseline only) run in a throwaway no-network container.

The original engine milestones and the token-savings plan are in [`docs/08-roadmap.md`](docs/08-roadmap.md); the CapCut gap list is [`docs/16-capcut-gap-analysis.md`](docs/16-capcut-gap-analysis.md).

```sh
uv sync
uv run pytest -m "not slow"
uv run atelier ops                                   # catalogue: ops, presets, recipes
uv run atelier inspect tests/fixtures/photo.jpg
echo '{"recipe":"batch_thumbnails","params":{"title":"Big news"}}' > spec.json
uv run atelier run spec.json --in in=tests/fixtures/photo.jpg   # rerun → served from cache
uv run atelier batch spec.json --each "in=photos/*.jpg" --out thumbs/
```

With an API key (`ANTHROPIC_API_KEY`) the agent turns a plain-language request into a spec, runs it and checks the result. These commands spend tokens:

```sh
uv run atelier ask "Link preview card titled 'Big news'" tests/fixtures/photo.jpg
uv run atelier ask "Watermark these with our logo, subtle, for the web" logo.png --each "photos/*.jpg"
uv run atelier stats                                 # tokens, turns, cache-read ratio, first-pass rate
uv run atelier eval run --split validation           # the eval suite (add --suite video or --suite docs)
uv run atelier eval report eval-report.json --gate   # free: summarize a saved run + the exit gate
```

No Anthropic key? Pass `--provider openai` (`OPENAI_API_KEY`), `--provider gemini`
(`GEMINI_API_KEY`), or `--provider ollama --model <name>` for a local, no-key model served by
[Ollama](https://ollama.com) (`uv sync --extra openai` / `--extra gemini` first). The HTTP API
selects a provider the same way, deployment-wide, via the `ATELIER_MODEL_CLIENT` setting
([docs/04 §4.1.1](docs/04-agent-layer.md#411-provider-choice-a-modeladapter-seam)).

The application layer is an HTTP API ([`docs/11-api.md`](docs/11-api.md)): organizations, API keys, uploads, jobs with live progress, quotas, saved recipes and a brand kit.

```sh
uv run python manage.py migrate
uv run python manage.py create_org acme "Acme Studio"          # prints an API key once
ATELIER_SECRET_KEY=$(openssl rand -hex 32) uv run uvicorn atelier.api.asgi:application
uv run python manage.py run_worker                              # in another terminal
```

## Documentation map

Read the docs in this order:

| # | Document | What it covers |
|---|----------|----------------|
| 1 | [Vision & principles](docs/01-vision-and-principles.md) | The problem, the approach, and the seven design rules everything else follows |
| 2 | [Architecture](docs/02-architecture.md) | Layers, components, data flow, repository layout |
| 3 | [Engine & pipeline spec](docs/03-engine-and-spec.md) | The edit-spec format, op contract, op catalogue, presets, recipes |
| 4 | [Agent layer](docs/04-agent-layer.md) | How Claude is used: tools, system prompt, caching, loop, budgets |
| 5 | [Determinism](docs/05-determinism.md) | What "deterministic" means here and how each media type achieves it |
| 6 | [Verification & previews](docs/06-verification.md) | Automated quality checks and the "escalation ladder" for visual review |
| 7 | [Token economics](docs/07-token-economics.md) | Where tokens go, how Atelier saves them, how we measure it |
| 8 | [Roadmap](docs/08-roadmap.md) | Phased delivery plan with exit criteria |
| 9 | [Testing strategy](docs/09-testing.md) | Golden, perceptual, property and agent-eval testing |
| 10 | [Security](docs/10-security.md) | Untrusted media, sandboxing, prompt injection, resource limits |
| 11 | [HTTP API](docs/11-api.md) | Endpoints, keys, jobs, progress events, quotas, running the service |
| — | [Architecture Decision Records](docs/adr/) | Why each major choice was made |

## At a glance

```
 User: "Cut 0:05–0:20, add captions, export for Reels"
          │
          ▼
 ┌──────────────────────┐   compact spec (~200 tokens)
 │  Agent (Claude API)  │ ─────────────────────────────┐
 │  4 tools, cached     │ ◄──── one-line results ──────┤
 └──────────────────────┘                              ▼
                                        ┌──────────────────────────────┐
                                        │ Engine (pure Python, no AI)   │
                                        │ validate → plan → run → check │
                                        │ ffmpeg · Pillow · docx · pdf  │
                                        └──────────────────────────────┘
                                                        │
                                          out.mp4 + check report
```

## Tech stack (summary)

- **Language:** Python 3.11+ with a locked environment (`uv`)
- **Images:** Pillow (core), pyvips (large images), OpenCV (analysis)
- **Video/audio:** pinned static ffmpeg/ffprobe 7.x, driven through Atelier's own filtergraph builder
- **Documents:** python-docx, docxtpl, openpyxl, python-pptx, pypdf/pikepdf, pypdfium2 (rendering)
- **Agent:** official Anthropic Python SDK (`anthropic`), Claude Messages API with custom tools
- **App:** Django (Django Ninja API, ASGI) + background job workers + content-addressed storage ([ADR-0007](docs/adr/0007-django-app-layer.md))
- **Optional ML plugins:** faster-whisper (transcription), rembg (background removal), Real-ESRGAN (upscaling)

See [ADR index](docs/adr/README.md) for the reasoning behind each choice.
