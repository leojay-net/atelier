# 16. CapCut (free and Pro) against Atelier: what is missing, for the editor and for the AI

**Written 2026-11-07; status updated the same day (section 5).** Atelier's *engine* already has
many of the operations CapCut sells; most of the gap is that the **manual editor does not expose
them**, and the AI is not always told to use them.

CapCut facts below come from web research. Most sources were third-party blogs, so treat exact
plan details (credits, storage, price) as approximate and check capcut.com before quoting them.

## 1. What CapCut has

**Editing core (free):** multi-track timeline (several video, audio, text and sticker layers),
split / trim / delete / reorder clips, speed (constant, and **speed curves** such as bullet,
montage, hero time), reverse, freeze frame, **keyframes** (position, scale, rotation, opacity,
volume), masks (shapes with feathering), blend modes, transitions between clips, filters and
colour adjustment (brightness, contrast, saturation, curves, LUTs), video effects, stickers and
overlays, picture-in-picture, background replacement, chroma key (green screen), text with
styles, animations and templates, auto captions with styles, text-to-speech voices, voice
effects (changer), audio fades, beat markers and sync to music, stabilisation, aspect-ratio
presets, undo/redo, timeline zoom, shortcuts, templates, project autosave.

**Pro (paid) adds:** 4K / HDR export, the full AI toolkit on a monthly credit allowance
(background removal without a green screen, camera/object tracking, vocal isolation, speaker
captions, more AI voices and effects, script-to-video, auto-edit from raw clips), the full
library of premium music, sound effects, stickers and stock footage, and cloud storage.

## 2. Where Atelier stands

Legend: **Engine** = an operation exists on the server and the AI can use it. **Editor** = a
user can do it by hand in the workspace today.

| CapCut feature | Engine / AI | Manual editor | Gap |
|---|---|---|---|
| Trim, split, delete parts | `video.trim`, `concat` | yes (drag edges too) | Reorder clips missing |
| Several clips on one timeline | `video.concat`, `slideshow` | no (one source video) | **Big** |
| Several video / overlay layers | `video.layers`, `overlay`, `pip` | no | **Big** |
| Per-layer mute / hide / remove | n/a | yes (new) | AI output is still one baked file |
| Speed | `video.speed` | one global speed | Per-clip speed, curves, reverse, freeze |
| Keyframes | no | no | Missing everywhere |
| Masks, blend modes | no | no | Missing everywhere |
| Transitions | `video.transition` | no | Expose between parts |
| Fades | `video.fade`, `audio.fade` | no | Expose |
| Filters, colour grading | `video.color`, `image.adjust` | no | Expose, add presets / LUTs |
| Effects (blur, glow, shake…) | partial (`blur_faces`) | no | Mostly missing |
| Stickers, image overlays, PiP | `video.overlay`, `pip`, `lower_third` | no | Expose, add a sticker library |
| Chroma key, background removal | no | no | Missing (needs a model) |
| Text styles, animation, templates | `video.text` (animated titles) | 2 styles | Add fonts, sizes, animations, templates |
| Auto captions | `video.captions` (word-timed) | no | Add a one-click button |
| Text-to-speech | 22 voices, cloned voice | yes | CapCut has more voice effects (changer) |
| Voice changer / voice effects | no | no | Missing |
| Noise reduction | `audio.denoise`, `denoise_speech` | no | Expose |
| Loudness normalisation | `audio.loudnorm` | no | Expose, one toggle |
| Music generation | yes (original, composed) | yes | Strong, CapCut only offers a library |
| Beat sync | music has a known BPM, shots use beat lengths | no | Add "snap cuts to beat" |
| Stabilise | `video.stabilize` | no | Expose |
| Auto reframe | `video.auto_reframe` | shape + manual framing | Add the auto button |
| Sound effects | `video.sfx` (4 sounds) | yes (4 sounds) | Needs a bigger library |
| Undo / redo, timeline zoom, shortcuts | n/a | partly (undo cuts, space) | Add proper undo/redo and zoom |
| Script to video, auto-edit | AI agent (prompt to finished video) | n/a | This is Atelier's strength |
| Animated characters and episodes | yes (H3 plus cast and voices) | n/a | Beyond CapCut |
| 4K / HDR export | engine can encode; presets limited | no | Add export presets |
| Stock library, templates, cloud storage | Openverse photos only | no | Out of scope |

