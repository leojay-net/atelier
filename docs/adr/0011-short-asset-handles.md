# ADR-0011: Specs accept short asset handles

- **Status:** Accepted
- **Date:** 2026-09-25 (decided under delegated authority)

## Context
Handles are `asset:` plus a 64-hex SHA-256. Every handle the model writes is output tokens, and hex is
expensive to tokenize. Tool results and asset lines already show 12 hex characters.

## Decision
A spec may refer to an asset by a full handle or by a unique prefix of at least 8 hex characters.
The validator resolves it against the store and canonicalization expands it to the full hash, so
canonical specs, cache keys and plan-cache keys never contain a prefix. An unknown prefix or one
matching several assets is a normal, path-precise validation error.

## Consequences
- Saves tokens on every handle the model writes (a 64-hex handle against a 12-hex one; the saving is an
  estimate, to be measured in the live eval, not a measured number).
- A collision needs two assets sharing 8+ hex digits: ~1e-4 for a store of 1000 assets, and it fails
  loudly (ambiguous handle), never silently.
- **Resolution must be scoped to the caller's store.** In the app layer (Phase 5) each organization
  has its own store; a prefix must never resolve into another tenant's assets.
