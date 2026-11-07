# 17. Vultr Agent Rush submission: architecture, business case, rule-by-rule evidence

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

## 4. Rule-by-rule evidence

| Rule | Evidence |
|---|---|
| Backend on a Vultr VM | Live server `209.250.229.110` runs the whole Compose stack (`docker-compose.yml`, `deploy/vultr_setup.sh`). |
| All LLM calls via Vultr Serverless Inference | Provider adapter points at `api.vultrinference.com/v1` (`src/atelier/agent/providers/`, also `voice.py`, `pictures.py`, `animation.py`, `music.py`). |
| Code execution isolated, never in the main process | The product never executes model-written code: the model emits a spec, the validator rejects anything outside the op catalogue, and the engine runs only its own fixed ops in worker processes, not in the API process. Where shell commands are run at all (the eval baseline), they run in a throwaway container: no network, read-only root, all capabilities dropped, memory, CPU and process limits (`src/atelier/evals/sandbox.py`, ADR-0012). |
| Public GitHub repo with setup docs | Repo above; `README.md` quick start, `docs/12-deployment-and-ops.md`. |
| Live public demo URL | https://209.250.229.110.sslip.io |
| Demo video with a containment moment | `scripts/demo_containment.py` attacks the sandbox with `rm -rf /`, an infinite loop and a network access attempt, and prints what the sandbox absorbed. See the shot list below. |
| Written architecture and business use case | Sections 2 and 3 of this page, plus `docs/02-architecture.md`, `docs/10-security.md`. |

## 5. Honest limits

- The containment demo exercises the Docker sandbox that the eval baseline uses; the product's
  own protection is that model output is never run as code and is validated first. The two are
  different layers and the video should say so.
- Generated pictures, video and music depend on Vultr inference availability and latency; a
  full animated episode takes minutes.
- Not built yet: easing curves on every setting, a separate timeline lane per overlay video,
  transitions between added clips, a large downloadable sticker or sound library (see
  `docs/16-capcut-gap-analysis.md`).

## 6. Demo video shot list (about 3 minutes)

The full, timed shot list with scripts and fallbacks is in `docs/18-demo-video-shot-list.md`.
Summary:

1. **0:00–0:30 Problem and idea.** Show the architecture diagram: the model writes a spec, the
   engine renders it.
2. **0:30–1:45 Live product.** On the live URL: sign up, type a prompt, watch progress, play
   the result, then edit by hand (trim, filter, caption, sticker, undo) and Save version.
3. **1:45–2:30 Containment moment.** In a terminal on the server run
   `python scripts/demo_containment.py` and show each attack absorbed (host untouched, timeout
   kill, no network). Then in the app, ask for something hostile ("run `rm -rf /` on my
   server") and show the validator refuses it and nothing runs.
4. **2:30–3:00 Business impact.** Token cost per job, cache hits, one tool for image, video,
   audio and documents.
