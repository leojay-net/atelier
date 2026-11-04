# Changelog

Op and preset version bumps are recorded here. A golden hash may only change together with
an entry below (enforced by `tests/golden/test_golden.py`).

## Unreleased

### Live picture test fixes (2026-11-04)
- `video.slideshow` 1.0.0 → 1.1.0: new mode `blur` (the default): a picture of another shape is
  shown whole over a blurred, slightly darkened copy of itself. Before, a landscape family photo
  in a vertical reel was center-cropped and lost both parents.
- Prompt: text slides crop the picture to the video's aspect before adding text (a title placed
  near the bottom of a 4:3 photo was cut off by the slideshow's crop) and use `background: auto`;
  tutorials teach concretely (real steps, a short real example such as a line of code).

### Live chat test fixes (2026-11-04)
- `audio.mix` 2.0.0 → 2.1.0: `length` (`voice` default, unchanged; or `music`): a short voice
  over a longer song keeps the whole song, the music ducking under the voice and easing back to
  full level after it. Before, "add a voice to my reel" either cut the song at the end of the
  voice or (with `video.soundtrack` mix) buried the voice under full-volume music.
- `make_voiceover` output is loudness-normalized to -16 LUFS (TTS arrives around -26 dB) and
  ends with 0.6 s of silence, so a closing fade never cuts the last word.
- `step_start` progress events list every op fused into the run (`ops`).

### Live user test fixes (2026-11-04, docs/13)
- `video.overlay` 1.0.0 → 1.1.0: on a vertical frame the image is kept inside the Reels / TikTok /
  Shorts safe area (top 14 %, bottom 20 %, sides 6 %, plus a 1 % inset), so a corner logo no longer
  fails `safe_margins` with the default margin. Landscape placement is unchanged.
- `image.text`, `video.text`, `video.captions`, `video.lower_third` 1.0.0 → 1.0.1: invisible
  characters (zero-width joiners, variation selectors) are dropped instead of drawn as empty boxes,
  and text with characters the bundled fonts cannot draw (emoji, CJK, …) fails with the characters
  named instead of rendering "tofu". Golden hashes unchanged (versions only).
- `video.speed` accepts `factor=0.25` (quarter-speed slow motion); the bound was exclusive.
- `video.transition` hidden from the catalogue (still callable); catalogue budget 3040 → 3240 for
  `video.crop`, `video.pip` and `video.lower_third`.

### Phase 6: ML plugins (in progress)
- New `atelier.ml` and `atelier.models`. Ops at 1.0.0 (`SEEDED_ML`): `image.remove_background`, `transcribe`
  (video or audio → SRT text), `audio.denoise_speech`. Recipe `auto_captioned_reel` v1. `atelier models
  list|fetch|verify`; pinned weights in `assets/models/models.json`; optional extra `atelier[ml]`. See ADR-0018.
- `video.auto_reframe` 1.0.0 (`SEEDED_ML`): tracked crop that follows the main subject (U2-Net centroid,
  smoothed, piecewise ffmpeg crop expression); fuses with the rest of the chain. `video.blur_faces` 1.0.0
  (`SEEDED_ML`): detects faces every frame (YuNet via the new `opencv-python-headless` dependency) and blurs,
  pixelates or blacks out each one at full resolution; cannot fuse (every frame needs Python between decode and
  encode), so it runs as its own pass. New pinned model `yunet`. See ADR-0018. The catalogue-only token budget
  (`test_catalogue_stays_small`) is raised 3000 → 3020 to fit; `video.stabilize` is hidden from the catalogue
  to keep most of that room back (still callable by name and through `describe_op`).
- `image.upscale` / `video.upscale` 1.0.0 (`SEEDED_ML`): enlarge with FSRCNN (a small super-resolution CNN) via
  `cv2.dnn_superres`; `scale` is 2, 3 or 4. `video.upscale` is the same standalone shape as `blur_faces` (every
  frame needs its own network pass, so it cannot fuse), capped to 640px on the longest side and 60s. New pinned
  model `fsrcnn` (3 files, one per scale). The `ml` extra's opencv dependency switched from
  `opencv-python-headless` to `opencv-contrib-python-headless` (a strict superset; `FaceDetectorYN` is
  unaffected) because only its `dnn_superres` wrapper loads FSRCNN's frozen graph correctly. The catalogue-only
  token budget is raised 3020 → 3040; `video.upscale` is hidden from the catalogue, the more specialized of the
  pair (same trade as `video.stabilize`). See ADR-0018.

