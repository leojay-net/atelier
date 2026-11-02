"""Budget, config, plan cache, telemetry and the client seam."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from atelier.agent.budget import Budget, BudgetLimits
from atelier.agent.client import AnthropicMessages, MissingCredentials
from atelier.agent.config import FALLBACK_BETA, AgentConfig
from atelier.agent.plan_cache import PlanCache, normalize_prompt, plan_key
from atelier.agent.telemetry import JobRecord, RequestRecord, Telemetry, format_summary, summarize

# -- budget ------------------------------------------------------------------------------------


def test_budget_soft_then_hard_by_turns() -> None:
    b = Budget(BudgetLimits(max_turns=3, max_output_tokens=10_000, max_images=1))
    assert not b.soft_exhausted
    b.charge(100)
    assert not b.soft_exhausted and not b.hard_exhausted
    b.charge(100)  # two of three used: the next request is the last one
    assert b.soft_exhausted and not b.hard_exhausted
    b.charge(100)
    assert b.hard_exhausted


def test_budget_by_output_tokens() -> None:
    b = Budget(BudgetLimits(max_turns=99, max_output_tokens=1000))
    b.charge(750)
    assert b.soft_exhausted and not b.hard_exhausted
    b.charge(250)
    assert b.hard_exhausted


def test_budget_images_and_notice() -> None:
    b = Budget(BudgetLimits(max_images=2))
    assert b.image_allowed()
    b.images = 2
    assert not b.image_allowed()
    assert "Finish now" in b.notice()


# -- config ------------------------------------------------------------------------------------


def test_config_effort_per_route_and_validation() -> None:
    cfg = AgentConfig(effort="high", route_effort={"repair": "low"})
    assert cfg.effort_for("repair") == "low"
    assert cfg.effort_for("ask") == "high"
    with pytest.raises(ValueError, match="effort"):
        AgentConfig(effort="turbo")
    with pytest.raises(ValueError, match="max_tokens_retry"):
        AgentConfig(max_tokens=20_000, max_tokens_retry=10_000)


# -- plan cache --------------------------------------------------------------------------------


def test_prompt_normalization_ignores_case_width_and_whitespace() -> None:
    assert normalize_prompt("  Make  it\tSMALL\n") == normalize_prompt("make it small")
    assert normalize_prompt("ＡＢＣ") == normalize_prompt("abc")  # full-width forms fold


def test_plan_key_depends_on_every_part_but_not_input_order() -> None:
    base = plan_key("make a card", ["b" * 64, "a" * 64], "v1", "m")
    assert base == plan_key("Make a  card", ["a" * 64, "b" * 64], "v1", "m")
    assert base != plan_key("make a card!", ["a" * 64, "b" * 64], "v1", "m")
    assert base != plan_key("make a card", ["a" * 64], "v1", "m")
    assert base != plan_key("make a card", ["a" * 64, "b" * 64], "v2", "m")
    assert base != plan_key("make a card", ["a" * 64, "b" * 64], "v1", "other-model")


def test_plan_cache_roundtrip_discard_and_corruption(tmp_path: Path) -> None:
    cache = PlanCache(tmp_path / "plans")
    assert cache.lookup("k") is None
    spec = {"in": "asset:00112233", "ops": [["export", {"preset": "web_jpeg"}]]}
    cache.store("k", spec, "did it")
    hit = cache.lookup("k")
    assert hit is not None and hit.spec == spec and hit.summary == "did it"
    cache.discard("k")
    assert cache.lookup("k") is None
    cache.discard("k")  # discarding twice is fine
    (tmp_path / "plans" / "bad.json").write_text("{not json")
    assert cache.lookup("bad") is None


# -- telemetry ---------------------------------------------------------------------------------


def _req(inp: int, out: int, read: int, write: int, stop: str = "end_turn") -> RequestRecord:
    return RequestRecord(
        turn=1,
        model="claude-opus-5",
        stop_reason=stop,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=read,
        cache_write_tokens=write,
    )


def _job(status: str, requests: list[RequestRecord], **kw: Any) -> JobRecord:
    return JobRecord(
        job_id="j", route="ask", model="claude-opus-5", effort="high", catalogue="c",
        status=status, requests=requests, **kw,
    )  # fmt: skip


def _records() -> list[JobRecord]:
    return [
        _job("ok", [_req(100, 50, 0, 2000), _req(150, 40, 2000, 100)], first_pass=True),
        _job("ok", [_req(100, 60, 2100, 0)], first_pass=False),
        _job("ok", [], plan_cache_hit=True),
        _job("refused", [_req(100, 0, 2100, 0, "refusal")]),
    ]


def test_summarize_headline_metrics() -> None:
    s = summarize(_records())
    assert s["jobs"] == 4 and s["completed"] == 2
    assert s["plan_cache_hit_rate"] == pytest.approx(1 / 4)
    assert s["completed_rate"] == pytest.approx(2 / 3)  # plan-cache hits are not agent runs
    assert s["first_pass_rate"] == pytest.approx(1 / 2)  # the refused job never ran a spec
    assert s["median_turns"] == 1 and s["median_images"] == 0
    # steady state skips the very first request: 6200 read of 6650 prompt tokens
    assert s["cache_read_ratio"] == pytest.approx(6200 / 6650)
    t = s["tokens_per_completed_job"]
    assert t == {"input": 175, "cache_read": 2050, "cache_write": 1050, "output": 75}
    # $/job at 5/25 per M, reads x0.1, writes x1.25: (0.017625 + 0.00305) / 2
    assert s["cost_per_completed_job"] == pytest.approx(0.0103375)


def test_a_silently_uncached_prefix_is_called_out() -> None:
    """Below the model's minimum cacheable length nothing is cached and nothing errors."""
    uncached = [_job("ok", [_req(2500, 50, 0, 0), _req(2600, 40, 0, 0)], first_pass=True)]
    s = summarize(uncached)
    assert s["caching_active"] is False
    assert "no prompt caching is happening" in format_summary(s)
    warm = summarize(_records())
    assert warm["caching_active"] is True
    assert "no prompt caching" not in format_summary(warm)
    only_hits = summarize([_job("ok", [], plan_cache_hit=True)])
    assert only_hits["caching_active"] is None  # no requests were made, so nothing to say