**Where we are ahead of CapCut:** the prompt-to-finished-video agent, consistent animated
characters with their own voices, original music from a prompt, deterministic and cached
renders, and a sandboxed engine.

## 3. Plan

### Tier A: expose what the engine already has (fast, high visibility)

These need a new panel and a field in the save request (`POST /jobs/{id}/edit`), not new
engine work.

1. **Filters / Adjust tab:** brightness, contrast, saturation, warmth, plus 8 to 10 named filters
   (maps to `video.color`). Live preview with CSS filters.
2. **Auto captions:** one button runs speech recognition (`ml/speech.py`) and `video.captions`.
   Styles: bold yellow, clean, karaoke.
3. **Fades and transitions** between kept parts and at the start and end (`video.fade`,
   `video.transition`).
4. **Audio clean-up:** denoise, loudness normalise (a toggle), voice-over ducking under speech.
5. **Stabilise and blur faces** as one-click checkboxes in Properties.
6. **Stickers and image overlays:** upload or pick a picture, drag on the player, set time range
   (maps to `video.overlay` / `pip`).
7. **Text upgrades:** font, size, colour, animation (pop, fade, slide), and 5 to 6 templates.
8. **Undo / redo and timeline zoom.**

### Tier B: the timeline becomes truly multi-clip (bigger, after Nov 8)

1. Several video clips on the timeline: add from Media, drag to reorder, split, per-clip speed,
   reverse, freeze frame.
2. Several overlay tracks (picture-in-picture, stickers) with their own bars.
3. Keyframes for position, scale, opacity (start with two-point animation).
4. Speed curves (a few presets).
5. Beat markers and "snap cuts to beat" using the music's BPM.
6. **AI layers:** the AI saves its pieces (silent video, voice, music, each scene) as separate
   assets, and the editor opens them as separate tracks.

### Tier C: needs a model or content we do not have

Background removal and chroma key (model or service), masks and blend modes (engine work),
voice changer, a large sound and sticker library, templates. Decide these only after A and B.

### The AI side

- Most Tier A operations exist, so the agent can use them now. Add short rules to the prompt
  (when to use denoise, loudnorm, filters, captions, transitions) and keep the cost down by
  letting the editor and the agent share the same operations.
- New AI abilities worth adding: "auto-edit" (cut raw clips to the beat of a chosen song),
  "make it a reel/trailer" recipes, "clean up the audio", "apply a look".
- Every manual feature should also be a spec field so "make it warmer and add captions" and the
  button do the same thing.

## 4. Suggested order

| When | What |
|---|---|
| Nov 7 to 8 | Submission work first (README, submission page, demo video). Tier A items 1, 2 and 4 only if time is left; each is about 1 to 2 hours. |
| After Nov 8 | Rest of Tier A (about 1 day), then Tier B multi-clip timeline (about 3 days), then the AI stems. |

## 5. Status (2026-11-07, deployed)

| Item | State |
|---|---|
| Tier A (filters, auto captions, fades, transitions, denoise, loudness, stabilise, stickers, text colour and animation, undo/redo, zoom, per-clip speed) | Built, in the editor and the AI prompt |
| Split into picture and sound layers | Built (`POST /jobs/{id}/split`); the editor opens the sound as its own layer. The AI does not yet produce split layers by itself |
| Reverse, hold a frame, speed curves (hero, bullet, montage, ramps) | Built, per part |
| Sticker glide (two-point keyframe) | Built (`video.overlay` `move_to`) |
| More clips after the edit | Built as a join at the end; the preview plays only the first video. Not yet a free multi-clip timeline (no reordering or mixing clips) |
| Beat grid and snap Split to beat | Built with a typed BPM; no automatic beat detection yet |
| Green-screen removal | Engine op `video.chromakey` (colour key over a picture); no editor panel yet; no AI background removal |
| Several overlay tracks, free keyframes (scale, opacity, rotation), masks, blend modes, voice changer, sticker/sound library | Not built |
