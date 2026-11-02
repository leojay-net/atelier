# 8. Roadmap

Delivery is phased so that **the token-saving thesis is proven early** (Phase 2, on images) before the larger video and document work is built on top of it. Every phase has hard exit criteria.

## Phase 0: Foundation
**Goal:** the skeleton that everything plugs into.

- Repo, `uv` project, lint/type/test CI, reference Docker image (pinned ffmpeg, fonts, LibreOffice)
- Spec models (Pydantic), canonical JSON, validator with actionable errors
- Op registry (`@op`), determinism classes, catalogue renderer
- Content-addressed store (local disk), result cache, DAG planner, subprocess runner with limits
- CLI: `atelier run spec.json`, `atelier inspect <file>`
- Golden-test harness (hash on reference image) and perceptual harness (SSIM/PSNR)

**Exit criteria:** a dummy two-op pipeline runs, caches, re-runs from cache and passes golden tests in CI. Invalid specs produce path-precise errors.

## Phase 1: Image engine
- Ops: crop, resize, pad, rotate/orient, adjust, composite, text (bundled fonts, auto-fit), watermark, convert, grid, export
- Presets: web_jpeg/webp/avif, og_image, thumb_youtube, transparent_png, print_300dpi
- Checks: dimensions, file_size_budget, color_profile, text_contrast, text_fit, safe_margins
- Previews: thumb, crop, compare, grid
- Recipes: `watermark_batch`, `batch_thumbnails`

**Exit criteria:** every op has golden tests; 100% of op args documented in the catalogue; batch of 500 images deterministic across two runs.

## Phase 2: Agent layer (on images)
- Messages API loop, 4 strict tools, frozen system prompt + catalogue, caching layout
- Budgets, stop-reason handling, refusal fallback, structured final output
- Plan cache, batch generalization
- Telemetry + dashboard
- **Eval suite v1:** 40 image tasks with graders (checks + rubric)
- Baseline comparison: the same 40 tasks done by a general coding agent (tokens, turns, pass rate)

**Exit criteria (the thesis gate):**
- median turns per job ≤ 3, median images per job = 0
- first-pass check rate ≥ 85%
- tokens per completed job **≥ 5× lower** than the baseline at **equal or better** grader pass rate
- prefix cache-read ratio ≥ 90% in steady state

If the gate fails, fix the catalogue, errors, hints and recipes before expanding scope.

## Phase 3: Video & audio engine
- ffmpeg filtergraph builder + step fusion; ffprobe inspector
- Ops: trim, concat, reframe, speed, overlay, text, fade, color, frames, slideshow; audio extract, loudnorm, mix/duck, fade, silence
- Plugin: transcription (faster-whisper, pinned) → `captions source:"auto"`
- Presets: reels, tiktok, shorts, youtube_1080p/4k, web_h264, archive_prores; caption styles
- Checks: duration, resolution_fps, loudness, black/frozen frames, av_sync, captions safe-area and timing, playability
- Previews: contact sheets with timestamps
- Recipes: `social_clip`, `podcast_audiogram`
- Eval suite: +40 video/audio tasks

**Exit criteria:** fused pipelines have one encode per output; perceptual tests pass cross-platform; eval thesis gate holds on video tasks.

## Phase 4: Document engine
- DOCX/PPTX/XLSX: fill, replace (run-aware), table, image, slide ops; xlsx write + recalc
- PDF: merge/split/rotate, stamp, form, redact, ocr, compress; doc.convert via LibreOffice
- Deterministic zip and PDF writers
- Checks: no_placeholders, text_overflow, fonts_embedded, formula_errors, redaction_complete, pdfa_valid
- Previews: rendered page contact sheets
- Recipes: `invoice_from_template`, `report_docx_to_pdf`, `slides_from_outline`, `contract_redact_and_stamp`
- Eval suite: +40 document tasks

**Exit criteria:** round-trip formatting preserved on the fixture corpus; eval thesis gate holds on docs.

## Phase 5: Application
- Django service (Ninja API, ASGI), job queue, workers, SSE progress, S3 store, Postgres metadata
- Auth (API keys → org/user), per-org quotas and budgets
- Web UI: upload, chat request, preview gallery, spec viewer/editor, "save as recipe," job history
- Brand kits (logo, fonts, colors) as per-org assets passed in messages, never in the system prompt

**Exit criteria:** end-to-end job via UI; horizontal worker scaling; per-org usage reporting.

## Phase 6: ML plugins & polish
- Background removal (rembg), upscaling (Real-ESRGAN), denoise (RNNoise), smart reframe tracking, optional TTS
- GPU worker pool; `SEEDED_ML` determinism tests per hardware class
- Recipe marketplace/library within an org

## Cross-cutting, every phase
- Security review of new ops (see [Security](10-security.md))
- Catalogue size budget: keep the system prompt catalogue under ~3k tokens. Use `describe_op` for depth.
- Changelog of op/preset version bumps
