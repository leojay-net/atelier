"""The rubric grader: LLM-as-judge for aesthetic tasks (docs/09 §9.4, grader kind 3).

The judge sees the original and the result as small images and a numbered rubric, and must
answer with strict JSON. Deterministic graders and checks still run first and decide most of a
task; the rubric only covers what they cannot ("is the title readable and clear of the
subject?").

Calibration against human labels on a held-out subset is a process step, not code: until it
has been done, treat rubric results as indicative and keep them out of the release gate.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from atelier.agent.client import MessagesClient
from atelier.agent.tools import MEDIA_TYPES
from atelier.engine.engine import Engine
from atelier.evals.graders import GraderResult

JUDGE_SYSTEM = (
    "You are a strict reviewer of images produced by an automated editing pipeline. You are "
    "shown the original image(s) and the result, then a numbered rubric. Judge only what is "
    "visible. A criterion passes only if it is clearly met."
)

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "failed_criteria": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["passed", "failed_criteria"],
    "additionalProperties": False,
}


def _image_block(engine: Engine, handle: str) -> dict[str, Any]:
    import base64

    result = engine.preview("thumb", [handle], {"long_edge": 768})
    rep = result.outputs["preview"]
    data = base64.standard_b64encode(engine.store.path(rep.hash).read_bytes()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": MEDIA_TYPES[rep.ext], "data": data},
    }


def judge(
    client: MessagesClient,
    *,
    model: str,
    engine: Engine,
    request: str,
    rubric: Sequence[str],
    originals: Sequence[str],
    outputs: Sequence[str],
) -> GraderResult:
    content: list[dict[str, Any]] = []
    for label, handles in (("Original", originals), ("Result", outputs)):
        for handle in handles:
            content.append({"type": "text", "text": f"{label}:"})
            content.append(_image_block(engine, handle))
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(rubric, 1))
    content.append({"type": "text", "text": f"Request: {request}\n\nRubric:\n{numbered}"})
    started = time.perf_counter()
    resp = client.create(
        {
            "model": model,
            "max_tokens": 2000,
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": "medium",
                "format": {"type": "json_schema", "schema": JUDGE_SCHEMA},
            },
            "system": JUDGE_SYSTEM,
            "messages": [{"role": "user", "content": content}],
        }
    )
    took = f"{time.perf_counter() - started:.1f}s"
    if resp.stop_reason != "end_turn":
        return GraderResult("rubric", False, f"judge stopped with {resp.stop_reason} ({took})")
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        verdict = json.loads(text)
        passed = bool(verdict["passed"]) and not verdict["failed_criteria"]
        failed = [str(c) for c in verdict["failed_criteria"]]
    except (ValueError, KeyError, TypeError):
        return GraderResult("rubric", False, "the judge did not return valid JSON")
    return GraderResult("rubric", passed, "; ".join(failed) or "all criteria met")
