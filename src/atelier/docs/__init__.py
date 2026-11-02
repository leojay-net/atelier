"""Document engine: PDF, DOCX, XLSX and PPTX (docs/03 §3.4, ADR-0016).

Office files are zip packages and PDFs are object graphs; both are untrusted input. Everything
here opens them through `package` (size and entry limits, no macros, no external content) and
writes them back deterministically (fixed timestamps and properties, sorted entries), so the
same edit of the same file gives the same bytes.
"""
