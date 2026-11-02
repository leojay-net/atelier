# ADR-0010: ffmpeg from a pinned Debian snapshot in the reference image (amends ADR-0004)

- **Status:** Accepted
- **Date:** 2026-09-25 (decided under delegated authority)

## Context
ADR-0004 chose "pinned static ffmpeg/ffprobe, part of the reference image and also vendored for dev
machines". What it needs is exact version control with the build ID in cache keys. The reference
Dockerfile installs ffmpeg 7.1.5 from `snapshot.debian.org` at a fixed date, the same mechanism that
pins LibreOffice and qpdf.

## Options considered
1. **Static build** (vendored binary): identical bytes of ffmpeg everywhere, including dev machines.
   Costs: a third-party binary to download, verify and update; GPL/licence review of that build; and it
   still would not make lossy encodes bit-identical across CPUs.
2. **Debian snapshot in the reference image**: exact version pinned by date, one supply chain for all
   system packages, nothing to vendor. Dev machines use whatever ffmpeg they have.

## Decision
Option 2. Phase 3 will put the ffmpeg build ID (version + configuration string) into every
ffmpeg-backed step's cache key (as ADR-0004 already intends), so a different ffmpeg on a dev machine
misses the cache instead of returning a wrong hit. No ffmpeg step exists yet.
Video goldens are pinned only in the reference image and skipped elsewhere, as lossy image encoders
already are. The rest of ADR-0004 stands: argv-list subprocess calls and Atelier's own filtergraph
builder.

## Consequences
- No vendored binaries. `docker/Dockerfile` remains the single source of the pinned toolchain.
- Dev machines cannot run video goldens; perceptual tests (SSIM/PSNR) cover them.
- If a dev-machine parity need appears later, adding a static build is a new ADR, not a rewrite.
