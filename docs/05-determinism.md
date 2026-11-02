# 5. Determinism

**Goal:** the same inputs plus the same spec give the same output, and we can prove it.

Determinism provides three things:
1. **Trust:** a recipe that worked yesterday works today.
2. **Caching:** identical work is never repeated, which saves compute and tokens.
3. **Testability:** golden-hash tests catch any behavior drift immediately.

## 5.1 Where nondeterminism comes from, and the fix for each

| Source | Example | Fix |
|---|---|---|
| Different binaries or libraries | ffmpeg 6 vs 7 change encoder output | Pinned static ffmpeg build; `uv.lock`; one Docker reference image |
| Host fonts | "Arial" differs between macOS and Linux | Bundled font pack; ops reject non-bundled fonts |
| Implicit defaults | Library default resampler changes between versions | Canonicalization writes **every** parameter explicitly |
| Timestamps & metadata | MP4 `creation_time`, PNG `tIME`, DOCX `modified`, PDF `/CreationDate` | Strip or normalize metadata on export (below) |
| Threading | Some encoders vary with thread count | Pin thread counts per preset |
| OpenMP/intra-op threading in a linked library | libvidstab's motion search, onnxruntime's intra-op reduction: neither is reached by ffmpeg's own `-threads`/`-filter_threads` | `OMP_NUM_THREADS=1` globally (ffmpeg); a *fixed*, not minimal, thread count in session options (onnxruntime) |
| Randomness | Dithering, ML sampling | Fixed seeds; `temperature=0` / fixed beam for transcription |
| Floating-point & SIMD | CPU-specific paths | Accept `PERCEPTUAL` class for affected ops; bit-exact only on the reference platform |
| Zip ordering | Office files are zip archives | Deterministic zip writer: sorted entries, fixed timestamps |
| Dict/set ordering | JSON serialization | Canonical JSON: `sort_keys=True`, fixed separators, normalized floats |

## 5.2 Per-media techniques

### Images
- Pillow: explicit `resample` on every geometric op; explicit `compress_level` for PNG and `quality`, `subsampling`, `optimize` and `progressive` for JPEG.
- Strip EXIF, XMP and `tIME` on export unless the preset keeps specific tags (for example orientation or copyright).
- Always convert to a declared working color space (sRGB, 8/16-bit) at load, and embed the declared ICC profile at export.
- `Image.MAX_IMAGE_PIXELS` is set explicitly (also a safety limit).

### Video & audio (ffmpeg)
- A single pinned static ffmpeg/ffprobe build. Its version string is part of every cache key.
- Export flags: `-map_metadata -1`, `-fflags +bitexact`, `-flags:v +bitexact`, `-flags:a +bitexact`, and an explicit `-metadata creation_time=` (empty or fixed).
- Explicit `-threads N` and encoder thread settings per preset.
- Explicit pixel format, color tags, GOP, rate control and audio sample rate and layout. Nothing is left to defaults.
- **Two-pass loudnorm** with measured values from pass 1 fed into pass 2. The measurement itself is deterministic.
- Frame-accurate trims decode from the previous keyframe and cut precisely. Stream-copy is only used when cut points land on keyframes (the planner decides; both are deterministic).

### Documents
- DOCX/PPTX/XLSX: set core properties (`created`, `modified`, `lastModifiedBy`, `revision`) to fixed or derived values, then **rewrite the zip deterministically** (sorted entries, fixed `1980-01-01` entry timestamps, fixed compression level).
- PDF: save with pikepdf using `deterministic_id=True` (the document `/ID` is derived from content), remove or fix `/CreationDate` and `/ModDate` in both the Info dictionary and XMP, and pin object-stream and compression settings per preset.
- Conversion via LibreOffice: a pinned version inside the reference image, with bundled fonts only, a fixed user profile directory and a `PERCEPTUAL` determinism class (rendering can vary at the sub-pixel level).

### ML plugins
- Pinned model weights (by hash), pinned runtime, fixed seeds, `temperature=0`, fixed beam size. A library's own
  internal thread pool (OpenMP, onnxruntime's intra-op) can silently change output bytes even when every
  ffmpeg/encoder thread flag is already pinned — checked per library, not assumed; OpenCV's YuNet detector was
  checked and found stable across thread counts, unlike the other two.
- `SEEDED_ML` class: deterministic on the same hardware class (CPU vs specific GPU). CPU is the reference.

## 5.3 Cache keys

```
step_key = sha256(
    canonical_json({
        "engine": ENGINE_VERSION,
        "op": op.name, "op_version": op.version,
        "args": canonical_args,               # defaults filled, units normalized, presets expanded
        "inputs": [input_content_hash, ...],  # content hashes, not names/paths
        "resources": {                         # only what this op touches
            "ffmpeg": FFMPEG_BUILD_ID,
            "fonts": FONT_PACK_HASH,
            "model_weights": WEIGHTS_HASH,
        },
    })
)
```

- Artifacts are stored by **content hash**, so identical outputs from different specs are stored once.
- Bumping an op's version invalidates only that op's cached results and everything downstream of it.
- `NONDETERMINISTIC` ops are cached with an explicit "pinned result" marker, visible in results. They are never silently reused as if they were deterministic.

## 5.4 Platform scope

Determinism guarantees are stated honestly:

| Scope | Guarantee |
|---|---|
| Reference platform (the Atelier Docker image, x86-64 CPU) | `BIT_EXACT` ops: identical bytes. `PERCEPTUAL` ops: identical bytes |
| Other platforms (macOS/ARM dev machines, other Linux) | `BIT_EXACT` ops: identical bytes for pure-Python/lossless paths. `PERCEPTUAL` ops: SSIM ≥ 0.995 / PSNR ≥ 45 dB vs reference |
| Across engine versions | Same **visual intent**; bytes may change only when an op version bumps (documented in the changelog) |

CI runs golden tests on the reference image. Developer machines run perceptual tests.

## 5.5 The model side

The model is the one component that isn't deterministic. The mitigations are listed in [Vision §"optimal at all times"](01-vision-and-principles.md#what-optimal-at-all-times-realistically-means). In summary:

- Recipes and presets shrink the space of possible specs.
- Strict tool schemas make malformed specs impossible.
- Canonicalization makes equivalent specs identical (so they share cache entries).
- The plan cache replays exact repeats without calling the model.
- Checks catch meaningful quality differences before delivery.

**Every delivered output is fully described by its canonical spec**, which is stored with the job. Re-running that spec reproduces the output exactly, with no model involved.
