"""Audio/video support: probing, the typed filtergraph builder and ffmpeg execution.

Everything here runs inside sandboxed workers, because media files are untrusted. Commands are
always argv lists (never a shell), user text reaches ffmpeg through files, and protocols are
restricted to local files (ADR-0004, docs/10-security.md).
"""
