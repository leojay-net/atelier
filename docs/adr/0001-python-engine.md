# ADR-0001: Python for the engine and application

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
The engine must cover images, video/audio and four document formats with mature, well-maintained libraries. It also hosts the agent layer, which needs an official Anthropic SDK.

## Options considered
1. **Python**
   - Pros: the strongest library coverage for every domain (Pillow, pyvips, OpenCV, python-docx, docxtpl, openpyxl, python-pptx, pypdf, pikepdf, pypdfium2, OCRmyPDF, PySceneDetect, faster-whisper, rembg); official Anthropic SDK; Pydantic for strict schemas.
   - Cons: slower in pure-Python hot loops; the GIL.
2. **TypeScript/Node**
   - Pros: good for web; sharp is excellent for images.
   - Cons: weak Office/PDF editing ecosystem; ML plugins would need a Python sidecar anyway.
3. **Rust/Go core + bindings**
   - Pros: performance and safety.
   - Cons: months of work re-wrapping libraries that already exist in Python; slower iteration.

## Decision
Python 3.11+, dependencies locked with `uv`. Heavy work is delegated to native code (ffmpeg, libvips, pdfium, qpdf), so Python is orchestration, not the hot path. Parallelism comes from worker processes, not threads.

## Consequences
- Performance-critical paths must stay in native libraries. Per-pixel Python loops are banned in ops.
- Pydantic models become the single source of truth for op params, tool schemas and docs.
- If a TS web frontend is built, it consumes the HTTP API; no engine logic in JS.
