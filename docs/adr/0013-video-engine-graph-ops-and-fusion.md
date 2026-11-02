# ADR-0013: Video engine: graph ops, fusion, and one polymorphic `export`

- **Status:** Accepted
- **Date:** 2026-09-26 (decided under delegated authority)

## Context
Phase 3 adds video and audio. docs/03 §3.7 requires that a chain like trim → reframe → overlay → export
is compiled into one ffmpeg run (one decode, one encode), that `export` is the only place output encoding
happens, and ADR-0004 requires argv-only commands and files (not strings) for user text. The Phase 1
engine ran each step as its own worker process and `export` was image-only with fixed input/output types.

## Decisions

1. **One `export` op for every media type.** Its input is `AnyOf(image, video, audio)` and its output is
   `SAME`. The preset's `kind` (`export.image` / `export.video` / ...) selects the settings model, and a
   `media_check` hook rejects a preset that does not fit the input ("'web_jpeg' encodes image, but the input
   is video", with the fitting presets listed). Resources are per media (`resources_by_media`), so exporting an
   image never requires ffmpeg. Alternatives were `video.export` / `audio.export` (three names in the
   catalogue, and recipes could not be written media-neutrally). Image behaviour and goldens are unchanged.
2. **Video ops are graph builders, not runnable steps.** A builder receives the `Graph` (the filters so far
   and the state of the main stream: size, fps, duration) and adds filters. Because the state is carried, an
   op computes from what earlier ops produced (a crop after a speed-up sees the right numbers).
3. **Fusion.** A graph step absorbs the group that produced any of its inputs (the main one or an extra)
   when that group is a graph group, the intermediate has exactly one consumer, and it is not a requested
   output. So a chain fuses, and so does a tree: `concat` over three `trim`s, or an `audio.mix` whose music
   comes from an `audio.trim`, is one graph, one decode and one encode. (This was linear at first; joins
   needed the tree.) Anything else is materialized. A
   materialized intermediate is a visually lossless H.264 (crf 14) so it does not visibly degrade. Groups
   execute in order of their *last* step, so an extra input of a fused step (a logo produced by a step
   listed later) is ready. Inside a group a step reads its inputs as slots: `>= 0` a file passed in,
   `-1 - k` the output of step k of the group (the worker keeps a snapshot of the streams each step
   left, so a later step, or a join, can pick any of them up). Non-graph steps keep their inputs exactly as
   written; only graph groups de-duplicate inputs, because they address inputs by slot. Fused results are
   cached under one key: the ordered ops with versions and args, how each reads its inputs, the content
   hashes of the external inputs, and the pinned resources, which include the hash of the ffmpeg binary
   and, for ops that take pictures or draw text, Pillow, the ICC profile and the font pack.
4. **Determinism.** x264 gets an explicit thread count (part of the preset, so of the key), filter threading
   is pinned, no metadata or timestamps are written, and color is normalized (BT.709, limited range, 4:2:0,
   square pixels). Video is a `PERCEPTUAL` class: identical bytes on the same platform and ffmpeg (tested),
   and only structure and content are asserted across ffmpeg versions. There are no video goldens.
5. **Untrusted media.** ffprobe and ffmpeg run only in the sandboxed worker, with the demuxer forced from the
   sniffed type and `-protocol_whitelist file`. Anything file-like a filter needs (overlay images, later fonts
   and text) is written by us into the work directory under a name we generate; images are re-encoded
   through Pillow first. Every output is decoded end to end before it is accepted. A timeout kills the
   worker's whole process group, so ffmpeg dies with it.
5a. **Frame rate.** The probe reports the nominal `r_frame_rate` for constant-rate streams; `avg_frame_rate`
   is off by a frame between ffmpeg versions and is not trusted on short clips.
6. **Previews.** `preview.frames` (hidden) makes a labelled contact sheet of evenly spaced frames: about one
   image's worth of tokens for six frames. `Engine.preview` and the agent's `preview` tool use it for videos.

## Consequences
- The catalogue grows by five ops, one recipe and four presets (~1.2k tokens total, well inside the budget).
- Trim is frame-accurate and always re-encodes; a stream-copy fast path for keyframe-aligned cuts is not built.
- Time-range problems (`end` past the duration) are reported when the step runs, in terms of what the earlier
  steps produced, not at validation time; validating against cached probes is a possible improvement.
- A video result's `JobResult.steps` lists runs, not spec steps, and says how many steps were fused.
- The eval suite has no video tasks yet, and the thesis gate has not been evaluated for video.
