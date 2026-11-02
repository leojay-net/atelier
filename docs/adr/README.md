# Architecture Decision Records

Each ADR records one significant decision: the context, the options considered, the choice and its consequences. ADRs are immutable once accepted. To change a decision, write a new ADR that supersedes the old one.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-python-engine.md) | Python for the engine and application | Accepted |
| [0002](0002-declarative-spec-interface.md) | A declarative pipeline spec is the model's only write interface | Accepted |
| [0003](0003-claude-api-custom-loop.md) | Claude Messages API with an Atelier-owned loop, instead of the Claude Agent SDK | Accepted |
| [0004](0004-ffmpeg-subprocess-own-builder.md) | Pinned static ffmpeg via subprocess, with Atelier's own filtergraph builder | Accepted |
| [0005](0005-content-addressed-storage.md) | Content-addressed storage and cache keys | Accepted |
| [0006](0006-pdf-rendering-library.md) | pypdfium2 + pikepdf/pypdf for PDF (not PyMuPDF) | Accepted |
| [0007](0007-django-app-layer.md) | Django for the app layer (instead of FastAPI) | Accepted |
| [0008](0008-agent-layer-implementation-decisions.md) | Agent layer: implementation decisions that differ from the design doc | Accepted |
| [0009](0009-warm-standby-workers.md) | Warm standby workers: hide start-up cost, keep one fresh process per step | Accepted |
| [0010](0010-ffmpeg-from-pinned-debian-snapshot.md) | ffmpeg from a pinned Debian snapshot in the reference image (amends ADR-0004) | Accepted |
| [0011](0011-short-asset-handles.md) | Specs accept short asset handles | Accepted |
| [0012](0012-eval-baseline-mount-free-sandbox.md) | The eval baseline is a bash-only agent in a mount-free Docker sandbox | Accepted |
| [0013](0013-video-engine-graph-ops-and-fusion.md) | Video engine: graph ops, fusion, and one polymorphic `export` | Accepted |
| [0014](0014-audio-ops-and-loudness-normalization.md) | Audio ops and two-pass loudness normalization | Accepted |
| [0015](0015-text-and-captions-as-pictures.md) | Titles and captions as rendered pictures | Accepted |
| [0016](0016-document-engine.md) | The document engine: safe packages, deterministic writers, honest redaction | Accepted |
| [0017](0017-app-layer-queue-and-tenancy.md) | The application layer: a database queue, per-organization stores, keys, events | Accepted |
| [0018](0018-ml-plugins-and-pinned-models.md) | ML plugins and pinned models | Accepted |
| [0019](0019-multi-provider-model-adapter.md) | A `ModelAdapter` seam for Claude, OpenAI, Gemini and Ollama | Accepted |

Template: [`template.md`](template.md)
