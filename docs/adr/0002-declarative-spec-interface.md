# ADR-0002: A declarative pipeline spec is the model's only write interface

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
The product must use few tokens, produce consistent results and be safe with untrusted inputs. The main alternative is to let the model write and run code, which is the common general-agent approach.

## Options considered
1. **Model writes code (Python/ffmpeg) + bash/code-execution tool**
   - Pros: unlimited flexibility.
   - Cons: high output-token cost per job; error loops; inconsistent results; large security surface; expertise not reused.
2. **Many fine-grained tools** (`crop`, `resize`, `trim`… as separate tools)
   - Pros: typed, safe.
   - Cons: one round trip per step (tokens and latency grow with steps); huge tool list in every request.
3. **One declarative pipeline spec (DAG of registered ops) submitted via a single tool**
   - Pros: one call per edit; strict validation before execution; canonicalizable → cacheable → reproducible; expertise in presets and recipes; tiny security surface.
   - Cons: limited to what ops exist; new capabilities require engineering work.

## Decision
Option 3. The model submits specs through `run_pipeline`. There is no code execution and no shell. Linear shorthand and recipe invocation keep specs short.

## Consequences
- Capability growth happens by **adding ops, presets and recipes**. That work is tested once and benefits every job.
- A missing op is a clear product signal (logged when the model reports it can't express a request) and feeds the backlog.
- The catalogue must stay compact: one line per op in the system prompt, depth via `describe_op`.
- A future "expert mode" with code execution would be a separate, explicitly opted-in route with its own budget. It is not part of v1.
