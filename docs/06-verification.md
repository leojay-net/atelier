# 6. Verification & Previews

Images are the most expensive thing an agent can put in its context, and "look at the output and judge it" is the most common reason agents loop. Atelier replaces most looking with **measuring**, and makes the remaining looking cheap.

## 6.1 Checks

Checks are deterministic functions `(output artifact, spec, preset) → pass | warn | fail + message + hint`. They run after every `run_pipeline`.

### Image checks
| Check | What it verifies |
|---|---|
| `dimensions` | Exact or bounded size per preset |
| `file_size_budget` | e.g. `og_image ≤ 300 KB`, `web_jpeg ≤ 500 KB` |
| `color_profile` | Expected ICC embedded; no unexpected alpha |
| `text_contrast` | WCAG contrast ratio of rendered text vs the local background (≥ 4.5:1 by default) |
| `text_fit` | No clipped or overflowing text boxes (from the typesetter's layout metrics) |
| `safe_margins` | Overlays and text inside declared safe areas |
| `subject_intact` | Saliency or face boxes not cropped (when `crop` / `reframe` was used) |
| `artifacts` | Banding / blockiness score below a threshold after heavy compression |

### Video / audio checks
| Check | What it verifies |
|---|---|
| `duration` | Matches the spec (± 1 frame) and platform limits |
| `resolution_fps` | Exact per preset |
| `loudness_target` | Integrated LUFS within ±1 of the target; true peak ≤ limit |
| `audio_present` / `channels` | No silent or missing tracks |
| `black_frames` / `frozen_frames` | `blackdetect` / `freezedetect` spans outside intended fades |
| `av_sync` | Stream start offsets and duration drift < 1 frame |
| `captions_in_safe_area` | Rendered caption boxes inside the preset safe zone |
| `captions_timing` | No overlaps, minimum duration, reading speed ≤ 20 chars/s |
| `subject_in_frame` | After reframe: tracked face/subject kept inside the frame over time |
| `playability` | Container/codec compatibility for the preset target (profile, level, faststart) |

### Document checks
| Check | What it verifies |
|---|---|
| `no_placeholders` | No leftover `{{…}}`, `[NAME]`, `Lorem ipsum`, `TODO` |
| `text_overflow` | PPTX text exceeding its shape (font-metric estimate); DOCX tables wider than the page |
| `fonts_embedded` | PDF fonts embedded (required for print/archive presets) |
| `formula_errors` | XLSX after `xlsx.recalc`: no `#REF!`, `#DIV/0!`, `#NAME?`, `#VALUE!` |
| `page_count` | Expected or bounded |
| `links` | Internal links and bookmarks resolve |
| `redaction_complete` | Redacted strings absent from the text layer and metadata |
| `pdfa_valid` | veraPDF validation for archive presets |

### Check results in tool output
Only failing and warning checks are listed in full. Passing checks are counted:

```
checks 13/14 · WARN text_contrast: title 3.1:1 on bright sky (min 4.5) → hint: style.shadow=true or style.box="auto"
```

## 6.2 The escalation ladder

The agent climbs this ladder **only as far as needed**. Each level costs more tokens than the one before.

| Level | What the model receives | Approx. cost | When |
|---|---|---|---|
| **L0: metrics** | One-line summary + check results | ~50–150 tokens | Always |
| **L1: thumbnail** | One image, long edge ≤ 512 px | ~350 tokens | Visual tasks ("make it look good"), or one warn/fail on a visual check |
| **L2: contact sheet** | One grid image (e.g. 3×3 frames or pages) with timestamps/page numbers burned in | ~800–1,100 tokens | Video or multi-page issues; replaces 9 separate images |
| **L3: region crop** | A crop of a specific region at native resolution, or a before/after strip | ~400–1,000 tokens | Fine detail: text rendering, edges, a specific failing area |

(Image cost is approximately `width × height / 750` tokens. A single full-HD frame sent naively costs about 1,500+ tokens. A 9-frame video review done naively can cost more than 13,000 tokens, versus about 1,000 for one contact sheet.)

**System prompt rule:** *Request a preview only if (a) a visual check warned or failed, or (b) the user's request is aesthetic by nature. Prefer contact sheets over multiple images. Never request a preview just to confirm success.*

**Hard budget:** `max_images` per job (default 3). When it's reached, the agent must decide from metrics.

## 6.3 Preview generation

All previews are produced by the engine, deterministically:
- **Thumbnail:** Lanczos downscale, sRGB, JPEG q≈70 (text-heavy content: PNG).
- **Contact sheet:** frames at scene changes (PySceneDetect) or evenly spaced; timestamp labels; for docs, rendered pages via pypdfium2 at low DPI.
- **Crop:** the region given by the failing check (for example the caption box at `00:03.2`) or by the model (`region: [x,y,w,h]`, normalized 0–1).
- **Compare:** side-by-side before/after with identical crop and scale.

## 6.4 Human-in-the-loop (app)

The UI always shows the user full-quality previews locally. That costs no tokens: **the user's eyes are free, the model's eyes are not**. When the user gives feedback ("logo a bit smaller"), the agent edits the stored spec (for example `scale: 0.12 → 0.09`) and re-runs. Only the changed step and its downstream steps re-execute, because everything upstream is cached.