### Phase 5: application layer (in progress)
- New `atelier.api` (Django, Django Ninja, ASGI): organizations, API keys, assets, jobs with progress events (polled or
  server-sent), quotas, saved recipes, brand kit, a database-backed worker (`manage.py run_worker`), operator admin,
  and an S3-compatible mirror of every organization's store (`atelier.engine.mirror`).
  See [docs/11-api.md](docs/11-api.md) and ADR-0017. No op, preset or recipe changed.
- The engine runner and `Engine` gain an optional progress `observer` (`plan`, `step_start`, `step_done`, and
  `turn`/`tools` from the agent loop) that can stop a run between steps; `JobRecord.units()` is the one definition of
  billed-token equivalents.

### Phase 4: document engine (in progress)
- New `atelier.docs`. Ops at 1.0.0: `doc.fill`, `doc.replace` (Word, PowerPoint, Excel; run-aware, keeps formatting),
  `doc.table`, `doc.image` (Word, PowerPoint), `xlsx.write`, `xlsx.recalc` (LibreOffice), `pptx.slide`,
  `doc.convert` (LibreOffice), `pdf.merge`, `pdf.pages`, `pdf.rotate`, `pdf.stamp`, `pdf.form`, `pdf.redact`
  (removes the text: affected pages become pictures), `pdf.compress`; hidden `preview.pages`. Stamps embed a subset of the
  bundled Inter, so stamped PDFs pass `fonts_embedded`. See ADR-0016.
- `export` now also encodes documents. Presets at v1: `pdf_screen`, `pdf_print`, `docx_clean`, `xlsx_clean`,
  `pptx_clean`. Recipes at v1: `invoice_from_template`, `report_docx_to_pdf`, `slides_from_outline`,
  `contract_redact_and_stamp` (recipes gained `object` and `list` parameters).
- Checks: `no_placeholders`, `formula_errors`, `text_overflow` (warns), `redaction_complete`, `pdf_valid` (default);
  `fonts_embedded` (in `pdf_print`). New pinned resources `office`, `pdf`, `libreoffice`. Probe v5 describes
  documents; `inspect_media` lines name placeholders, form fields and sheets.
- Evals: 40 document tasks (`tests/evals/tasks/docs.yaml`), graders `pages`, `slides`, `text_has`, `text_lacks`,
  `page_text`, `placeholders`, `fonts`, `form`, `cell`, `calculated`, `rotation`, `info`; `--suite docs`; the
  baseline image gains python-docx, openpyxl, python-pptx, pypdf, pikepdf and LibreOffice.
- Fix: `video.concat` crossfades failed on ffmpeg 7 ("xfade: the inputs needs to be a constant frame rate"):
  `setpts` now comes before `fps` in the conform chain. Found by the reference image, not the host.

### Phase 3: video engine (in progress)
- `video.stabilize` 1.0.0 (`PERCEPTUAL`): two-pass motion smoothing (libvidstab `vidstabdetect` →
  `vidstabtransform`), `auto`/`keep` zoom modes; needs a vidstab-enabled ffmpeg build (the reference image has
  one). Found and fixed a cross-cutting determinism bug while building it: libvidstab's OpenMP-parallel motion
  search varied run to run even with ffmpeg's own thread flags pinned; `OMP_NUM_THREADS=1` is now set on every
  ffmpeg/ffprobe subprocess call (`av/binaries.py`).
- Ops at 1.0.0: `video.trim`, `video.reframe`, `video.speed`, `video.fade`, `video.overlay`; hidden
  `preview.frames`. `export` is now one op for image, video and audio inputs (v1.0.0 unchanged: image results
  and goldens are identical).
- Export presets at v1: `web_h264`, `reels_1080x1920`, `tiktok_1080x1920`, `shorts_1080x1920`,
  `youtube_1080p`. Recipe `social_clip` v1.
- Checks: `playable`, `av_sync`, `audio_present`, `duration_max`, `resolution`, `frame_rate`,
  `safe_margins` (shared with images). Probe v4 (video and audio via ffprobe).
- Audio: ops at 1.0.0 `audio.extract`, `audio.trim`, `audio.fade`, `audio.loudnorm`, `audio.mix`; export presets
  at v1 `podcast_mp3`, `podcast_m4a`, `voice_opus`, `wav_master`; recipe `extract_audio` v1. Loudness targets
  (Reels/TikTok/Shorts -14 LUFS; podcast/voice presets -16 LUFS) with the `loudness_target` check. See ADR-0014.
