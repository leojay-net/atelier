"""The agent loop, driven by a scripted model (no network)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from atelier.agent.budget import BudgetLimits
from atelier.agent.client import MissingCredentials
from atelier.agent.config import AgentConfig
from atelier.agent.loop import (
    AgentResult,
    Job,
    find_input_name,
    run_batch_job,
    run_job,
)
from atelier.agent.plan_cache import PlanCache, plan_key
from atelier.agent.prompt import FINAL_SCHEMA, catalogue_version, system_blocks
from atelier.agent.telemetry import Telemetry
from atelier.agent.tools import TOOLS
from atelier.engine.engine import Engine
from atelier.media import HandleError
from harness.fake_model import (
    ScriptedModel,
    fallback_marker,
    final,
    message,
    refusal,
    run_pipeline,
    text,
    thinking,
    tool_use,
)

STABLE = ("model", "thinking", "output_config", "system", "tools", "cache_control")


def good(photo: str) -> dict[str, Any]:
    return {
        "in": photo,
        "ops": [["image.resize", {"width": 320}], ["export", {"preset": "web_jpeg"}]],
    }


def failing(photo: str) -> dict[str, Any]:
    top = ["image.text", {"text": "Top headline", "valign": "top", "box": [0.05, 0.0, 0.9, 0.12]}]
    return {"in": photo, "ops": [top, ["export", {"preset": "thumb_youtube"}]]}


def bad(photo: str) -> dict[str, Any]:
    return {"in": photo, "ops": [["image.crop", {"aspect": "16:9", "focus": "middle"}]]}


def go(
    model: ScriptedModel,
    engine: Engine,
    photo: str,
    *,
    prompt: str = "make it small",
    cfg: AgentConfig | None = None,
    **kw: Any,
) -> AgentResult:
    job = kw.pop("job", None) or Job(prompt, assets=(photo,))
    return run_job(job, engine=engine, client=model, config=cfg or AgentConfig(), **kw)


def last_user(request: dict[str, Any]) -> dict[str, Any]:
    return request["messages"][-1]


# -- the happy path and the request shape -------------------------------------------------------


def test_happy_path(engine: Engine, photo: str) -> None:
    model = ScriptedModel(
        run_pipeline(good(photo), usage=(1000, 80, 0, 3000)),
        final("Made it.", usage=(120, 40, 3000, 200)),
    )
    result = go(model, engine, photo)
    assert result.ok and result.summary == "Made it." and result.question == ""
    assert list(result.outputs) == ["s2"] and result.outputs["s2"].startswith("asset:")
    assert result.spec == good(photo)
    assert result.engine_result is not None and result.engine_result.passed
    rec = result.record
    assert rec is not None and rec.status == "ok" and rec.turns == 2
    assert (rec.total("input_tokens"), rec.total("output_tokens")) == (1120, 120)
    assert (rec.total("cache_read_tokens"), rec.total("cache_write_tokens")) == (3000, 3200)
    assert rec.first_pass is True and rec.images == 0 and not rec.plan_cache_hit


def test_request_shape_and_cache_layout(engine: Engine, photo: str) -> None:
    model = ScriptedModel(run_pipeline(good(photo)), final())
    go(model, engine, photo, prompt="Make it small")
    first = model.requests[0]
    assert first["model"] == "claude-opus-5" and first["max_tokens"] == 16_000
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": FINAL_SCHEMA},
    }
    assert first["tools"] == TOOLS and first["cache_control"] == {"type": "ephemeral"}
    assert first["system"] == system_blocks(engine.registry)
    assert [i for i, b in enumerate(first["system"]) if "cache_control" in b] == [
        len(first["system"]) - 1
    ]
    assert "tool_choice" not in first  # forced tool use is rejected by current models
    (opening,) = first["messages"]
    assert opening["role"] == "user" and isinstance(opening["content"], str)
    assert opening["content"].startswith("Make it small\n\nAssets:\nimage · ")
    for request in model.requests[1:]:
        for key in STABLE:  # nothing that feeds the cached prefix may change within a job
            assert json.dumps(request[key], sort_keys=True) == json.dumps(
                first[key], sort_keys=True
            )


def test_history_is_append_only_and_alternates_roles(engine: Engine, photo: str) -> None:
    model = ScriptedModel(
        message([tool_use("describe_op", {"names": ["export"]})], "tool_use"),
        run_pipeline(good(photo)),
        final(),
    )
    go(model, engine, photo)
    sizes = [len(r["messages"]) for r in model.requests]
    assert sizes == [1, 3, 5]
    for earlier, later in zip(model.requests, model.requests[1:], strict=False):
        assert later["messages"][: len(earlier["messages"])] == earlier["messages"]
    roles = [m["role"] for m in model.requests[-1]["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]


def test_parallel_tool_calls_are_answered_in_one_user_message(engine: Engine, photo: str) -> None:
    model = ScriptedModel(
        message(
            [
                tool_use("describe_op", {"names": ["export"]}, id="toolu_A"),
                tool_use("inspect_media", {"assets": [photo]}, id="toolu_B"),
            ],
            "tool_use",
        ),
        final(),
    )
    go(model, engine, photo)
    reply = last_user(model.requests[1])
    assert [b["tool_use_id"] for b in reply["content"]] == ["toolu_A", "toolu_B"]
    assert all(b["type"] == "tool_result" for b in reply["content"])
    assert len(model.requests[1]["messages"]) == 3  # user, assistant, ONE user message


def test_route_effort_is_pinned_per_job(engine: Engine, photo: str) -> None:
    cfg = AgentConfig(route_effort={"repair": "low"})
    model = ScriptedModel(final(question="Which size?"))
    result = go(model, engine, photo, cfg=cfg, job=Job("fix it", (photo,), route="repair"))
    assert model.requests[0]["output_config"]["effort"] == "low"
    assert result.record is not None and result.record.effort == "low"


def test_batch_note_and_asset_lines_in_the_opening_message(engine: Engine, photo: str) -> None:
    model = ScriptedModel(final(question="?"))
    go(model, engine, photo, job=Job("resize them", (photo,), batch_size=5))
    opening = model.requests[0]["messages"][0]["content"]
    assert "will run on 5 files" in opening and "Assets:" in opening


# -- repair loops ------------------------------------------------------------------------------


def test_validation_error_is_fed_back_and_repaired(engine: Engine, photo: str) -> None:
    model = ScriptedModel(run_pipeline(bad(photo)), run_pipeline(good(photo)), final())
    result = go(model, engine, photo)
    assert result.ok and result.record is not None
    assert result.record.first_pass is False and result.record.turns == 3
    (feedback,) = last_user(model.requests[1])["content"]
    assert feedback["is_error"] is True and feedback["content"].startswith("invalid spec")
    assert "focus" in feedback["content"] and "center" in feedback["content"]


def test_check_failure_carries_its_hint_and_is_repaired(engine: Engine, photo: str) -> None:
    model = ScriptedModel(run_pipeline(failing(photo)), run_pipeline(good(photo)), final())
    result = go(model, engine, photo)
    assert result.ok and result.record is not None and result.record.first_pass is False
    (feedback,) = last_user(model.requests[1])["content"]
    assert feedback["is_error"] is True and "hint:" in feedback["content"]


def test_finishing_with_a_failed_check_reports_it(engine: Engine, photo: str) -> None:
    model = ScriptedModel(run_pipeline(failing(photo)), final("Could not fix the margin."))
    result = go(model, engine, photo)
    assert result.status == "checks_failed" and not result.ok
    assert result.outputs  # the best completed output is still returned
    assert result.summary == "Could not fix the margin."


def test_an_earlier_passing_run_beats_a_later_failing_one(engine: Engine, photo: str) -> None:
    model = ScriptedModel(run_pipeline(good(photo)), run_pipeline(failing(photo)), final())
    result = go(model, engine, photo)
    assert result.ok and result.spec == good(photo)


# -- endings that are not a clean success -------------------------------------------------------


def test_the_model_asks_a_question_instead_of_guessing(engine: Engine, photo: str) -> None:
    result = go(ScriptedModel(final(question="What size do you need?")), engine, photo)
    assert result.status == "needs_input" and result.question == "What size do you need?"
    assert result.outputs == {} and result.spec is None


def test_finishing_without_output_or_question(engine: Engine, photo: str) -> None:
    result = go(ScriptedModel(final("I could not do that.")), engine, photo)
    assert result.status == "no_output" and result.summary == "I could not do that."


def test_a_reply_that_is_not_json_is_kept_not_crashed_on(engine: Engine, photo: str) -> None:
    model = ScriptedModel(run_pipeline(good(photo)), message([text("all done")]))
    result = go(model, engine, photo)
    assert result.ok and result.summary == "all done"
    assert any("not valid JSON" in w for w in result.warnings)


def test_refusal_stops_the_job_and_keeps_earlier_output(engine: Engine, photo: str) -> None:
    model = ScriptedModel(run_pipeline(good(photo)), refusal("cyber", "declined"))
    result = go(model, engine, photo)
    assert result.status == "refused" and result.detail == "refusal (cyber): declined"
    assert result.outputs  # the successful run before the refusal is not thrown away
    assert model.calls == 2  # nothing was retried or appended after the refusal


def test_refusal_on_the_first_turn(engine: Engine, photo: str) -> None:
    result = go(ScriptedModel(refusal(None, None)), engine, photo)
    assert result.status == "refused" and result.detail == "refusal (uncategorized)"
    assert result.outputs == {}


def test_max_tokens_is_retried_once_without_appending_the_truncated_turn(
    engine: Engine, photo: str
) -> None:
    model = ScriptedModel(
        message([text("partial")], "max_tokens"), run_pipeline(good(photo)), final()
    )
    result = go(model, engine, photo)
    assert result.ok
    assert model.requests[0]["max_tokens"] == 16_000
    assert model.requests[1]["max_tokens"] == 20_000
    assert model.requests[1]["messages"] == model.requests[0]["messages"]


def test_max_tokens_twice_fails_explicitly(engine: Engine, photo: str) -> None:
    model = ScriptedModel(message([], "max_tokens"), message([], "max_tokens"))
    result = go(model, engine, photo)
    assert result.status == "error" and "max_tokens" in result.detail and model.calls == 2


def test_pause_turn_resumes_with_the_assistant_turn(engine: Engine, photo: str) -> None:
    model = ScriptedModel(
        message([text("working")], "pause_turn"), run_pipeline(good(photo)), final()
    )
    assert go(model, engine, photo).ok
    resumed = model.requests[1]["messages"]
    assert resumed[-1]["role"] == "assistant" and len(resumed) == 2


def test_unexpected_stop_reason_is_an_error_not_silence(engine: Engine, photo: str) -> None:
    result = go(ScriptedModel(message([], "stop_sequence")), engine, photo)
    assert result.status == "error" and "stop_sequence" in result.detail


def test_tool_use_stop_without_a_tool_block_is_an_error(engine: Engine, photo: str) -> None:
    result = go(ScriptedModel(message([text("hm")], "tool_use")), engine, photo)
    assert result.status == "error"


# -- budgets -----------------------------------------------------------------------------------


def test_soft_budget_adds_a_wrap_up_notice_only_on_the_last_turn(
    engine: Engine, photo: str
) -> None:
    cfg = AgentConfig(limits=BudgetLimits(max_turns=3))
    model = ScriptedModel(
        message([tool_use("describe_op", {"names": ["export"]})], "tool_use"),
        run_pipeline(good(photo)),
        final(),
    )
    assert go(model, engine, photo, cfg=cfg).ok
    after_turn_1 = last_user(model.requests[1])["content"]
    after_turn_2 = last_user(model.requests[2])["content"]
    assert [b["type"] for b in after_turn_1] == ["tool_result"]
    assert [b["type"] for b in after_turn_2] == ["tool_result", "text"]
    assert "Finish now" in after_turn_2[-1]["text"]


def test_hard_budget_stops_but_still_runs_the_last_requested_pipeline(
    engine: Engine, photo: str
) -> None:
    cfg = AgentConfig(limits=BudgetLimits(max_turns=2))
    model = ScriptedModel(run_pipeline(failing(photo)), run_pipeline(good(photo)))
    result = go(model, engine, photo, cfg=cfg)
    assert result.status == "budget" and model.calls == 2
    assert result.spec == good(photo) and result.outputs  # the final request's run still counted


def test_hard_budget_by_output_tokens(engine: Engine, photo: str) -> None:
    cfg = AgentConfig(limits=BudgetLimits(max_output_tokens=100))
    model = ScriptedModel(run_pipeline(good(photo), usage=(10, 150, 0, 0)))
    result = go(model, engine, photo, cfg=cfg)
    assert result.status == "budget" and result.outputs and model.calls == 1


def test_preview_counts_against_the_image_budget(engine: Engine, photo: str) -> None:
    cfg = AgentConfig(limits=BudgetLimits(max_images=1))
    look = message([tool_use("preview", {"assets": [photo], "kind": "thumb"})], "tool_use")
    model = ScriptedModel(look, look, run_pipeline(good(photo)), final())
    result = go(model, engine, photo, cfg=cfg)
    assert result.ok and result.record is not None and result.record.images == 1
    blocked = last_user(model.requests[2])["content"][0]
    assert blocked["is_error"] is True and "image budget used (1/1)" in blocked["content"]


# -- fallbacks ---------------------------------------------------------------------------------


def test_after_a_fallback_only_the_live_tool_call_is_answered_and_echoed(
    engine: Engine, photo: str
) -> None:
    declined = message(
        [
            thinking(),
            tool_use("describe_op", {"names": ["export"]}, id="toolu_DECLINED"),
            fallback_marker(),
            text("continuing"),
            tool_use("run_pipeline", {"spec": good(photo)}, id="toolu_LIVE"),
        ],
        "tool_use",
        model="claude-opus-4-8",
    )
    model = ScriptedModel(declined, final())
    result = go(model, engine, photo)
    assert result.ok and result.record is not None and result.record.requests[0].fallback
    assistant = model.requests[1]["messages"][1]
    assert [b.type for b in assistant["content"]] == ["fallback", "text", "tool_use"]
    reply = last_user(model.requests[1])["content"]
    assert [b["tool_use_id"] for b in reply] == ["toolu_LIVE"]


def test_without_a_fallback_thinking_blocks_echo_unchanged(engine: Engine, photo: str) -> None:
    turn = message([thinking(), tool_use("run_pipeline", {"spec": good(photo)})], "tool_use")
    model = ScriptedModel(turn, final())
    go(model, engine, photo)
    assistant = model.requests[1]["messages"][1]
    assert [b.type for b in assistant["content"]] == ["thinking", "tool_use"]


# -- plan cache --------------------------------------------------------------------------------


def test_second_identical_job_costs_zero_tokens(engine: Engine, photo: str, tmp_path: Path) -> None:
    cache, telemetry = PlanCache(tmp_path / "plans"), Telemetry(tmp_path / "t")
    first = ScriptedModel(run_pipeline(good(photo)), final("Made it."))
    a = go(first, engine, photo, plan_cache=cache, telemetry=telemetry)
    assert a.ok
    again = ScriptedModel()  # any call would raise
    b = go(again, engine, photo, prompt="  MAKE it   small ", plan_cache=cache, telemetry=telemetry)
    assert b.ok and b.summary == "Made it." and again.calls == 0
    assert b.outputs == a.outputs and b.spec == good(photo)
    assert b.record is not None and b.record.plan_cache_hit and b.record.turns == 0
    assert [r.plan_cache_hit for r in telemetry.load()] == [False, True]


def test_plan_cache_misses_on_a_different_asset_or_prompt(
    engine: Engine, photo: str, logo: str, tmp_path: Path
) -> None:
    cache = PlanCache(tmp_path / "plans")
    go(ScriptedModel(run_pipeline(good(photo)), final()), engine, photo, plan_cache=cache)
    other_asset = ScriptedModel(final(question="?"))
    go(other_asset, engine, logo, plan_cache=cache)
    other_prompt = ScriptedModel(final(question="?"))
    go(other_prompt, engine, photo, prompt="make it tiny", plan_cache=cache)
    assert other_asset.calls == 1 and other_prompt.calls == 1


def test_failed_or_questioning_jobs_are_not_cached(
    engine: Engine, photo: str, tmp_path: Path
) -> None:
    cache = PlanCache(tmp_path / "plans")
    go(ScriptedModel(run_pipeline(failing(photo)), final()), engine, photo, plan_cache=cache)
    go(ScriptedModel(final(question="?")), engine, photo, plan_cache=cache)
    assert list((tmp_path / "plans").glob("*.json")) == []


def test_a_stale_cached_spec_is_evicted_and_the_model_takes_over(
    engine: Engine, photo: str, tmp_path: Path
) -> None:
    cache = PlanCache(tmp_path / "plans")
    version = catalogue_version(system_blocks(engine.registry))
    digest = engine.store.resolve(photo).hash
    key = plan_key("make it small", [digest], version, AgentConfig().model)
    cache.store(key, {"in": photo, "ops": [["image.gone", {}]]}, "written for an old op")
    model = ScriptedModel(run_pipeline(good(photo)), final("Fresh."))
    result = go(model, engine, photo, plan_cache=cache)
    assert result.ok and result.summary == "Fresh." and model.calls == 2
    hit = cache.lookup(key)
    assert hit is not None and hit.spec == good(photo)  # replaced by the passing plan


# -- telemetry and failures ---------------------------------------------------------------------


def test_a_crash_mid_job_is_still_recorded(engine: Engine, photo: str, tmp_path: Path) -> None:
    class Boom:
        def create(self, request: dict[str, Any]) -> Any:
            raise RuntimeError("network down")

    telemetry = Telemetry(tmp_path / "t")
    with pytest.raises(RuntimeError, match="network down"):
        run_job(
            Job("x", (photo,)),
            engine=engine,
            client=Boom(),
            config=AgentConfig(),
            telemetry=telemetry,
        )
    (record,) = telemetry.load()
    assert record.status == "error" and record.turns == 0


def test_missing_credentials_propagates_and_is_not_counted_as_a_job(
    engine: Engine, photo: str, tmp_path: Path
) -> None:
    class NoKey:
        def create(self, request: dict[str, Any]) -> Any:
            raise MissingCredentials("no credentials")

    telemetry = Telemetry(tmp_path / "t")
    with pytest.raises(MissingCredentials):
        run_job(
            Job("x", (photo,)),
            engine=engine,
            client=NoKey(),
            config=AgentConfig(),
            telemetry=telemetry,
        )
    assert telemetry.load() == []


def test_an_unknown_asset_fails_before_any_request(engine: Engine) -> None:
    model = ScriptedModel()
    with pytest.raises(HandleError):
        go(model, engine, "", job=Job("x", ("asset:00000000",)))
    assert model.calls == 0


# -- batches -----------------------------------------------------------------------------------


def _images(engine: Engine, tmp_path: Path, n: int) -> list[str]:
    handles = []
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (200 + 20 * i, 150), (40 * i, 90, 160)).save(buf, "PNG")
        path = tmp_path / f"sample{i}.png"
        path.write_bytes(buf.getvalue())
        handles.append(engine.store.put_file(path).handle)
    return handles


def test_one_model_run_serves_a_whole_batch(engine: Engine, tmp_path: Path) -> None:
    handles = _images(engine, tmp_path, 3)
    model = ScriptedModel(run_pipeline(good(handles[0])), final("Resized."))
    batch = run_batch_job(
        "resize them all", handles, engine=engine, client=model, config=AgentConfig()
    )
    assert batch.agent.ok and batch.input_name == "in" and batch.error == ""
    assert model.calls == 2  # one agent run, however many files
    assert "will run on 3 files" in model.requests[0]["messages"][0]["content"]
    assert len(batch.items) == 3 and all(i.result and i.result.passed for i in batch.items)
    assert len({i.result.outputs["s2"].hash for i in batch.items if i.result}) == 3  # per file


def test_batch_with_a_fixed_asset_varies_only_the_named_input(
    engine: Engine, logo: str, tmp_path: Path
) -> None:
    handles = _images(engine, tmp_path, 2)
    spec = {"recipe": "watermark_batch", "params": {"in": handles[0], "logo": logo}}
    model = ScriptedModel(run_pipeline(spec), final())
    batch = run_batch_job(
        "watermark them", handles, fixed=[logo], engine=engine, client=model, config=AgentConfig()
    )
    assert batch.input_name == "in" and len(batch.items) == 2
    assert all(i.result and i.result.passed for i in batch.items)
    assert logo[:20] in model.requests[0]["messages"][0]["content"] or "image" in str(
        model.requests[0]["messages"][0]["content"]
    )


def test_batch_that_the_agent_could_not_do_runs_nothing(engine: Engine, tmp_path: Path) -> None:
    handles = _images(engine, tmp_path, 2)
    model = ScriptedModel(final(question="What size?"))
    batch = run_batch_job("resize", handles, engine=engine, client=model, config=AgentConfig())
    assert batch.agent.status == "needs_input" and batch.items == []


def test_batch_reports_when_the_varying_input_cannot_be_found(
    engine: Engine, tmp_path: Path
) -> None:
    handles = _images(engine, tmp_path, 2)
    elsewhere = {"in": handles[1], "ops": [["export", {"preset": "web_jpeg"}]]}  # not the sample
    model = ScriptedModel(run_pipeline(elsewhere), final())
    batch = run_batch_job("x", handles, engine=engine, client=model, config=AgentConfig())
    assert batch.agent.ok and batch.items == [] and "which input" in batch.error


def test_an_empty_batch_is_rejected(engine: Engine) -> None:
    with pytest.raises(ValueError, match="at least one"):
        run_batch_job("x", [], engine=engine, client=ScriptedModel(), config=AgentConfig())


def test_find_input_name_in_every_spec_form(engine: Engine, photo: str, logo: str) -> None:
    short = engine.store.resolve(photo).short
    assert find_input_name(engine, {"in": short, "ops": []}, photo) == "in"
    assert find_input_name(engine, {"in": logo, "ops": []}, photo) is None
    full = {"version": 1, "inputs": {"a": photo, "b": logo}, "steps": []}
    assert find_input_name(engine, full, photo) == "a"
    assert find_input_name(engine, full, logo) == "b"
    recipe = {"recipe": "watermark_batch", "params": {"in": photo, "logo": logo}}
    assert find_input_name(engine, recipe, photo) == "in"
    twice = {"version": 1, "inputs": {"a": photo, "b": photo}, "steps": []}
    assert find_input_name(engine, twice, photo) is None  # ambiguous: refuse to guess
    assert find_input_name(engine, "not a spec", photo) is None
