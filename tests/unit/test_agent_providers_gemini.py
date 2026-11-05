"""`GeminiAdapter` driven through the real agent loop, with a scripted Gemini-shaped model
standing in for the network."""

from __future__ import annotations

import json
from typing import Any

from atelier.agent.config import AgentConfig
from atelier.agent.loop import AgentResult, Job, run_job
from atelier.agent.providers.gemini import GeminiAdapter
from atelier.agent.tools import TOOLS
from atelier.engine.engine import Engine
from harness.fake_gemini import ScriptedGemini, final, message, refusal, run_pipeline


def good(photo: str) -> dict[str, Any]:
    return {
        "in": photo,
        "ops": [["image.resize", {"width": 320}], ["export", {"preset": "web_jpeg"}]],
    }


def go(model: ScriptedGemini, engine: Engine, photo: str, **kw: Any) -> AgentResult:
    job = kw.pop("job", None) or Job("make it small", assets=(photo,))
    return run_job(
        job,
        engine=engine,
        adapter=GeminiAdapter(model),
        config=kw.pop("cfg", None) or AgentConfig(model="gemini-2.5-flash"),
        **kw,
    )


def test_happy_path(engine: Engine, photo: str) -> None:
    model = ScriptedGemini(
        run_pipeline(good(photo), usage=(1000, 80, 0)),
        final("Made it.", usage=(120, 40, 100)),
    )
    result = go(model, engine, photo)
    assert result.ok and result.summary == "Made it." and result.question == ""
    assert result.outputs and result.spec == good(photo)
    rec = result.record
    assert rec is not None and rec.turns == 2
    assert (rec.total("input_tokens"), rec.total("output_tokens")) == (1120, 120)
    assert rec.total("cache_read_tokens") == 100  # Gemini's automatic context caching


def test_request_shape(engine: Engine, photo: str) -> None:
    model = ScriptedGemini(run_pipeline(good(photo)), final())
    go(model, engine, photo)
    first = model.requests[0]
    assert first["model"] == "gemini-2.5-flash"
    config = first["config"]
    declared = config.tools[0].function_declarations
    assert [d.name for d in declared] == [t["name"] for t in TOOLS]
    assert config.system_instruction  # the full system prompt, joined
    assert "response_schema" not in config.model_fields_set  # tools + forced JSON don't mix
    (opening,) = first["contents"]
    assert opening.role == "user"


def test_tool_result_round_trip(engine: Engine, photo: str) -> None:
    model = ScriptedGemini(run_pipeline(good(photo)), final())
    go(model, engine, photo)
    second = model.requests[1]
    contents = second["contents"]
    assistant = contents[-2]
    call = assistant.parts[0].function_call
    assert call.name == "run_pipeline" and json.loads(json.dumps(call.args))["spec"] == good(photo)
    reply = contents[-1]
    assert reply.role == "user"
    response = reply.parts[0].function_response
    assert response.id == call.id and response.name == "run_pipeline"


def test_refusal_maps_to_safety(engine: Engine, photo: str) -> None:
    model = ScriptedGemini(refusal())
    result = go(model, engine, photo)
    assert result.status == "refused" and "SAFETY" in result.detail


def test_max_tokens_is_not_appended_and_retried(engine: Engine, photo: str) -> None:
    model = ScriptedGemini(message([], finish_reason="MAX_TOKENS"), final())
    result = go(model, engine, photo)
    assert result.status == "no_output" and len(model.requests) == 2
    assert len(model.requests[1]["contents"]) == len(model.requests[0]["contents"])
    first_max = model.requests[0]["config"].max_output_tokens
    second_max = model.requests[1]["config"].max_output_tokens
    assert second_max > first_max
