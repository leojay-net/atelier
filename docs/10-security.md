# 10. Security

Atelier processes **untrusted files** and runs **model-generated instructions**. Both are attack surfaces. The design limits blast radius structurally rather than relying on the model to behave.

## 10.1 Model-side containment
- **No shell, code-execution or filesystem tools.** The model can only submit specs referencing registered ops (P1). This is the most important control.
- **Strict schemas** with `additionalProperties: false`. Unknown args are rejected before execution.
- **No free-form strings reach a shell or filter parser unescaped.** User text (captions, titles, watermark text) goes to ffmpeg through files (for example `textfile=` / subtitle files) or through a dedicated escaper with unit tests. It is never interpolated into filtergraph strings raw.
- **Asset handles only.** The model never names host paths or URLs. The app layer resolves handles inside the job's sandbox directory.
- **Prompt injection via content:** text extracted from documents, subtitles or metadata is returned to the model as **data** in tool results, clearly delimited. Because the model's capabilities are limited to spec ops on the job's own assets, injected instructions cannot exfiltrate data or reach other jobs. Outward-facing actions (email, publish, upload to third parties) are **not** agent tools. If added later, they require explicit user confirmation in the app.

## 10.2 Untrusted media & documents
- ffmpeg: `-protocol_whitelist file,pipe`, no network protocols; input format probing limits; `-xerror` on critical failures.
- Pillow: explicit `MAX_IMAGE_PIXELS` (decompression-bomb protection); reject unsupported or exotic formats by default.
- Office/zip: limits on total uncompressed size, entry count and compression ratio (zip bombs); no macro execution (python-docx/openpyxl don't run macros; LibreOffice is run with macros disabled); a package with a macro project, or with a relationship a converter would fetch (linked image, remote template, linked object), is refused before any library parses it. Implemented in `atelier/docs/package.py` and `docs/convert.py` ([ADR-0016](adr/0016-document-engine.md)).
- PDF: pikepdf/qpdf parse with limits; JavaScript and embedded files are stripped on output by default; redaction removes content, not just draws over it.
- Every job runs in a **worker subprocess** with CPU time, wall time, memory and output size limits, and a per-job temp directory that is deleted after the job. Each step gets its own single-use process; it may be started ahead of time as a warm standby ([ADR-0009](adr/0009-warm-standby-workers.md)) but is never reused.
- Containers run as non-root, read-only root filesystem, no outbound network for workers (except ML weight fetch at build time, not runtime).

## 10.3 Resource & cost limits
- Per-spec limits: max output resolution, duration, pages, steps, total input size.
- Per-job agent budgets: turns, output tokens, images.
- Per-org quotas on compute minutes and token spend. Hard stops return partial results with a clear message.

## 10.4 Secrets & data
- **Model-written shell commands** (only the eval baseline runs any, never the product) execute solely in a throwaway container with no network, no host mounts, a read-only root and dropped capabilities; only regular files come back ([ADR-0012](adr/0012-eval-baseline-mount-free-sandbox.md)).
- The Anthropic API key and cloud credentials live only in the API/agent service environment (or a secret manager), never in workers and never in prompts.
- Assets are encrypted at rest (S3 SSE); signed, expiring URLs for downloads.
- Retention policy per org; content-addressed dedup is **scoped per org** so one org's hashes never reveal another's content.
- Logs record specs, summaries and usage metrics, not document text or media content, unless an org enables debug retention.
