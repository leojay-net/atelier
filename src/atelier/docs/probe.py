"""Describe a document for the agent and for the checks (runs in the sandboxed probe worker)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from atelier.media import MediaType
from atelier.ops.registry import OpError


def probe_document(path: Path, media: MediaType) -> dict[str, Any]:
    """Structure and content facts about a PDF or Office file, or {"error": ...} if unreadable."""
    try:
        if media is MediaType.PDF:
            from atelier.docs import pdfdoc

            return pdfdoc.describe(path)
        if media is MediaType.DOCX:
            from atelier.docs import word

            return word.describe(path)
        if media is MediaType.PPTX:
            from atelier.docs import slides

            return slides.describe(path)
        if media is MediaType.XLSX:
            from atelier.docs import sheet

            return sheet.describe(path)
    except OpError as e:
        return {"error": str(e)}
    return {}
