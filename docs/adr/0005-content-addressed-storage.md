# ADR-0005: Content-addressed storage and cache keys

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
We need reproducibility, dedup, cheap re-runs and a way for the model to refer to files without handling bytes or paths.

## Decision
- All artifacts (inputs, intermediates, outputs) are stored by `sha256` of their bytes and referenced as `asset:<hash>`.
- Step results are cached under `step_key = sha256(canonical{engine version, op, op version, canonical args, input hashes, resource IDs})` (see [Determinism §5.3](../05-determinism.md#53-cache-keys)).
- Storage backends: local disk (dev and single node), S3-compatible (production). Dedup is scoped **per org**.
- Garbage collection: reference-counted from jobs and recipes, plus a TTL for unreferenced intermediates.

## Consequences
- Re-running a job or editing one step recomputes only what changed.
- The model sees short, stable handles. Paths never appear in prompts.
- Hashing large videos costs time. Hashing is streamed and done once at upload, and intermediates are hashed as they're written.
