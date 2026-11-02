# ADR-0009: Warm standby workers: hide start-up cost, keep one fresh process per step

- **Status:** Accepted
- **Date:** 2026-09-25 (decided under delegated authority)

## Context
Every step ran in its own fresh worker process, ~0.35 s of which ~0.24 s is importing NumPy, Pillow,
pydantic and the ops. A 4-step pipeline spent about 1.1 s on start-up. The security design (docs/10)
requires a fresh, resource-limited process per step with a stripped environment.

## Options considered
1. **Long-lived workers reused across steps.** Fast, but state leaks between steps and jobs, and
   CPU/memory limits accumulate instead of applying per step. Rejected: it removes the isolation the
   design depends on.
2. **Fork each step from a warm template process.** Fresh copy-on-write children and no import cost, so
   it also raises batch throughput (potentially ~10x on Linux). Needs a socket protocol and is unsafe
   on macOS (Accelerate/libdispatch after fork), so it would be Linux-only with a second code path.
3. **Warm standby workers.** Start a few fresh processes ahead of time; each finishes its imports,
   waits for exactly one job on stdin, runs it and exits; a replacement is started at once.

## Decision
Option 3 (`atelier/engine/pool.py`). Kept from a cold start: one process per step and never reused;
the stripped environment (a standby is used only if started with exactly the environment the job
needs); per-step resource limits (applied when the job arrives); wall-time kills the whole process
group. Added: standbys start in a private empty directory with `-P` so nothing in the caller's cwd can
shadow an import, and they exit when their parent's pipe closes. The pool tops itself up only when the
requester is the sole job in flight: standbys help sequential work and only add load to a batch.
`ATELIER_WARM_WORKERS` sets the count (default 2; 0 = always cold).

## Consequences
- Measured on an 8-core Mac: a 4-step pipeline 1.1-1.4 s -> 0.34-0.42 s (~3x); the fast suite 107 s ->
  64 s. A 60-image batch at 8 workers is unchanged (9-12 s either way): it is CPU-bound on import work
  that this design moves off the critical path but does not remove.
- Two idle processes (~100 MB each) live for the process lifetime; they are killed at exit.
- Warm and cold workers produce identical bytes (tested, and the 500-image determinism test passes).
- Not done: the fork-from-template design (option 2) for batch throughput on Linux. Revisit if batch
  speed becomes a product need; it should be measured against a real workload first.
- Heavy optional plugins (ML models) must not become builtin ops without revisiting `_warm()`, since
  every standby would import them.
