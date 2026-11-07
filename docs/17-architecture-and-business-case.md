# 17. Architecture, business case and operating evidence

**Live demo:** https://209.250.229.110.sslip.io (sign up with any email; no card)
**Repository:** https://github.com/leojay-net/atelier

## 1. What Atelier is

Atelier turns a plain-language request ("make a 30 s birthday reel for Tolu from these photos,
with music and captions") into a finished video, image, audio file or document, and then lets
the user keep editing it by hand in a CapCut-style workspace (timeline, layers, filters, stickers,
captions, undo/redo) or by asking the AI again.

The idea that makes it cheap and safe: **the model decides *what* to do; a deterministic engine
decides *how*.** The model never writes code or shell commands and never touches file bytes. It
writes a short JSON spec (about 200 tokens). A validator checks every op, argument and file
handle, and the engine renders it the same way every time, with results cached by content hash.

## 2. Architecture

```
Browser (Next.js workspace)  ──►  nginx (TLS)  ──►  web (Next.js)  ──►  api (Django, ASGI)
                                                                          │  jobs, auth, quotas
                                                       Vultr Serverless   ▼
                                                       Inference    ◄──  worker x2 (agent + engine)
                                                       (all LLM, image,        │  validate → plan → run → check
                                                        video, music calls)    ▼
                                                                       /data (content-addressed assets)
                                                                       optional: Vultr Object Storage
```

- **Vultr Cloud Compute VM** runs everything with Docker Compose: `nginx`, `web`, `api`,
  `worker` (two replicas), `certbot`.
- **Vultr Serverless Inference** (`https://api.vultrinference.com/v1`) serves every model call:
  the director (LLM), pictures, video clips, music and voices.
- **Vultr Object Storage** (S3-compatible) is supported as the asset store.
- **Engine:** ffmpeg (pinned) for video/audio, Pillow for images, python-docx, openpyxl,
  python-pptx and pypdf for documents; faster-whisper for captions, RNNoise for denoise.
- Every manual edit in the workspace saves as a spec through the same pipeline (`POST
  /jobs/{id}/edit`), so hand edits and AI edits are versioned, cached and checked identically.

## 3. Business use case

Small businesses, creators and agencies need weekly social video, product shots, decks and
documents but cannot afford an editor or a monthly seat for every tool. Atelier gives them:

- **Prompt to finished piece** (script, voice, music, pictures, captions, sound effects) in
  one request, then **fine control** in a normal editor, so nobody is stuck with the AI's
  first attempt.
- **Predictable cost:** a spec is about 200 tokens instead of a coding agent generating and
  debugging ffmpeg scripts, and repeat renders are cache hits that cost no tokens.
- **Safe by construction:** nothing the model produces is executed as code.
- **Beyond one tool:** the same service edits images, video, audio and Office/PDF documents.

## 4. Where it runs and how it is isolated

| Property | How it is met |
|---|---|
| Backend on a Vultr VM | The whole stack runs on one Vultr Cloud Compute VM under Docker Compose (`docker-compose.yml`, `deploy/vultr_setup.sh`). |
| Every model call through Vultr Serverless Inference | The provider adapters point at `api.vultrinference.com/v1` (`src/atelier/agent/providers/`, `voice.py`, `pictures.py`, `animation.py`, `music.py`). |
| Durable storage | Every organisation's content-addressed store is mirrored to Vultr Object Storage (S3-compatible) when a bucket is configured (`src/atelier/engine/mirror.py`). |
| Model output is never executed | The model emits a spec. The validator rejects anything outside the operation catalogue, and the engine runs only its own fixed operations in worker processes, never in the API process, each under CPU, memory and file-size limits. |
| Hostile shell commands are contained | Where shell commands are run at all (the evaluation baseline), they run in a throwaway container: no network, read-only root, all capabilities dropped, memory, CPU and process limits (`src/atelier/evals/sandbox.py`, ADR-0012). `scripts/verify_isolation.py` attacks that sandbox with `rm -rf /`, an infinite loop, a fork bomb and a network access attempt and prints what it absorbed. |
| Public, repeatable deployment | Live at https://209.250.229.110.sslip.io. Setup is in `README.md` and `docs/12-deployment-and-ops.md`. |
| Architecture and security notes | `docs/02-architecture.md`, `docs/10-security.md`. |

## 5. Honest limits

- The throwaway-container sandbox covers shell commands (the evaluation baseline and the
  verification script). The product's own protection is that model output is never run as code
  and is validated first. They are two layers, and `docs/13-live-user-test-2026-11-04.md`
  explains the difference in production.
- Generated pictures, video and music depend on Vultr inference availability and latency; a
  full animated episode takes minutes.
- Not built yet: easing curves on every setting, a separate timeline lane per overlay video,
  transitions between added clips, a large downloadable sticker or sound library (see
  `docs/16-capcut-gap-analysis.md`).