- Ops at 1.0.0: `video.text` and `video.captions` (SRT/WebVTT burn-in). Text is rendered by Pillow and overlaid,
  so it does not need drawtext or libass. See ADR-0015. Check `overlay_safe_area` is replaced by `safe_margins`,
  now shared by images and video.
- `audio.mix` **2.0.0**: ducking is a gain curve computed from the voice instead of `sidechaincompress`, which was
  not deterministic (up to 5 different outputs in 12 runs). Parameters are now `music_volume`, `duck`,
  `duck_db`. See ADR-0014 §7.
- Ops at 1.0.0: `video.concat` (2 to 24 clips, optional crossfade, fit/fill, silence for clips without sound),
  `video.slideshow` (2 to 40 pictures), `video.soundtrack` (replace or mix, loop, fade out), `video.color`,
  `video.frame` (video to image), `audio.silence`, `audio.denoise`.
- Ops at 1.0.0: `video.mute`. Recipes at v1: `captioned_reel`, `podcast_clean`, `voiceover_music`, `music_video`.
  Export preset `youtube_4k` v1. Podcast presets now list the `dead_air` check.
- Checks: `black_frames` (default, warns), `frozen_frames`, `dead_air` (podcast presets list it). Every export is
  analyzed once after encoding (`meta.analysis`: black, frozen and silent intervals).
- Adjacent ffmpeg steps are fused into one graph: one decode, one encode. Fusion now covers trees, not just
  chains (a join over several trims is one graph). Ops that take pictures or draw text pin Pillow, the ICC profile
  and the font pack in their cache key. See ADR-0013.
- Fix: spec error paths and messages now escape control characters and bracket-quote keys that are not
  identifiers, so an untrusted key can no longer break the one-line error format or produce an empty path.

- Evals: 40 video/audio tasks (`tests/evals/tasks/video.yaml`), new graders `kind`, `duration`, `sound`,
  `picture`, `loudness` (and `dims`/`format` now judge video and audio too), `atelier eval run|baseline --suite`.
  The baseline image gains ffmpeg. The system prompt gains one example (join two clips).
- Fix: a `concat` (or any node with both picture and sound outputs) followed by a step that uses only one of them
  (loudness normalization, `video.mute`, `audio.extract`) failed with "Error binding filtergraph": unread
  outputs are now closed with sinks.

### Phase 2: agent layer
- New `atelier.agent` (loop, four tools, frozen system prompt, budgets, plan cache, telemetry) and
  `atelier.evals` (40 image tasks, graders, runner, exit gate). No op or preset changed. The system
  prompt's catalogue version is recorded per job and is part of the plan-cache key.

### Phase 1: image engine
- Ops at 1.0.0: `image.crop`, `image.resize`, `image.pad`, `image.rotate`, `image.flip`,
  `image.adjust`, `image.convert`, `image.composite`, `image.text`, `image.watermark`,
  `image.grid`, `export`; hidden `preview.thumb`, `preview.crop`, `preview.compare`,
  `preview.sheet`.
- Export presets at v1: `web_jpeg`, `web_webp`, `web_avif`, `og_image`, `thumb_youtube`,
  `transparent_png`, `print_300dpi`. Watermark presets at v1: `corner_subtle`,
  `corner_bold`, `center_large`, `tiled_proof`.
- Recipes at v1: `watermark_batch`, `batch_thumbnails`.
- Recipe `batch_thumbnails` **v2**: with a title it now crops to the preset's aspect before
  placing the text. At v1 the text was laid out before the preset's fill-crop and left the
  safe area (or was cut off) for 11 of 16 size x photo-aspect combinations. Recipe `when`
  accepts a list (all conditions must hold).
- Checks: `dimensions`, `file_size_budget`, `color_profile`, `text_fit`, `text_contrast`,
  `safe_margins`.
- Font pack v1 (Inter, Noto Sans, Noto Serif; OFL) and bundled sRGB ICC profile.
- Probe v1: image and text metadata, parsed in the worker sandbox.

### Phase 0: foundation
- Engine foundation: spec v1, canonicalization, validator, registry, store,
  result cache (`ENGINE_VERSION` 1), planner, subprocess runner, CLI.
- Hidden ops `debug.upper`, `debug.join`, `debug.sleep`, `debug.fail` at 1.0.0.
