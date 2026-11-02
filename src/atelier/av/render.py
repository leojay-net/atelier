"""Run a finished graph and turn its output into an artifact with verified metadata.

Every rendered file is decoded end to end before it is accepted (the `playable` check's
evidence) and probed, so downstream checks judge what a viewer would receive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from atelier.av.analysis import analyze
from atelier.av.export import mezzanine
from atelier.av.graph import Graph, decode_check, measure_file
from atelier.av.probe import probe_av
from atelier.media import MediaType
from atelier.ops.registry import Artifact, OpError


def render(g: Graph, out_dir: Path) -> Artifact:
    if g.encode is None:
        mezzanine(g)  # the last step was not `export`
    assert g.encode is not None
    out = out_dir / f"output{g.encode.ext}"
    g.execute(out)
    problem = decode_check(out)
    if problem is not None:
        raise OpError(f"the encoded output does not decode cleanly: {problem}")
    probed = probe_av(out, g.encode.ext)
    loudness = None
    if (g.encode.info or {}).get("loudness") and probed.get("audio"):
        after = measure_file(out)  # what a viewer will hear, measured on the finished file
        if after is not None:
            loudness = {
                "integrated": after["input_i"],
                "true_peak": after["input_tp"],
                "range": after["input_lra"],
            }
    analysis = None
    if g.encode.info:  # an export, not an intermediate that another step will read
        analysis = analyze(
            out,
            video=bool(probed.get("video")),
            audio=bool(probed.get("audio")),
            duration=probed.get("duration"),
        )
    meta: dict[str, Any] = {
        **probed,
        "bytes": out.stat().st_size,
        "verified": True,  # decoded end to end without errors
        "export": g.encode.info or None,
        "loudness": loudness,
        "analysis": analysis,
        "boxes": g.notes.get("boxes", []),
        "graph": g.filtergraph(),
    }
    media = MediaType.VIDEO if probed.get("video") else MediaType.AUDIO
    return Artifact(path=out, media=media, ext=g.encode.ext, meta=meta)
