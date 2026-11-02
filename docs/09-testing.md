# 9. Testing Strategy

Four test layers. The first three cost zero tokens and run on every commit. The fourth (agent evals) spends tokens and runs on prompt/catalogue changes and nightly.

## 9.1 Unit & property tests (engine)
- Spec validator: every error code has a test; error messages are snapshot-tested because they are part of the product (P7).
- **Property-based tests (Hypothesis):**
  - canonicalization is idempotent (`canon(canon(s)) == canon(s)`)
  - equivalent specs → identical cache keys
  - random valid specs never crash the planner
  - random invalid specs always produce ≥ 1 error with a valid `path`
- Filtergraph builder: generated argv snapshot tests; no shell strings, ever.

## 9.2 Golden tests (bit-exact)
- Run inside the **reference Docker image** in CI.
- Each `BIT_EXACT` / `PERCEPTUAL` op × representative fixtures → expected SHA-256 stored in `tests/golden/*.json`.
- Image outputs are hashed by **decoded content** (mode, size, pixels), not file bytes, because PNG deflate streams from zlib-ng can differ between CPU architectures while the pixels are identical. Byte-level determinism on one platform is covered by repeat-run tests (e.g. the 500-image batch run twice).
- Lossy-encoder cases (JPEG/WebP/AVIF) are `reference_only`. Everywhere else they are covered by a quality floor (SSIM/PSNR against the lossless source).
- Updating a golden hash requires an op version bump and a changelog entry (enforced by a CI check).

## 9.3 Perceptual tests
- Run on developer machines and non-reference platforms.
- Compare output to reference artifacts with SSIM ≥ 0.995 and PSNR ≥ 45 dB (images/video frames); loudness ±0.1 LU; for docs, rendered page diffs.
- Video: sample frames at fixed timestamps + full-stream duration and stream-layout checks.

## 9.4 Agent evals

The eval suite is the only place where quality and token cost are judged together.

### Suite composition
- ~40 tasks per domain (image, video/audio, docs), grown from real user requests over time.
- Each task has: inputs, the request text, **graders** and a **token budget**.
- Mix: recipe-shaped (~50%), multi-step custom (~35%), ambiguous/aesthetic (~15%).

### Graders
1. **Deterministic graders:** all checks pass, plus task-specific assertions (duration = 15 s ± 1 frame, logo inside the top-right safe box, no placeholder text…).
2. **Spec graders:** the produced canonical spec matches an accepted set (for tasks with one right answer).
3. **Rubric grader (LLM-as-judge):** for aesthetic tasks. Scores the final preview against a written rubric. Calibrated against human labels on a held-out subset.

### Metrics reported per run
pass rate · tokens per completed task (by type) · turns · images · first-pass check rate · cost per completed task.

### Baseline
The thesis gate compares against the same tasks done by a general coding agent (`atelier eval baseline`):
bash only, in a Docker sandbox, same model and effort ([ADR-0012](adr/0012-eval-baseline-mount-free-sandbox.md)).
It has no spec and no engine checks, so both sides are compared on the *portable* graders only
(dimensions, format, size, transparency, asking-vs-guessing, rubric), and cost is compared in billed input-token
equivalents (uncached 1x, cache write 1.25x, cache read 0.1x, output 5x).

### Process
- Train/validation/test split of tasks. Prompt and catalogue tuning looks only at train and validation; test is the headline.
- Any change to the system prompt, catalogue, tool descriptions, hints, effort or model runs the suite and compares against the last accepted baseline.
- **Acceptance rule:** no pass-rate regression on test; median tokens per job may not rise > 10% without a matching quality gain.

## 9.5 Load & soak
- Worker throughput for typical pipelines (images/s, video real-time factor).
- Memory ceilings under max-resolution inputs; limit enforcement tests (timeouts kill cleanly and report).