def test_summarize_and_format_handle_no_data() -> None:
    s = summarize([])
    assert s["jobs"] == 0 and s["completed_rate"] is None and s["cache_read_ratio"] is None
    assert "n/a" in format_summary(s)


def test_unknown_model_has_no_cost() -> None:
    job = _job("ok", [_req(1, 1, 0, 0)])
    job.model = "some-future-model"
    assert job.cost() is None
    assert summarize([job])["cost_per_completed_job"] is None


def test_telemetry_file_roundtrip(tmp_path: Path) -> None:
    t = Telemetry(tmp_path / "telemetry")
    assert t.load() == []
    records = _records()
    for r in records:
        t.record(r)
    assert t.load() == records


# -- client ------------------------------------------------------------------------------------


class _FakeSDK:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create("beta")))
        self.messages = SimpleNamespace(create=self._create("plain"))

    def _create(self, kind: str) -> Any:
        def create(**kw: Any) -> str:
            self.calls.append((kind, kw))
            return kind

        return create


def test_client_uses_beta_endpoint_with_default_fallbacks() -> None:
    sdk = _FakeSDK()
    assert AnthropicMessages(sdk, fallbacks=True).create({"model": "m", "max_tokens": 1}) == "beta"
    kind, kw = sdk.calls[0]
    assert kind == "beta"
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["model"] == "m"


def test_client_without_fallbacks_uses_plain_endpoint() -> None:
    sdk = _FakeSDK()
    assert AnthropicMessages(sdk, fallbacks=False).create({"model": "m"}) == "plain"
    kind, kw = sdk.calls[0]
    assert kind == "plain" and "fallbacks" not in kw and "betas" not in kw


def test_client_turns_the_sdks_missing_credentials_typeerror_into_a_clear_error() -> None:
    sdk = _FakeSDK()

    def no_auth(**_: Any) -> None:
        raise TypeError('"Could not resolve authentication method. Expected one of api_key…"')

    sdk.beta.messages.create = no_auth
    with pytest.raises(MissingCredentials, match="ANTHROPIC_API_KEY"):
        AnthropicMessages(sdk, fallbacks=True).create({"model": "m"})

    def other(**_: Any) -> None:
        raise TypeError("something unrelated")

    sdk.messages.create = other
    with pytest.raises(TypeError, match="unrelated"):  # only the credentials case is translated
        AnthropicMessages(sdk, fallbacks=False).create({"model": "m"})
