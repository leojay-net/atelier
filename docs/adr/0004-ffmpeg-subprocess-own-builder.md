# ADR-0004: Pinned static ffmpeg via subprocess, with Atelier's own filtergraph builder

- **Status:** Accepted (the static-binary/vendoring part is amended by [ADR-0010](0010-ffmpeg-from-pinned-debian-snapshot.md))
- **Date:** 2026-09-24

## Context
Video and audio work needs ffmpeg. How we call it affects determinism, quality (re-encode count) and security.

## Options considered
1. **System ffmpeg + `ffmpeg-python` / similar wrappers**
   - Cons: host version drift breaks determinism; popular wrappers are thinly maintained; string-built filtergraphs are an injection risk.
2. **PyAV (libav bindings)**
   - Pros: in-process, frame-level access.
   - Cons: bundled libav version tied to the wheel; filter coverage and encoder options are more awkward; crashes take down the worker.
3. **Pinned static ffmpeg/ffprobe binary + own typed filtergraph/argv builder, run via `subprocess` with argv lists**
   - Pros: exact version control (the build ID goes into cache keys); full filter and encoder coverage; process isolation; the builder allows **step fusion** (one decode/encode per output) and safe escaping.
   - Cons: we maintain the builder.

## Decision
Option 3. The ffmpeg build is part of the reference Docker image and is also vendored for dev machines. Commands are always argv lists (`shell=False`). User text reaches ffmpeg through files or a tested escaper.

## Consequences
- The filtergraph builder is a core module with heavy snapshot and property testing.
- The planner can fuse consecutive ffmpeg-backed steps, which reduces generation loss and runtime.
- PyAV may still be used for **read-only analysis** (frame sampling for previews) where convenient.
