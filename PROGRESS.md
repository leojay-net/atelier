# Progress

Running log of implementation against [docs/08-roadmap.md](docs/08-roadmap.md). Newest first within each phase.

## Vultr Agent Rush Hackathon Track

**Target:** [Lablab.ai Vultr: Agent Rush Hackathon](https://lablab.ai/ai-hackathons/vultr-hackathon) (Nov 3 – 8, 2026)  
**Goal:** Maximize Vultr Cloud infrastructure utilization across compute, inference, object storage, and secure sandboxing, ensuring 100% adherence to all competition requirements and scoring criteria.

### Vultr Infrastructure Matrix
| Vultr Component | Role in Atelier | Status |
|---|---|---|
| **Vultr Cloud Compute (VM)** | Central orchestrator hosting ASGI API, background media workers, and Next.js 16 Web UI | ✅ Automated deploy script & compose ready |
| **Vultr Serverless Inference** | Powers AI director loop via `https://api.vultrinference.com/v1` (`llama-3.3-70b-instruct`) | ✅ Adapter implemented & unit tested |
| **Vultr Object Storage** | S3-compatible content-addressed storage for media assets, inputs, and deliverables | ✅ S3Backend configured (`ewr1.vultrobjects.com`) |
| **Zero-Blast-Radius Sandbox** | Isolated Docker containers (`--network none`, `--read-only`, no caps, execution timeout) | ✅ Implemented & verified via containment test |
| **Vultr API & Telemetry** | Dynamic model discovery and token/latency efficiency tracking | ✅ Telemetry & CLI stats built-in |

### Hackathon Requirements & Checklist Status
- [x] **Vultr VM Backend**: Production `docker-compose.yml` and `deploy/vultr_setup.sh` automated provisioning.
- [x] **Vultr Serverless Inference**: Provider adapter implemented (`src/atelier/agent/providers/__init__.py`), tested (`tests/unit/test_agent_providers_vultr.py`), and integrated into CLI and worker layers.
- [x] **Sandbox Boundary Layer**: Docker sandbox with `--network none`, `--read-only`, `--cap-drop ALL`, and timeout guards (`src/atelier/evals/sandbox.py`).
- [x] **Containment Moment Script**: `scripts/demo_containment.py` verifying host safety against destructive commands (`rm -rf /`), infinite loops, and exfiltration attempts.
- [x] **Public GitHub Repo**: Cleanly structured, open-source repository at `https://github.com/leojay-net/atelier`.
- [x] **Web Application**: Next.js 16 client in `web/` with multi-step workflows, SSE live progress streaming, and asset management.
- [ ] **Live Vultr Deployment**: Deploy stack to public Vultr Cloud Compute IP.
- [ ] **Demo Video Recording**: 3-minute video highlighting the real product flow and the required "Containment Moment".
- [ ] **Final Submission**: Complete submission on lablab.ai with public URL and architecture breakdown.

---

## Status at a glance

| Phase | State |
|---|---|
| 0 Foundation | ✅ done (reference image built and verified 2026-09-25) |
| 1 Image engine | ✅ done (all 28 goldens pinned and passing in the reference image) |
| 2 Agent layer | 🟡 built and tested offline; **the thesis gate is not measured** (no API credentials, no baseline yet) |
| 3 Video & audio | 🟡 built and tested offline (ops, tree fusion, audio, loudness, captions, checks, stabilize, 40 video/audio eval tasks); **the thesis gate on video is not measured** (needs the live run); a few presets remain |
| 4 Documents | 🟡 built and tested offline (PDF, Word, Excel, PowerPoint ops, export, checks, previews, 4 recipes, 40 eval tasks); LibreOffice paths are proven in the reference image only; OCR and PDF/A are not built |
| 5 App (Django) | 🟡 API layer built and tested (keys, assets, jobs, events, quotas, recipes, brand kit, workers); **no web UI in this repo**; S3 store, per-key rate limits and the exit criteria (horizontal scaling, UI end to end) are not done |
| 6 ML plugins | 🟡 plugin framework + 7 plugins (background removal, transcription, speech denoise, tracked reframing, face blur, image/video upscaling) with pinned weights; TTS, GPU pool not built |

## Phase 6: ML plugins

State: a plugin shape and seven working plugins ([ADR-0018](docs/adr/0018-ml-plugins-and-pinned-models.md)).

Done:
- [x] Pinned models (`assets/models/models.json`, `atelier models list|fetch|verify`): size and SHA-256 per file,
  https only, commit-pinned URLs, mismatches discarded; models are cache-key resources; fetched at image build
- [x] `image.remove_background` (U2-Net small, onnxruntime), `transcribe` (Whisper tiny, faster-whisper →
  SRT), `audio.denoise_speech` (RNNoise via ffmpeg `arnndn`); recipe `auto_captioned_reel`
- [x] `video.auto_reframe` (U2-Net centroid tracking: samples frames sparsely through the segmentation model,
  smooths the path, crops with a piecewise ffmpeg expression — fuses with the rest of the chain)
- [x] `video.blur_faces` (YuNet via `opencv-contrib-python-headless`: detects every frame at a capped resolution,
  blurs/pixelates/blacks out each box at full resolution; cannot join the fused ffmpeg graph, so it runs as its
  own pass — see ADR-0018)
- [x] `image.upscale` / `video.upscale` (FSRCNN via `cv2.dnn_superres`; `scale` 2, 3 or 4, each its own pinned
  weights file since FSRCNN is trained per scale factor; `video.upscale` is the same standalone shape as
  `blur_faces`, additionally capped to 640px on the longest side and 60s since a full network pass over every
  pixel of every frame is far costlier than a per-frame detector call)
- [x] Optional `ml` extra now carries `opencv-contrib-python-headless` (switched from the plain build: its
  `dnn_superres` wrapper is the only one that loads FSRCNN correctly, and it is a strict superset so
  `FaceDetectorYN` keeps working unchanged); op modules import no runtime at import time (warm workers stay
  light), tested
- [x] `SEEDED_ML` repeatability tested (two fresh engines, same bytes) on this Mac, for all seven plugins

Not done:
- [ ] Text to speech, GPU worker pool, per-hardware determinism tests, recipe library within an org (saved
  recipes exist; sharing across orgs is a marketplace)
- [ ] The catalogue-only budget (docs/04 §4.3, `test_catalogue_stays_small`) is at 3040 tokens, raised from 3000
  in two steps: to 3020 when `video.auto_reframe` and `video.blur_faces` shipped (`video.stabilize` hidden from
  it), then to 3040 when `image.upscale` and `video.upscale` shipped (`video.upscale` hidden too, the more
  specialized of the pair — same trade as `stabilize`). All four hidden/visible ops stay fully callable by name
  and through `describe_op`; 3040 covers only what hiding and trimming left over, not headroom for the next one

Found while building it:
- **`drawbox`'s `x`/`y` do not evaluate `t` on this ffmpeg build**, despite accepting the expression with no
  error: a debug log showed the sentinel `x:-2147483648` (`INT_MIN`), so the box never actually moved. `overlay`'s
  `x`/`y` do evaluate `t` correctly; `auto_reframe`'s tracked crop is built on `overlay`-style positioning, not
  `drawbox`.
- **onnxruntime's `intra_op_num_threads` changes a mask's bytes, not just its speed**: the same thread count gives
  identical bytes every run; a different count gives different bytes (confirmed with `u2netp`). Fixed at a
  constant 4 (not derived from the host), which also measured 5-6x faster than the determinism-safe `1`.
- **OpenCV's own threading did not need the same fix**: `video.blur_faces`'s YuNet detector gave bit-identical
  results across repeats and across thread counts 1/2/4/8/auto on this build, unlike libvidstab's OpenMP or
  onnxruntime's intra-op reduction. `cv2.setNumThreads` is still pinned to 4, for predictable behavior, not
  because determinism required it here.
- **A CNN face detector needs a photographic test image, not a drawn shape**: unlike U2-Net's salience (any
  bright box on a plain background works for `auto_reframe`'s tests), YuNet was trained on real photos and does
  not fire on a flat-colored `drawbox`/`crop` shape. The test fixture is a synthetic face built with numpy/cv2
  (radial shading, photographic noise, eyes, brows, a nose shadow, a mouth), which scores ~0.4 confidence — real
  photos score 0.8-0.99+, which is why the tests pass a lower `min_score` than the op's real-world default of 0.6.
- **Plain `opencv-python-headless` cannot load the super-resolution model `video.upscale` needed**:
  `cv2.dnn.readNetFromTensorflow` fails FSRCNN's frozen graph with a `DepthToSpace: blocksize is required`
  error. Confirmed this is a gap in the generic TensorFlow graph importer (the base build's own `dnn` module
  does have a `DepthToSpaceLayer` implementation; it just does not get populated correctly from this graph's
  encoding), not a true contrib-exclusive layer — but `opencv-contrib-python-headless`'s dedicated
  `cv2.dnn_superres.DnnSuperResImpl` loader works around it regardless, so that is what the `ml` extra now
  installs instead of the plain build.
- **A fixed thread count was not needed for determinism here either**: `cv2.dnn_superres`'s FSRCNN upsample
  gave bit-identical bytes across repeats and across thread counts 1/2/4/8/auto, the same result as YuNet's
  detector — a second data point that OpenCV's `dnn` module is stable on this platform, not assumed from the
  first one. `cv2.setNumThreads` is still pinned to 4, for predictable behavior, not because determinism
  required it.
- **A frozen TensorFlow graph can declare a fixed input shape even when its paper and README describe it as
  working on any image size**: the ONNX Model Zoo's `super-resolution-10.onnx` (the first model tried, from
  PyTorch's own ONNX export tutorial) hardcodes `[batch_size, 1, 224, 224]` in the exported graph, so it only
  ever accepts exactly 224x224 patches — a real re-tiling pipeline would be needed to use it on arbitrary video
  frames. `cv2.dnn_superres`'s FSRCNN handles any input size natively (confirmed from 100x150 up to 1280x720),
  which is why it was used instead, despite needing the dependency switch above.
- **`video.auto_reframe` was registered as `PERCEPTUAL`, not `SEEDED_ML`, until a test caught it**: `video_op()`
  hardcoded the determinism tier for every graph-builder op, so an op that samples a neural model but still
  joins the fused graph (unlike `blur_faces`/`upscale`'s standalone shape) silently inherited the wrong tier.
  `video_op()` now takes an optional `determinism` override; `auto_reframe` passes `SEEDED_ML` explicitly.

## Phase 5: Application

State: the headless application layer is built (`src/atelier/api/`, [ADR-0017](docs/adr/0017-app-layer-queue-and-tenancy.md),
[API reference](docs/11-api.md)). The web UI is not in this repository: it is the part you said you had built, and
the API is shaped to be driven by it (OpenAPI schema, CORS by allow-list, SSE by `fetch`).

Done:
- [x] Django + Ninja + ASGI project, `manage.py`, settings from the environment, in-memory test settings
- [x] Organizations, API keys (SHA-256 only, shown once), assets, jobs, job events, saved recipes, brand kit
- [x] One content-addressed store per organization: another org's handles do not resolve, dedup is per org
- [x] Jobs: `spec` (zero tokens), `prompt` (the agent) and saved-recipe runs; refused up front with every error;
  progress events from the engine runner and the agent loop; cancel; a database queue (`run_worker`, atomic claim);
  a reaper for silent workers; crash containment (a bug fails the job, not the worker)
- [x] Quotas per org per month (model units, compute seconds) with a clear 429; `GET /usage`
- [x] Brand kit sent in the request text, never the system prompt; validated colors, bundled fonts, image logos
- [x] SSE stream (async, resumes from `Last-Event-ID`) and polled events; CORS allow-list; OpenAPI schema
- [x] Downloads: safe names, inline only for safe types, `nosniff`, sandbox CSP
- [x] S3 (or compatible) mirror of every org's store (`engine/mirror.py`): API and workers need no shared disk
- [x] `docker/app.Dockerfile`, `docker-compose.yml` (API + workers on one volume); 40 API tests with a scripted model

Not done:
- [ ] The web UI (upload, chat, preview gallery, spec viewer/editor, job history) and its end-to-end test
- [ ] Postgres run under load and a real multi-machine scaling test (the S3 mirror is tested with mocked S3 and a
  directory backend, and with an emptied local disk)
- [ ] Rate limits on reads and uploads (jobs per key per minute exist), retention/cleanup of old jobs and assets, admin
  static files in the image, billing
- [ ] Batch jobs over the API (`--each` exists in the CLI), webhooks

## Phase 4: Documents

State: built and tested. Host tests cover everything that needs no LibreOffice; the reference image covers
conversion, recalculation and the invoice/report recipes ([ADR-0016](docs/adr/0016-document-engine.md)).

Done:
- [x] Safe packages (`docs/package.py`): size, entry and ratio limits, no macros, no fetched external content; a
  deterministic Office zip writer (sorted, fixed dates and properties)
- [x] PDF I/O (`docs/pdfio.py`): encrypted files refused, JavaScript/launch/embedded files stripped, saves with a
  content-derived ID and no dates (the source's `/ID` is dropped: converters make it random)
- [x] Run-aware text engine (`docs/text.py`): placeholders split across runs are found and filled in the run where
  they start; used by `doc.fill` and `doc.replace` on Word, PowerPoint and Excel
- [x] Ops: `doc.fill`, `doc.replace`, `doc.table`, `doc.image`, `xlsx.write` (formula safety: nothing that can
  reach outside the file), `xlsx.recalc`, `pptx.slide` (add/duplicate/delete/move), `doc.convert`, `pdf.merge`,
  `pdf.pages`, `pdf.rotate`, `pdf.stamp` (page numbers, Bates, diagonal watermark; correct on rotated pages; the bundled font embedded as a subset),
  `pdf.form` (fill, flatten), `pdf.redact` (removes text), `pdf.compress`
- [x] `export` for documents; presets `pdf_screen`, `pdf_print`, `docx_clean`, `xlsx_clean`, `pptx_clean`
- [x] Checks `no_placeholders`, `formula_errors`, `text_overflow`, `redaction_complete`, `pdf_valid`,
  `fonts_embedded`; previews of pages (Office files are printed first)
- [x] Recipes `invoice_from_template`, `report_docx_to_pdf`, `slides_from_outline`, `contract_redact_and_stamp`
- [x] 40 document eval tasks with offline-proven references and file-judging graders; `--suite docs`

Not done:
- [ ] The thesis gate on documents (needs the live run)
- [ ] OCR (`pdf.ocr`, Tesseract), PDF/A conversion and validation, embedded fonts for form appearances, merging form
  fields, `xlsx` conditional formatting, tracked changes and comments handling
- [ ] Round-trip formatting corpus (the roadmap's exit criterion): only synthetic fixtures so far

Found while building it:
- **qpdf's overlay helper is not deterministic**: it names the XObject randomly on every call. Stamps are drawn as
  form XObjects with counted names instead.
- **A converter's PDF is not byte-stable even after normalizing**: the first half of `/ID` survives an edit and
  LibreOffice makes it random. It is dropped before every save.
- **Redaction cannot keep the text of a page it touches** without editing content streams; the page becomes a
  picture. That is the honest trade: nothing is left underneath, and the check proves it on the finished file.

## Phase 3: Video & audio engine

State: built, tested on the host (ffmpeg 8.1.2) and in the reference image (ffmpeg 7.1.5). What is left needs
credentials (the live gate), a new ffmpeg build (`stabilize`), or a model (transcription).

Done:
- [x] ffprobe inspector in the sandbox (`av/probe.py`): forced demuxer, `file`-only protocols, nominal frame rate;
  `atelier inspect` prints video and audio lines
- [x] Typed filtergraph builder (`av/graph.py`) with one escaping function; user text/files never enter a filter
  string ([ADR-0013](docs/adr/0013-video-engine-graph-ops-and-fusion.md))
- [x] **Step fusion**: a chain of ffmpeg steps is one decode and one encode. Tested by counting ffmpeg encode
  invocations with a logging wrapper (a 5-step pipeline is 1 encode; a shared intermediate is materialized once)
- [x] One polymorphic `export` for image, video and audio (preset kind picks the encoder; wrong-media presets get a
  precise error listing the presets that fit)
- [x] Video ops `trim` (frame-accurate), `reframe` (fill / fit / blur_pad), `speed`, `fade`, `overlay`, `text`,
  `captions` (SRT/WebVTT, drawn by Pillow and overlaid; [ADR-0015](docs/adr/0015-text-and-captions-as-pictures.md)),
  `concat` (tree fusion: a join over trims is one graph), `slideshow`, `soundtrack`, `color`, `frame`
- [x] `video.stabilize` (libvidstab two-pass: `vidstabdetect` then `vidstabtransform`, fused with the rest of
  the chain; `auto`/`keep` zoom modes); needs a vidstab-enabled ffmpeg build (the reference image has one, this
  Mac's Homebrew build does not, so host tests skip cleanly). Hidden from the agent's catalogue (the ~3k-token
  budget, Phase 6 below) — still fully callable by name and through `describe_op`
- [x] Audio ops `extract`, `trim`, `fade`, `loudnorm`, `mix` (with ducking), `silence`, `denoise`; audio presets; two-pass loudness
  normalization measured on the final signal and verified on the output ([ADR-0014](docs/adr/0014-audio-ops-and-loudness-normalization.md))
- [x] Video presets `web_h264`, `reels_1080x1920`, `tiktok_1080x1920`, `shorts_1080x1920`, `youtube_1080p`; recipes
  `social_clip`, `extract_audio`
- [x] Checks `playable`, `av_sync`, `audio_present`, `duration_max`, `resolution`, `frame_rate`,
  `safe_margins` (shared with images), `loudness_target`, `black_frames`; opt-in `frozen_frames`, `dead_air`
- [x] `preview.frames`: a timestamped contact sheet of a video (~375 tokens for six frames), used by the agent's
  `preview` tool (with an optional `time`)
- [x] Prompt examples for a video request (one recipe call) and for joining clips (a full spec); the catalogue plus
  examples are about 3.1k tokens
- [x] Recipes `captioned_reel`, `podcast_clean`, `voiceover_music`, `music_video`; `video.mute`; preset `youtube_4k`
- [x] **40 video/audio eval tasks** (`tests/evals/tasks/video.yaml`, 20 recipe / 14 custom / 3 ambiguous / 3
  aesthetic; 22 train / 8 validation / 10 test), synthetic inputs (`tests/evals/make_av_inputs.py`), graders for kind,
  format, size, duration, sound, picture and loudness that judge the delivered file, `--suite video`, and a baseline
  image with ffmpeg (the baseline refuses to run video tasks on an image without it). Every reference solution runs
  through the engine offline and must pass its own graders

Not done (from docs/08):
- [ ] Transcription plugin (`captions source: "auto"`), `archive_prores`, `youtube_4k`, `podcast_audiogram`
- [ ] Caption timing check (cues overlapping, too fast to read)
- [ ] Trim's stream-copy fast path; validating time ranges against a probe at validation time
- [ ] The thesis gate on video (needs the live run: `atelier eval run --suite video`, then `eval baseline --suite video`)
- [ ] Cross-platform perceptual tests (SSIM/PSNR) for video; there are no video goldens by design

Found while building it:
- **Fusion bug caught by the goldens:** de-duplicating a step's input refs (right for a fused graph) broke `image.grid`
  when one photo was used for several cells. Fixed, with a regression test.
- **ffmpeg 7.1 vs 8.x differ:** MP4 stream durations are one frame apart, so `avg_frame_rate` reads 30.4 on a
  30 fps clip. The probe now trusts the nominal rate. Found only because the reference image runs a different ffmpeg
  than this Mac, which is exactly what it is for.
- **A validator bug older than Phase 3**, found by fuzzing with random Hypothesis seeds (one run in twelve): an
  unknown spec key containing a newline (or empty) was echoed verbatim into an error path, breaking the one-line
  error format the model reads and giving an empty path. Errors now escape control characters and bracket-quote odd
  keys; both counterexamples are pinned as explicit `@example`s. After the fix the two seeds that had failed and 41 others pass.
- **`audio.mix` was not deterministic.** `sidechaincompress` fed two live inputs gave up to 5 different outputs in
  12 identical runs on both ffmpeg 7.1.5 and 8.1.2; threads, bitexact and queue-size flags did not fix it. Ducking
  is now computed in numpy from the decoded voice and applied with `amultiply` (ADR-0014 §7): 10 runs, 1 output.
  Building it also exposed that `aformat` upmixing a mono track to stereo is 3 dB down (used `pan` instead).
- Overlay tests first compared two separate lossy encodes and were fragile; they now overlay onto flat black.
- **libvidstab's internal motion search is not deterministic under OpenMP**, even with every ffmpeg-level thread
  count (`-threads`, `-filter_threads`) already pinned to 1: repeated identical runs gave different `.trf`
  transform-file bytes. Fixed globally in `av/binaries.py`'s `run()` (the one place every ffmpeg/ffprobe
  subprocess call goes through) by setting `OMP_NUM_THREADS=1` in the environment — this also protects any
  future op that links an OpenMP-parallel library, not just `stabilize`.

## Phase 2: Agent layer

State: everything that can be built and proven without spending tokens is done. What is left needs
credentials, a baseline harness and your go-ahead to spend (see open questions 4-6).

Plan:
- [x] Messages API loop (`agent/loop.py`): append-only history; every stop reason handled
  (`end_turn`, `tool_use`, `max_tokens` with one retry, `refusal`, `pause_turn`, anything else is an
  explicit error); parallel tool results in one user message; fallback-turn echo rules
- [x] Four tools, sorted and byte-stable (`agent/tools.py`): `describe_op`, `inspect_media`,
  `preview` strict; `run_pipeline` not (ADR-0008 §1). Results are short text; failures are `is_error`
- [x] Frozen system prompt (`agent/prompt.py`): rules + catalogue + 5 examples, ≈ 1.7k tokens. One
  explicit cache breakpoint on the last system block plus top-level automatic caching. Every example
  is run through the real engine by a test, so none can rot
- [x] Budgets: turns, output tokens, images; soft (wrap-up notice) and hard (stop, keep best output)
- [x] Plan cache (zero-token repeats) and batch generalization: `atelier ask "..." --each "shots/*.jpg"`
  is one agent run on a sample, then the engine over every file
- [x] Telemetry (`agent/telemetry.py`) and `atelier stats`: tokens, turns, first-pass rate, cache-read
  ratio, cost. It warns when nothing is ever cached, which fails silently below a model's minimum prefix
- [x] Eval suite v1 (`tests/evals/`, `src/atelier/evals/`): 40 image tasks (20 recipe, 14 custom, 3 ask,
  3 aesthetic; 22 train / 8 validation / 10 test), deterministic graders, an LLM rubric grader, a
  runner, the Phase 2 exit gate and the docs/09 acceptance rule; `atelier eval run|report`
- [x] Offline proof that the suite is sound: every task's reference solution passes every grader
- [ ] **Live run** of the checks, the suite and the baseline (needs `ANTHROPIC_API_KEY` and approval to spend)
- [x] **Baseline harness** (`evals/baseline.py`, `evals/sandbox.py`, `docker/baseline.Dockerfile`,
  [ADR-0012](docs/adr/0012-eval-baseline-mount-free-sandbox.md)): the same 40 tasks by a bash-only coding agent in a
  mount-free Docker sandbox, same model/effort, compared on the portable graders (`atelier eval baseline`).
  Tested against a scripted model and a real container; **not run against the live API**
- [ ] Gate verdict, and tuning of the catalogue/errors/hints if it fails (docs/08)

**Exit criteria (the thesis gate), current status**
- median turns ≤ 3, median images = 0, first-pass check rate ≥ 85%, cache-read ratio ≥ 90%: implemented
  in `check_gate`, **not measured**
- tokens per completed job ≥ 5x lower than the baseline at equal or better pass rate: implemented (both sides
  judged on the portable graders), **not measured**: needs a live Atelier run and a live baseline run

How to run the live parts (these spend tokens; a full 40-task run is likely a few dollars, unmeasured):
```sh
ATELIER_LIVE=1 uv run pytest tests/live -v -s        # the API accepts our request; the prefix caches
docker build -f docker/baseline.Dockerfile -t atelier-baseline docker/     # once (needs network)
uv run atelier eval run --split validation           # then --split test for the headline number
uv run atelier eval baseline --split test            # the general coding agent, same tasks
# the same for video and audio: add --suite video (the baseline image needs a rebuild for ffmpeg)
uv run atelier eval report eval-report.json --baseline baseline-report.json --gate
```

Found while building it:
- **`batch_thumbnails` (Phase 1) was broken for most photos.** With a title it failed its own checks
  on 11 of 16 size x photo-aspect combinations (3:2, 4:3 and 1:1 among them): the title was laid out
  before the preset's fill-crop moved it out of the safe area. My "recipe checks pass" claim in Phase 1
  had only been verified on one aspect ratio. Fixed as recipe v2 (crop to the preset's aspect first),
  with a regression sweep in the tests. Recipe `when` now accepts a list (all must hold).
- **A prompt example was failing its own checks** (the same recipe). Caught by the test that runs every
  example, which is why that test exists.
- **`describe_name` crashed on the new `when` list**, so `atelier ops batch_thumbnails` would have too.
  Fixed; a test now describes every name in the catalogue.
- **SDK facts** the design doc did not have: `fallbacks`/`betas` exist only on `client.beta.messages`;
  `BetaFallbackBlock` requires `trigger`; missing credentials surface as a bare `TypeError` at request
  time (now `MissingCredentials`, and it is not counted as a job); Claude Opus 5 caches prefixes of
  512+ tokens, but Opus 4.6/4.5 and Haiku 4.5 need 4096, so a model switch can silently disable caching.
- **Docker Desktop wedged on host bind mounts** while I was building the baseline: `docker run -v <dir>` hung, containers
  sat in `Created`, then Docker Desktop reported "unable to start" and restarted, which stopped every container on the
  machine (this repo's work does not depend on them; others' did). Not reproducible on demand, so the cause is unknown; the
  sandbox was redesigned to need **no mounts** (tar streams through `docker exec`) and every Docker call is now time-bounded.
- **The full suite passes in the reference image (linux/arm64): 441 tests including the 500-image determinism test**, with one
  failure that was a test bug (killed orphans are zombies when PID 1 is not an init process; fixed).
- **Run ruff on `src tests` only** (as CI does), never `ruff format .`: ruff 0.16 also reformats the Python
  code blocks inside `docs/*.md`. It did that to `docs/03` and `docs/04` here; the original text was restored.
- The mutation check on the loop (breaking four invariants one at a time) is caught by the tests each time.
- Tests: 502 fast tests (up from 215): about 91 s on the host including the real-Docker sandbox tests, and 162 s in the
  reference image (498 passed there; the Docker tests skip inside a container). Before the warm workers the suite took 107 s.

## Phase 1: Image engine

Plan:
- [x] Deps: Pillow 12.3 (with AVIF, WebP, lcms2 and FreeType; no raqm), numpy, PyYAML
- [x] Font pack: Inter (4 weights), Noto Sans and Noto Serif (regular/bold), all OFL. The `fonts.json` manifest records each file's hash and is verified before use. Plus a bundled sRGB ICC profile.
- [x] Image I/O: EXIF auto-orient and sRGB conversion at load; byte-stable PNG intermediates; layout boxes carried through every geometry op
- [x] Ops: crop, resize, pad, rotate, flip, adjust, convert, composite, text, watermark, grid, export (catalogue ≈ 550 tokens)
- [x] Presets: web_jpeg, web_webp, web_avif, og_image, thumb_youtube, transparent_png, print_300dpi, plus watermark styles corner_subtle, corner_bold, center_large, tiled_proof. They expand into canonical args (version included), and `overrides` are validated.
- [x] Checks: dimensions, file_size_budget, color_profile, text_fit, text_contrast, safe_margins. They are pure functions of metadata; the modes are default/strict/none/list.
- [x] Previews: preview.thumb, preview.crop, preview.compare, preview.sheet (hidden, sandboxed, cached)
- [x] Recipes: watermark_batch, batch_thumbnails. The `{recipe, params}` spec form has typed params (asset/enum/preset/str/number/timecode/color), `when` conditions, errors reported at `params.*`, and the origin recorded in the canonical spec.
- [x] Batch runner: `Engine.run_batch` and `atelier batch SPEC --each in=GLOB`. Items run concurrently and each step is still sandboxed.
- [x] Inspect/probe runs in the worker sandbox (inputs are untrusted) and is cached by content hash
- [x] CLI: `inspect` (multiple targets), `preview`, `batch`; `ops` prints the full catalogue (≈ 880 tokens) and describes any op, preset or recipe; `run` exits with 3 when a check fails
- [x] Tests (215 fast + 1 slow):
  - image ops end to end, checks, presets, recipes
  - golden cases for every op (25 anywhere + 3 lossy cases pinned in the reference image only)
  - quality floors for lossy presets
  - every public param documented
  - catalogue token budget
  - 500-image batch byte-identical across two runs

**Exit criteria**
- every op has golden tests: ✅ (the 3 lossy exports are pinned in the reference image and skipped elsewhere)
- 100% of op args documented: ✅ (enforced by a test)
- a batch of 500 images is deterministic across two runs: ✅ (88s for both runs)

Notes from smoke testing (2026-09-25):
- Checks caught real problems: text cut off by the og_image fill-crop, and a logo inside the YouTube timestamp zone. Fix: `focus: attention` now also keeps layout boxes (text/logos) in frame whenever they fit.
- The smoke runs also hit a stale cache after I changed shared focus logic without bumping op versions. Rule: **a change in a shared helper must bump the versions of every op that uses it** (enforced by goldens once they exist).
- Result header is now `fail ·` when any check fails.
- The `background: auto` contrast plate steps its opacity up until WCAG AA is met (a fixed 55% plate wasn't always enough).
- Image goldens hash decoded pixels, not file bytes. zlib-ng's deflate output can vary by CPU architecture; same-platform byte determinism is covered by the 500-image repeat test.

Follow-ups noted during Phase 1:
- **Performance: done for latency, not for batch throughput** ([ADR-0009](docs/adr/0009-warm-standby-workers.md)).
  Each step still runs in its own fresh single-use process, but the next one is started ahead of time with its
  imports done. A 4-step pipeline went from 1.1-1.4 s to 0.34-0.42 s. A CPU-bound batch is unchanged, because the
  import work is moved off the critical path but not removed. Only forking each step from a warm template would
  cut it (Linux only, potentially ~10x); not built, measure against a real workload first.
- **Cross-platform goldens:** the 25 pixel-hash goldens made on macOS ARM also pass in the reference image, which on Apple Silicon Docker Desktop is linux/arm64. **Linux x86 is still untested** (the lossy encoders and FreeType text are the likeliest to differ); the first CI run will show it.
- Complex-script text (Arabic, Indic) needs raqm; not supported yet.
- CMYK print output needs a licensed CMYK profile; `print_300dpi` is sRGB for now.
- Still missing: `image.lut`, `sharpen`/`denoise`, `background` (ML plugin, Phase 6).

## Phase 0: Foundation (done 2026-09-25)

- Spec v1: full form + linear shorthand; canonicalization (defaults, units, float precision); validator that reports all errors with paths, codes, allowed values and suggestions
- Op registry (`@op`), determinism classes, catalogue and `describe`
- Content-addressed store (magic-byte sniffing, read-only objects, short-prefix handles), result cache with step keys, planner, runner (one sandboxed subprocess per step: rlimits, wall timeout, stripped env)
- CLI: `put`, `inspect`, `run` (`--in`, `--dry-run`, `--json`), `ops`
- Tests: unit, Hypothesis properties, golden harness (enforces op version bump on byte changes), perceptual SSIM/PSNR harness
- CI workflow, reference Dockerfile, ADR-0007 (Django for the app layer), CHANGELOG

## Decisions made along the way

- **Short handles:** specs accept a unique prefix of ≥ 8 hex characters (`asset:5891b5b5`); summaries show 12. This saves tokens. Accepted: [ADR-0011](docs/adr/0011-short-asset-handles.md).
- **ffmpeg in the reference image:** Debian ffmpeg 7.x from a pinned snapshot.debian.org date instead of a static binary (ADR-0004 says static). Accepted: [ADR-0010](docs/adr/0010-ffmpeg-from-pinned-debian-snapshot.md).
- **Text layout:** Pillow's BASIC layout engine, which gives deterministic output but no complex-script shaping (Arabic, Devanagari…). Revisit with raqm in the reference image.
- **EXIF orientation** is applied automatically when an image is loaded, so there's no separate `orient` op; `rotate`/`flip` are explicit.

## Decisions made under delegation (2026-09-25)

You asked me to choose the best route and move on, so these are decided and recorded as ADRs:
- [ADR-0008](docs/adr/0008-agent-layer-implementation-decisions.md): the nine agent-layer choices (accepted)
- [ADR-0009](docs/adr/0009-warm-standby-workers.md): warm standby workers, one fresh process per step
- [ADR-0010](docs/adr/0010-ffmpeg-from-pinned-debian-snapshot.md): keep the Debian-snapshot ffmpeg (amends ADR-0004)
- [ADR-0011](docs/adr/0011-short-asset-handles.md): specs accept short handles (resolution must stay scoped to
  the caller's store)

## Open questions for the team

1. **Live run.** May I run the live checks, the eval suite and the baseline? They need `ANTHROPIC_API_KEY`; this machine
   has none and no `ant` CLI, so nothing has ever been sent to the API. Delegating design decisions is not permission to
   spend, so I have not asked for a key or tried to find one. Order I would run them in: `tests/live` (a few cents), then
   `eval run --split validation`, then the test split for both the agent and the baseline.
