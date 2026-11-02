# ADR-0018: ML plugins and pinned models

- **Status:** Accepted
- **Date:** 2026-09-26 (decided under delegated authority)
- **Builds on:** [ADR-0009](0009-warm-standby-workers.md), [docs/05 determinism](../05-determinism.md)

## Context
Phase 6 adds ops that run neural models: cutting a subject out of a photo, writing captions from speech,
removing noise from a voice. They bring three problems the classical ops do not have: weights are large and
come from third parties, inference libraries are heavy (and the warm standby workers import every builtin op
module), and a model's output is a function of its weights and of the runtime that runs it.

## Decisions
1. **Weights are pinned like binaries.** `assets/models/models.json` lists every model file with its URL
   (pinned to a release tag or a commit, never a moving branch), size and SHA-256, plus licence and source.
   `atelier models fetch NAME` downloads over https, checks size and hash and discards a mismatch; `verify`
   re-checks installed files; nothing is ever fetched during a job (workers have no business on the network).
   The reference image fetches them at build time from the same manifest and stops the build if a hash differs.
2. **A model is a resource.** `model_<name>` resources put the manifest hashes into the cache key of every op
   that uses the model, and are unavailable (with the `atelier models fetch` instruction) until the files are
   installed; `ml_runtime` adds the versions of onnxruntime, faster-whisper, CTranslate2, NumPy and
   opencv-contrib-python-headless.
3. **The ML runtimes are an optional extra** (`atelier[ml]`): `onnxruntime`, `faster-whisper` and
   `opencv-contrib-python-headless` (the `contrib` build, not plain `opencv-python-headless`: it is a strict
   superset — `FaceDetectorYN` keeps working unchanged — and its `dnn_superres` wrapper is the only one of the
   two that loads the super-resolution model correctly; the plain build's generic TensorFlow importer fails on
   its `DepthToSpace` layer with a missing-attribute error that is a parsing gap in the generic importer, not a
   genuinely contrib-exclusive layer). Op modules import no runtime at import time (tested in a subprocess), so
   a warm standby worker never loads one; the op imports it when it runs, and a missing runtime is an `OpError`
   naming the extra.
4. **Inference is deterministic on one platform.** CPU inference with a *fixed* thread count (checked: onnxruntime's
   parallel reduction changes floating-point rounding with the thread count, the same way libvidstab's did, so the
   count is a constant in the code, not derived from the machine's core count, exactly like x264's `-threads`;
   measured 5-6x faster fixed at 4 threads than at 1, with no loss of reproducibility), fixed session options, beam
   size 1 and temperature 0 for Whisper, `int8` weights. The ops are `SEEDED_ML`: identical bytes on the same
   hardware and runtime versions (tested with two fresh engines), not across platforms.
5. **Seven plugins ship.** `image.remove_background` (U2-Net small via onnxruntime; soft or hard edge, feather,
   optional background color, existing transparency kept), `transcribe` (Whisper tiny via faster-whisper:
   video or audio in, an SRT text asset out, which `video.captions` already reads, so captions need no file:
   the recipe `auto_captioned_reel`), and `audio.denoise_speech` (the RNNoise "beguiling drafter" model through
   ffmpeg's `arnndn`, the model copied into the graph's work directory and referenced by file name, never by
   path). Both `audio.denoise` (a spectral filter, for steady hiss) and `audio.denoise_speech` exist because
   they suit different sounds: RNNoise treats a non-speech tone as noise. Four more plugins followed the same
   shape: `video.auto_reframe` (U2-Net small, the same model as `remove_background`: samples frames sparsely,
   smooths the salient-object centroid, crops with a piecewise ffmpeg expression — fuses with the rest of the
   graph like any other video op), `video.blur_faces` (YuNet, an anchor-free face detector, via
   `opencv-contrib-python-headless`'s `FaceDetectorYN`: every frame, not sampled, since a missed frame would
   show an unblurred face — see decision 7), and `image.upscale`/`video.upscale` (FSRCNN, a small
   super-resolution CNN, via `cv2.dnn_superres`; `scale` is 2, 3 or 4, each its own pinned weights file since
   FSRCNN is trained per scale factor; `video.upscale` is the standalone shape too, and additionally caps its
   source to 640px on the longest side and 60s, since a full network pass over every pixel of every frame costs
   far more than `blur_faces`'s per-frame detector call — measured ~50ms at 320x240 up to ~780ms at 1280x720 on
   this platform).
6. **Small models on purpose.** They make mistakes (names, accents, busy scenes). The op docs say so, and the
   agent is told to read a transcript before burning it in and to preview a cut-out.
7. **Not every ML op can join the fused ffmpeg graph.** `video.blur_faces` needs a different region blurred in
   every frame, in a count that changes frame to frame, and `video.upscale` needs a different full-frame network
   pass every frame with a larger output size than its input: neither is an ffmpeg filter expression, the way a
   piecewise crop expression captures a single tracked subject (`video.auto_reframe`). So both are registered as
   a plain `@op`, not a `video_op`: their own decode (ffmpeg → raw BGR24 on a pipe), their own Python loop (one
   transform per frame — detect-and-blur each box, or one super-resolution pass), their own encode (raw BGR24 on
   a pipe → ffmpeg, muxed with the original audio), connected by two concurrent subprocesses this process pumps
   frames between (never buffered whole: a minute of 1080p would be several gigabytes). Neither can fuse with a
   `trim` before it or an `export` after it — each becomes its own pass — which is the right trade for work that
   is fundamentally per-pixel and per-frame, not expressible as a filter.

## Consequences
- Not built: text to speech, a GPU worker pool, and per-hardware determinism tests. The plugin shape above is
  what they would follow — decision 7's standalone shape for ones that cannot fuse. Upscaling, previously listed
  here as needing a GPU-class runtime, is now built: that was true of Real-ESRGAN specifically, not of every
  super-resolution model — FSRCNN runs on CPU through `cv2.dnn_superres`, which is why `opencv-contrib-python-headless`
  replaced the plain build (decision 3).
- The reference image grows by the runtimes and about 80 MB of weights.
- The system prompt's catalogue is at its budget, raised from ~3k to 3020 tokens when `video.auto_reframe` and
  `video.blur_faces` shipped, then to 3040 when `image.upscale` and `video.upscale` shipped. `video.stabilize`
  and `video.upscale` are hidden from it (`hidden=True`: still callable by name and through `describe_op`, just
  not listed) to keep most of that room back each time; there is no partial "one-line index" for a hidden op
  today, so hiding one trades its discoverability for the others' full parameter listing — a real cost, not a
  free move. A future op this size should expect the same trade, not an unconditional budget bump.
