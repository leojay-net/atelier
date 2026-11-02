# 1. Vision & Principles

## The problem

A general-purpose AI agent *can* edit media today. It writes a Python or ffmpeg script, runs it, looks at the output and fixes what's wrong. That works, but it has three structural costs:

1. **Token cost.** Every edit starts from scratch. The model reasons about library APIs, writes code, reads errors, rewrites code, then looks at full-size images to judge the result. A simple "crop, resize, add watermark" can take 8–15 turns.
2. **Inconsistency.** The same request on two different days can produce different code, different encoder settings and different results. Expertise such as correct loudness targets, safe caption margins or proper color handling is rediscovered (or forgotten) on every run.
3. **Fragility.** Generated code touches the shell and the filesystem directly. Errors are opaque (`ffmpeg exited with code 1`), so retries are expensive guesswork.

## The approach

Atelier moves everything that *can* be deterministic out of the model and into an engine:

| Concern | General-purpose agent | Atelier |
|---|---|---|
| Deciding *what* edit to make | Model | Model |
| Deciding *how* (flags, codecs, filters) | Model, every time | Engine (ops + presets, tuned once) |
| Executing | Model-written code | Engine, deterministic |
| Checking quality | Model looks at output | Engine measures first; model looks only if needed |
| Reporting | Raw logs into context | One-line summaries |

The model's job shrinks to **translating intent into a small, validated spec** and **making the few judgment calls that really need taste**.

## Design principles

Every design decision in these docs traces back to one of these seven rules.

### P1. The model writes specs, never code
The only thing the agent can produce is a pipeline spec (JSON) that references registered ops. There is no bash tool, no code-execution tool and no file-write tool. This caps how much the model can do wrong and how many tokens it can spend doing it.

### P2. Big operations, few calls
One `run_pipeline` call runs an entire multi-step edit. Tools are coarse-grained. The agent has **4 tools**, not 40. Tool definitions are sent on every request, so a small surface is also a token saving.

### P3. Expertise lives in presets and recipes
`export: "reels_1080x1920"` holds codec, bitrate, pixel format, color tags, loudness target and caption safe-zones, all decided once by a human and tested. Quality comes from the preset, not from the model's guess.

### P4. Short results, file references not file contents
Tools return short text such as `ok out.mp4 · 15.00s · 1080x1920 · 30fps · 4.2MB · -14.1 LUFS · checks 9/9`. Files are passed around as content-addressed handles (`asset:3f2a…`), never as bytes.

### P5. Measure first, look second
Quality checks (dimensions, duration, loudness, text overflow, placeholder leftovers, safe-area violations…) run in code on every job. The model receives an image **only** when a check fails or when the task is visual by nature. Even then it gets the smallest useful preview: a thumbnail, a contact sheet or a zoomed crop.

### P6. Same input + same spec = same output
Pinned binaries, pinned library versions, fixed seeds, explicit parameters, normalized metadata. Outputs are cached by a hash of everything that affects them, so a repeat run costs zero compute and zero tokens. See [Determinism](05-determinism.md).

### P7. Errors are instructions
Validation happens before execution and errors say exactly how to fix the problem:
`steps[2].args.font: "Inter Bold" not installed. Available: Inter, Inter-SemiBold, NotoSans, Roboto.`
One precise error means one cheap retry, not a debugging session.

## What "optimal at all times" realistically means

An honest boundary:

- The **engine** can be fully deterministic and tested. Given a spec, the output is guaranteed.
- The **model** is not fully deterministic. Two phrasings of a request may produce slightly different specs.

Atelier handles this by **shrinking the model's decision surface**:
- **Recipes** turn common tasks into "pick a recipe, fill 2–3 slots."
- **Presets** remove low-level choices entirely.
- **Strict schemas** make invalid specs impossible to submit.
- A **plan cache** replays an identical request on identical inputs without calling the model at all.
- **Checks** catch anything that slips through, before the user sees it.

The result: quality has a high floor set by the engine, and the model only contributes judgment on top of it.

## Non-goals (v1)

- A timeline-based GUI editor like Premiere or Figma. Atelier is spec-driven; a UI may *visualize* specs later.
- Real-time or live processing.
- Model-generated pixels as a core feature. Generative ops (inpainting, TTS, upscaling) are **plugins** with their own determinism class. See [Engine](03-engine-and-spec.md#determinism-classes).
- Arbitrary user code inside pipelines.
