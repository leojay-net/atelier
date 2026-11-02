"""`OpenAICompatibleAdapter` (OpenAI, Ollama, ...) driven through the real agent loop, with a
scripted OpenAI-shaped model standing in for the network."""

from __future__ import annotations

import json
from typing import Any

from atelier.agent.config import AgentConfig
from atelier.agent.loop import AgentResult, Job, run_job
from atelier.agent.providers.openai_compatible import OpenAICompatibleAdapter
from atelier.agent.tools import TOOLS
from atelier.engine.engine import Engine
from harness.fake_openai import ScriptedOpenAI, final, message, refusal, run_pipeline


def good(photo: str) -> dict[str, Any]:
    return {
        "in": photo,
        "ops": [["image.resize", {"width": 320}], ["export", {"preset": "web_jpeg"}]],
    }


def go(model: ScriptedOpenAI, engine: Engine, photo: str, **kw: Any) -> AgentResult:
    job = kw.pop("job", None) or Job("make it small", assets=(photo,))
    return run_job(
        job,
        engine=engine,
        adapter=OpenAICompatibleAdapter(model),
        config=kw.pop("cfg", None) or AgentConfig(model="gpt-4o"),
        **kw,
    )


def test_happy_path(engine: Engine, photo: str) -> None:
    model = ScriptedOpenAI(
        run_pipeline(good(photo), usage=(1000, 80, 0)),
        final("Made it.", usage=(120, 40, 100)),
    )
    result = go(model, engine, photo)
    assert result.ok and result.summary == "Made it." and result.question == ""
    assert result.outputs and result.spec == good(photo)
    rec = result.record
    assert rec is not None and rec.turns == 2
    assert (rec.total("input_tokens"), rec.total("output_tokens")) == (1120, 120)
    assert rec.total("cache_read_tokens") == 100  # OpenAI's automatic caching, surfaced as usage


def test_request_shape(engine: Engine, photo: str) -> None:
    model = ScriptedOpenAI(run_pipeline(good(photo)), final())
    go(model, engine, photo)
    first = model.requests[0]
    assert first["model"] == "gpt-4o"
    assert [t["function"]["name"] for t in first["tools"]] == [t["name"] for t in TOOLS]
    assert first["tools"][0]["function"]["strict"] is True
    assert first["response_format"]["json_schema"]["strict"] is True
    assert first["messages"][0]["role"] == "system" and first["messages"][0]["content"]
    assert first["messages"][1]["role"] == "user"


def test_tool_result_round_trip(engine: Engine, photo: str) -> None:
    model = ScriptedOpenAI(run_pipeline(good(photo)), final())
    go(model, engine, photo)
    second = model.requests[1]
    assistant = second["messages"][-2]
    assert assistant["role"] == "assistant" and assistant["tool_calls"]
    tool_reply = second["messages"][-1]
    assert tool_reply["role"] == "tool"
    assert tool_reply["tool_call_id"] == assistant["tool_calls"][0]["id"]
    spec = json.loads(assistant["tool_calls"][0]["function"]["arguments"])["spec"]
    assert spec == good(photo)


def test_refusal_maps_to_content_filter(engine: Engine, photo: str) -> None:
    model = ScriptedOpenAI(refusal())
    result = go(model, engine, photo)
    assert result.status == "refused" and result.detail == "content filtered"


def test_max_tokens_is_not_appended_and_retried(engine: Engine, photo: str) -> None:
    model = ScriptedOpenAI(message(content="(cut off)", finish_reason="length"), final())
    result = go(model, engine, photo)
    assert result.status == "no_output" and len(model.requests) == 2
    # the truncated turn was dropped, not echoed: history is unchanged between attempts
    assert len(model.requests[1]["messages"]) == len(model.requests[0]["messages"])
    assert model.requests[1]["max_tokens"] > model.requests[0]["max_tokens"]
