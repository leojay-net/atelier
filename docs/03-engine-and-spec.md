# 3. Engine & Pipeline Spec

The pipeline spec is the **only interface between the model and the media**. Keeping it compact and strictly validated is the main way Atelier saves tokens and keeps output consistent.

## 3.1 The pipeline spec

A spec is a small DAG (directed acyclic graph) of steps. Each step names an op, its input(s) and its args.

```json
{
  "version": 1,
  "inputs": {
    "clip": "asset:9c1e4b…",
    "logo": "asset:a0f3d2…"
  },
  "steps": [
    { "id": "cut",  "op": "video.trim",        "in": "clip", "args": { "start": "00:05", "end": "00:20" } },
    { "id": "vert", "op": "video.reframe",     "in": "cut",  "args": { "aspect": "9:16", "mode": "smart_center" } },
    { "id": "caps", "op": "video.captions",    "in": "vert", "args": { "source": "auto", "style": "clean" } },
    { "id": "brand","op": "video.overlay",     "in": ["caps", "logo"], "args": { "anchor": "top_right", "margin": "safe", "scale": 0.12 } },
    { "id": "out",  "op": "export",            "in": "brand","args": { "preset": "reels_1080x1920" } }
  ],
  "outputs": ["out"],
  "checks": "default"
}
```

