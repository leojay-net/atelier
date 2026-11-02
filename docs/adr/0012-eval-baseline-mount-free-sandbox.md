# ADR-0012: The eval baseline is a bash-only agent in a mount-free Docker sandbox

- **Status:** Accepted
- **Date:** 2026-09-25 (decided under delegated authority)

## Context
The Phase 2 gate needs "5x fewer tokens per completed job than a general coding agent, at equal or
better pass rate", so a baseline must exist. It has to be a fair opponent, and because it runs
model-written shell commands it has to be safe.

## Options considered
1. **Drive an existing coding-agent CLI headlessly.** Least code, but its prompts, tools and caching
   are outside our control and its token accounting differs from ours.
2. **Our own Messages API loop with a bash tool, in Docker, with the task's work directory bind-mounted.**
   Simple, but a host directory is exposed to model-run code, needs uid fiddling on Linux, and does not
   work with remote daemons or rootless Docker. It also hung outright on Docker Desktop when file sharing
   failed (see Consequences).
3. **The same loop, with no mounts:** inputs go in and outputs come back as tar streams through
   `docker exec`, and `/work` is a small tmpfs.

## Decision
Option 3 (`atelier/evals/sandbox.py`, `baseline.py`, `docker/baseline.Dockerfile`).
- **Fair:** one `bash` tool and nothing else (a leaner tool list means fewer baseline tokens, so it is the
  conservative choice); the same model, effort, adaptive thinking, automatic prompt caching and "ask
  instead of guessing" instruction as the Atelier agent; the same cost units.
- **Safe:** throwaway container, no network, read-only root, all capabilities dropped, no privilege
  escalation, memory/CPU/process limits, small tmpfs mounts (which also bound disk), a timeout per command
  and per Docker call, commands passed as one argv element. **No host path is visible to the container.**
  What comes back is only regular files from `out/`, extracted defensively (no links, devices or
  directories, names flattened, counts and sizes capped).
- **Comparable:** the baseline has no spec and no engine checks, so both sides are compared on the
  *portable* graders only (dimensions, format, size, transparency, asking-vs-guessing, rubric). "Completed"
  means the same on both sides in the gate's token and pass-rate criteria.

## Consequences
- The baseline image (`atelier-baseline`) has Pillow and NumPy at the versions in `uv.lock`, a system font,
  and none of Atelier, so it cannot import the engine.
- "Asked instead of guessing" is approximated for the baseline: no deliverables and a final reply ending in
  "?". Each command runs in a fresh shell (files persist, shell state does not) and the system prompt says so.
- **Why no mounts:** during this work Docker Desktop's directory bind mounts hung, containers stuck in
  `Created`, and Docker Desktop then reported "unable to start" and restarted, stopping the other containers on
  the machine. Whatever the root cause, an evaluation harness must not depend on host file sharing, and must
  bound every Docker call with a timeout so a sick daemon cannot hang a run.
- Building the image needs network once (`docker build -f docker/baseline.Dockerfile -t atelier-baseline
  docker/`). Running the baseline spends tokens; nothing runs it automatically.
