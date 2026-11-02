# ADR-0015: Titles and captions as rendered pictures

- **Status:** Accepted
- **Date:** 2026-09-26 (decided under delegated authority)
- **Builds on:** [ADR-0013](0013-video-engine-graph-ops-and-fusion.md), [ADR-0014](0014-audio-ops-and-loudness-normalization.md)

## Context
Text on video is the most requested edit for social clips, and ffmpeg has two ways to do it: `drawtext`
and `subtitles` (libass). Both depend on how that ffmpeg was built. The Homebrew ffmpeg on this Mac has
neither; the reference image has both, with different fonts, hinting and layout. Text in a filter string is
also the one place untrusted words could reach a filter parser.

## Decisions
1. **Text is drawn by Pillow, not by ffmpeg.** `video.text` and `video.captions` render each piece of text
   into a transparent PNG with the same typesetter as `image.text` (bundled hash-verified fonts, wrapping,
   auto-fit, outline, shadow, plate), and ffmpeg only overlays the picture during a time window
   (`overlay=enable='between(t,s,e)'`). The same words look the same on every machine, they are judged by the
   same rules as image text, and no user text is ever part of a filter string.
2. **Captions come from an SRT or WebVTT file given as the second input**, parsed by us (`av/subtitles.py`):
   markup, ASS overrides, entities and control characters are dropped (plain words only), errors carry the
   line number, the file is limited to 1 MiB and a track to 300 cues. Automatic transcription is a plugin
   for later; it will produce the same file.
3. **A small named vocabulary instead of a style sheet.** Three styles (`clean`, `bold_pop`, `minimal_lower`)
   and five positions (`top`, `upper_safe`, `middle`, `lower_safe`, `bottom`), plus optional `size` and
   `color`. Sizes are a fraction of the *shorter* side, so a style reads the same on a vertical and a
   landscape frame. Every cue is fitted to the same box at the same maximum size, so cues of a track share
   one size unless one is too long for the box. The `*_safe` positions clear the platform UI zones of the
   9:16 presets; `top` and `bottom` deliberately do not, and the check says so.
4. **One safe-margin check for images and video.** `overlay_safe_area` is gone; `safe_margins` judges
   text, captions, overlays and watermarks in both. A caption track records one layout box (the union of
   what was drawn), so a 300-cue track adds one entry to the metadata, not 300.
5. **Each cue is one overlay input.** That is cheap for the tens of cues in a short clip and is why the
   cap exists. If real use needs more, the answer is to merge cues that do not overlap in time into one
   timeline of pictures, not to raise the cap.

## Consequences
- No per-word highlighting, karaoke timing, animation or emoji; those need a different renderer.
- Text over moving video cannot use `background: auto` (it needs the pixels underneath); the styles use an
  outline, a shadow, or a fixed plate instead. There is no contrast check for video text yet.
- The captions op works on the host ffmpeg and in the reference image alike, so its tests run in both.
