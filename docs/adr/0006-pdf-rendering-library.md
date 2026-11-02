# ADR-0006: pypdfium2 + pikepdf/pypdf for PDF (not PyMuPDF)

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
PDF work needs (a) structural editing (merge, split, forms, metadata, redaction) and (b) rendering pages to images for previews and checks. PyMuPDF covers both well, but it is licensed AGPL-3.0 (or commercial). That would impose AGPL obligations on a hosted Atelier or require buying a license.

## Options considered
1. **PyMuPDF**: excellent all-rounder; AGPL/commercial licensing.
2. **pypdfium2 (rendering, Apache-2.0/BSD) + pikepdf (structure, MPL-2.0, on qpdf) + pypdf (BSD, pure-Python convenience)**: permissive licenses; slightly more glue code.

## Decision
Option 2. pypdfium2 for rendering and text extraction, pikepdf for structural edits and deterministic saves, pypdf where it's simpler. OCR via OCRmyPDF (MPL-2.0) with pinned Tesseract.

## Consequences
- Keeps the whole stack permissively licensed for a hosted product.
- True redaction needs careful implementation (remove content-stream operators and the text layer, not just draw boxes), covered by the `redaction_complete` check.
- Revisit if a commercial PyMuPDF license is acquired.