### Spec rules
- **`version`**: the spec schema version. The engine rejects versions it doesn't know.
- **`inputs`**: named asset handles. Raw paths and URLs are not allowed; the app layer turns uploads into handles. A handle is the full `asset:<sha256>` or a unique prefix of at least 8 hex characters ([ADR-0011](adr/0011-short-asset-handles.md)).
- **`steps[].in`**: a single step id or input name, or a list for multi-input ops (overlay, concat, mix, merge).
- **`args`**: validated against the op's Pydantic model. **No unknown keys are allowed** (`extra="forbid"`).
- **`outputs`**: which step results to deliver. Intermediate results are cached but not delivered.
- **`checks`**: `"default"` (the domain's standard checks), `"strict"`, `"none"`, or an explicit list.

### Shorthand: linear pipelines
Most edits are linear. The spec accepts a compact form that the validator expands into the full DAG. The model is encouraged to use it because it's shorter:

```json
{ "in": "asset:9c1e…", "ops": [
    ["video.trim", {"start": "00:05", "end": "00:20"}],
    ["video.captions", {"source": "auto", "style": "clean"}],
    ["export", {"preset": "reels_1080x1920"}]
]}
```

### Recipe invocation
The shortest form of all. The model picks a recipe and fills its slots:

```json
{ "recipe": "social_clip", "params": { "in": "asset:9c1e…", "start": "00:05", "end": "00:20", "platform": "reels" } }
```

### Canonicalization
Before hashing or caching, specs are **canonicalized**:
- defaults filled in explicitly,
- units normalized (`"00:05"` → `5.0` seconds, `"12%"` → `0.12`),
- keys sorted, floats rounded to op-declared precision,
- presets expanded to their concrete args, with the preset version recorded.

So `{"start":"0:05"}` and `{"start": 5}` produce the **same cache key**.

## 3.2 Validation & error design (principle P7)

Validation runs completely **before** any work starts and reports *all* errors at once, not just the first one. Each error has:

```json
{
  "path": "steps[3].args.anchor",
  "code": "enum_mismatch",
  "message": "anchor 'upper_right' is not valid",
  "allowed": ["top_left", "top_right", "bottom_left", "bottom_right", "center"],
  "suggestion": "top_right"
}
```

It reaches the model as one compact line per error:
`steps[3].args.anchor: 'upper_right' invalid → use one of top_left|top_right|bottom_left|bottom_right|center (did you mean top_right?)`

Semantic validation goes beyond the schema:
- time ranges inside the input's duration (`end 00:52 > duration 00:48.2`),
- media-type compatibility (`video.overlay` second input must be image or video),
- font, LUT and preset existence,
- resource limits (output resolution, duration, page count),
- DAG sanity (no cycles, no unused inputs, every output reachable).

## 3.3 The op contract

```python
@op(
    name="image.resize",
    version="1.2.0",
    inputs=[MediaType.IMAGE],
    output=MediaType.IMAGE,
    determinism=Determinism.BIT_EXACT,
    params=ResizeParams,          # Pydantic model, extra="forbid"
)
def resize(ctx: OpContext, img: ImageArtifact, p: ResizeParams) -> ImageArtifact:
    ...

def summary(result: ImageArtifact) -> str:      # one-line result for the model
    return f"{result.width}x{result.height} {result.mode}"
```

Every op **must**:
1. Be a pure function of its inputs, args and pinned resources: no network, no clock, no global randomness.
2. Declare a **version**. A behavior change bumps the version, which invalidates cached results for that op only.
3. Declare a **determinism class** (below).
4. Provide a **summary** formatter (≤ 120 characters).
5. Provide a **one-line catalogue description** and a **detailed doc** (served on demand by `describe_op`).
6. Come with golden or perceptual tests before merge (see [Testing](09-testing.md)).

### Determinism classes

| Class | Guarantee | Examples |
|---|---|---|
| `BIT_EXACT` | Identical bytes on the reference platform | resize, crop, trim with stream copy, docx text replace, pdf merge |
| `PERCEPTUAL` | Identical bytes on the same platform; across platforms SSIM ≥ 0.995 / PSNR ≥ 45 dB | lossy encodes (x264/x265/JPEG), text rendering |
| `SEEDED_ML` | Deterministic given model weights + seed + params on the same hardware class | whisper transcription (temperature 0, fixed beam), rembg |
| `NONDETERMINISTIC` | Not guaranteed; always flagged in results and never cached silently | cloud generative APIs (plugins only) |

## 3.4 Op catalogue (v1 target)

Names are kept short and grouped so the catalogue stays small inside the system prompt.

### Image (`image.*`)
| Op | Purpose |
|---|---|
| `crop` | Box, aspect-ratio or subject-aware crop. `focus: attention` picks the most detailed region *and* keeps already-placed text and logos in frame |
| `resize` | Fit / fill / exact, with explicit resampling filter (Lanczos by default) |
| `pad` | Canvas extend with color, blur or mirror fill |
| `rotate` / `flip` | Explicit rotation and mirroring. EXIF orientation is applied automatically when any image is loaded, so there is no separate `orient` op |
| `adjust` | Brightness, contrast, saturation, gamma, white balance, levels |
| `lut` | Apply a bundled 3D LUT |
| `composite` | Layer images with anchor, margin, opacity, blend mode |
| `text` | Typeset text with bundled fonts, auto-fit to box, stroke, shadow, contrast guard |
| `watermark` | Preset-driven logo or text watermark |
| `background` | Solid, blur or replace (uses ML plugin `bg.remove` when needed) |
| `sharpen` / `denoise` | Classical filters (unsharp mask, bilateral / NLM) |
| `grid` | Collage and contact-sheet layout |
| `convert` | Color space and ICC conversion, bit depth, alpha handling |

### Video (`video.*`)
| Op | Purpose |
|---|---|
| `trim` | Cut a range: stream-copy when on keyframes, otherwise precise re-encode |
| `concat` | Join clips with normalization (fps, resolution, audio layout) and optional transitions |
| `reframe` | Aspect change (fit / fill / blur-pad / smart-center tracking) |
| `speed` | Constant speed change with pitch-preserved audio |
| `overlay` | Image or video overlay with anchor, safe margin, timing, fade |
| `text` | Titles and lower-thirds from style presets |
| `captions` | Burn or mux subtitles from SRT/VTT or `source:"auto"` (transcription plugin) |
| `fade` | Video/audio fade in and out |
| `color` | Adjust or LUT (same parameters as image) |
| `stabilize` | vidstab two-pass |
| `frames` | Extract frames or GIF at an explicit fps |
| `slideshow` | Images + durations + transitions → video |

### Audio (`audio.*`)
| Op | Purpose |
|---|---|
| `extract` | Pull an audio stream from video |
| `loudnorm` | Two-pass EBU R128 to a target (for example −14 LUFS, −1 dBTP) |
| `mix` | Voice + music with ducking (gain curve computed from the voice, so bit-exact) |
| `fade` / `trim` / `concat` | Basic editing |
| `denoise` | afftdn / RNNoise model (pinned) |
| `silence` | Detect or remove silences with explicit thresholds |

### Documents (`doc.*`, `pdf.*`)
| Op | Purpose |
|---|---|
| `doc.fill` | Fill DOCX/PPTX/XLSX templates (docxtpl / Jinja) from structured data |
| `doc.replace` | Run-aware find-and-replace that keeps formatting |
| `doc.table` | Insert or update tables from rows (DOCX/PPTX) with a style preset |
| `doc.image` | Insert or replace images by placeholder or alt-text anchor |
| `xlsx.write` | Write ranges, formulas, number formats, conditional formatting |
| `xlsx.recalc` | Recalculate formulas via headless LibreOffice (pinned) and report errors |
| `pptx.slide` | Add, duplicate, reorder or delete slides from layouts |
| `doc.convert` | DOCX/PPTX/XLSX → PDF via pinned headless LibreOffice |
| `pdf.merge` / `pdf.pages` / `pdf.rotate` | Page operations (`pdf.pages` keeps, reorders or splits out pages: one op makes one output, so a split is one step per part) |
| `pdf.stamp` | Watermark, header/footer, page numbers, Bates numbering |
| `pdf.form` | Fill AcroForm fields, optionally flatten |
| `pdf.redact` | True redaction (content removal, not just black boxes) |
| `pdf.ocr` | OCR layer via OCRmyPDF (pinned Tesseract) |
| `pdf.compress` | Image downsampling and stream optimization with a preset |

### Cross-cutting
| Op | Purpose |
|---|---|
| `export` | Final encode or format with a preset; the **only** place output encoding happens. `overrides` may replace individual preset settings; the merged settings and the preset version are written into the canonical args |

## 3.5 Presets

Presets are **versioned YAML files reviewed like code**. They hold the expert knowledge the model would otherwise have to reason about.

```yaml
# presets/export/reels_1080x1920.yaml
id: reels_1080x1920
version: 3
kind: export.video
container: mp4
video:
  codec: libx264
  profile: high
  level: "4.1"
  pix_fmt: yuv420p
  width: 1080
  height: 1920
  fps: 30
  rate_control: { mode: crf, crf: 20, maxrate: 8M, bufsize: 16M }
  gop: 60
  color: { primaries: bt709, trc: bt709, space: bt709, range: tv }
audio:
  codec: aac
  bitrate: 160k
  sample_rate: 48000
  channels: 2
  loudness: { target_lufs: -14, true_peak_db: -1.0 }
safe_area: { top: 0.14, bottom: 0.20, left: 0.06, right: 0.06 }   # UI overlays on the platform
checks: [duration_max_90s, resolution_exact, loudness_target, captions_in_safe_area]
```

Initial preset families:
- **Video export:** `reels`, `tiktok`, `youtube_1080p`, `youtube_4k`, `youtube_shorts`, `linkedin`, `x_video`, `web_h264`, `web_av1`, `archive_prores`
- **Image export:** `web_jpeg`, `web_webp`, `web_avif`, `thumb_youtube`, `og_image`, `print_300dpi_cmyk`, `transparent_png`
- **Caption styles:** `clean`, `bold_pop`, `karaoke`, `minimal_lower`
- **Text / typography:** `title_modern`, `lower_third_news`, `quote_card`
- **Docs:** `pdf_screen`, `pdf_print`, `pdf_archive_pdfa2b`, table styles

## 3.6 Recipes

A recipe is a parameterized, versioned spec template, i.e. a saved workflow. Recipes are the biggest single lever for both tokens and consistency: a recurring task turns into "pick a recipe and fill a few slots."

```yaml
# recipes/social_clip.yaml
id: social_clip
version: 2
description: Cut a segment, reframe vertically, auto-captions, platform export.
params:
  in:       { type: asset, media: video }
  start:    { type: timecode }
  end:      { type: timecode }
  platform: { type: enum, values: [reels, tiktok, shorts], default: reels }
  captions: { type: enum, values: [clean, bold_pop, none], default: clean }
steps:
  - [video.trim,     { start: "{{start}}", end: "{{end}}" }]
  - [video.reframe,  { aspect: "9:16", mode: smart_center }]
  - when: "captions != 'none'"
    do: [video.captions, { source: auto, style: "{{captions}}" }]
  - [export,         { preset: "{{platform}}_1080x1920" }]
```

Starter recipes: `social_clip`, `podcast_audiogram`, `product_photo` (bg remove → center → pad → shadow → web export), `batch_thumbnails`, `invoice_from_template`, `report_docx_to_pdf`, `slides_from_outline`, `watermark_batch`, `contract_redact_and_stamp`.

**"Save as recipe"**: after a successful agent job, the app can generalize its spec into a recipe (with the user confirming which values become params). Next time, the same task costs a fraction of the tokens.

## 3.7 Execution model

1. **Validate** (all errors at once), then **canonicalize**.
2. **Plan:** build the DAG, compute a cache key per step, mark cached steps and estimate the runtime.
3. **Fuse:** adjacent ffmpeg-backed video/audio steps are **fused into a single filtergraph** where possible. That means one decode, one encode and no generation loss from intermediate re-encodes. Intermediate results are only materialized at cache boundaries (steps whose outputs are reused or requested).
4. **Run:** each fused group runs in a worker subprocess with CPU, memory and time limits.
5. **Check:** run domain checks and preset checks on outputs.
6. **Report:** a structured result plus a one-line summary per output.

### Why fusion matters
Chaining `trim → reframe → overlay → export` as four separate ffmpeg runs means four lossy encodes and 4× the time. The filtergraph builder compiles it into one run:

```
ffmpeg -ss 5 -to 20 -i clip.mp4 -i logo.png -filter_complex \
 "[0:v]crop=…,scale=1080:1920:flags=lanczos[v0];[1:v]scale=…[lg];[v0][lg]overlay=W-w-65:269[v1]" \
 -map "[v1]" -map 0:a -c:v libx264 … out.mp4
```

The model never sees this command. It exists only in logs and debug output.
