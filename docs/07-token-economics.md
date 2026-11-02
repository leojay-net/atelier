# 7. Token Economics

The product thesis: **fewer tokens per completed job, with a higher and more consistent quality floor.** This doc explains where tokens go, what Atelier does about each item, and how the claims get measured instead of assumed.

## 7.1 Where tokens go in a naive agent

For a typical "edit this video for Reels with captions" task done by a general coding agent:

| Cost driver | Why it's expensive |
|---|---|
| Writing code | Every run re-derives ffmpeg flags, filtergraphs and library calls as output tokens (the most expensive kind) |
| Error loops | Opaque errors → rewrite → re-run; each loop resends the growing transcript |
| Raw tool output | ffmpeg logs, stack traces and file listings flood the context |
| Visual verification | Full-size frames or screenshots, often several per iteration |
| Large tool surface | Many generic tools (bash, read, write, edit, glob, web…) with long definitions in every request |
| Uncached prefixes | Dynamic system prompts that change per request |

## 7.2 What Atelier does about each

| Driver | Atelier mechanism | Effect |
|---|---|---|
| Writing code | Spec only; linear shorthand; recipes | ~100–300 output tokens for a typical job's spec |
| Error loops | Strict schemas + full pre-validation + actionable errors + hints | Most jobs need 0 retries; a retry costs ~1 short turn |
| Raw tool output | One-line summaries; failing checks only | Tool results ~50–200 tokens |
| Visual verification | Checks first; escalation ladder; contact sheets; `max_images` | Median 0 images per job |
| Large tool surface | 4 strict tools, sorted and frozen | Small, fully cached definitions |
| Uncached prefixes | Frozen system prompt + catalogue with an explicit breakpoint; automatic tail caching | Fixed prefix read at ~0.1× price |
| Repeats | Plan cache (exact) + batch generalization | 0 tokens for repeats; 1 agent run for N files |
| Over-thinking | Adaptive thinking; effort pinned per route and tuned by evals | Thinking scales with difficulty |

## 7.3 Illustrative budget (targets, to be measured)

These are **design targets** for the eval suite, not measured results. Real numbers come from Phase 2 telemetry (see [Roadmap](08-roadmap.md)).

**Job:** "Cut 0:05–0:20 from this clip, add clean captions, export for Reels."

| | Naive coding agent (typical) | Atelier target |
|---|---|---|
| Model requests | 8–15 | 2–3 |
| Output tokens | 4k–12k | 0.5k–1.5k (incl. thinking) |
| Input tokens (uncached) | 40k–150k cumulative | 1k–3k |
| Input tokens (cache reads) | varies | ~4k × turns at ~0.1× price |
| Images sent | 3–10 | 0 (median) |
| Result consistency | Varies by run | Deterministic given the spec |

**Batch of 200 product photos:** naive ≈ 200 × a per-image loop, or one long script-writing session. Atelier = **one** agent run on a sample + 199 zero-token engine runs.

## 7.4 Measuring it

Every model request records its `usage` fields (see [Agent §4.9](04-agent-layer.md#49-telemetry-per-request-per-job)). A dashboard reports, per route and domain:

- **tokens per completed job** (input uncached / cache read / cache write / output, and blended $ at the configured model's price),
- **turns per job**, **images per job**, **first-pass check rate**, **plan-cache hit rate**, **prefix cache-read ratio**.

### Guardrail tests
- CI: a second identical request must show `cache_read_input_tokens > 0` (catches silent cache invalidators such as a timestamp slipping into the system prompt).
- Eval suite: per-task budgets. A prompt or catalogue change that raises median tokens per job by more than 10% fails review unless justified by a quality gain.

### Tuning levers, in order
1. **Free wins first:** caching correctness, shorter tool results, better errors and hints, more recipes.
2. **Effort per route:** lower effort where evals show no quality loss.
3. **Model choice per route:** only if evals support it, and as an explicit product decision.
