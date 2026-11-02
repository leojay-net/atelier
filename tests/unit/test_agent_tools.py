"""The four tools, dispatched against a real engine."""

from __future__ import annotations

from typing import Any

import pytest

from atelier.agent.budget import Budget, BudgetLimits
from atelier.agent.tools import Toolbox, ToolOutcome
from atelier.engine.engine import Engine


@pytest.fixture
def box(engine: Engine) -> Toolbox:
    return Toolbox(engine, Budget(BudgetLimits(max_images=2)))


def linear(photo: str, *ops: Any) -> dict[str, Any]:
    return {"in": photo, "ops": [list(o) for o in ops]}


GOOD = ("export", {"preset": "web_jpeg"})


def failing_spec(photo: str) -> dict[str, Any]:
    """Headline in the top safe margin of a YouTube thumbnail: safe_margins fails."""
    top = ("image.text", {"text": "Top headline", "valign": "top", "box": [0.05, 0.0, 0.9, 0.12]})
    return linear(photo, top, ("export", {"preset": "thumb_youtube"}))


# -- run_pipeline ------------------------------------------------------------------------------


def test_run_pipeline_success(box: Toolbox, photo: str) -> None:
    out = box.dispatch(
        "run_pipeline", {"spec": linear(photo, ("image.resize", {"width": 320}), GOOD)}
    )
    assert not out.is_error
    assert isinstance(out.content, str) and out.content.startswith("ok · ")
    assert "checks" in out.content and "cache:" in out.content
    assert len(box.attempts) == 1 and box.attempts[0].passed
    assert box.first_pass is True and box.best() is box.attempts[0]


def test_run_pipeline_reports_every_validation_error_with_paths(box: Toolbox, photo: str) -> None:
    bad = linear(photo, ("image.crop", {"aspect": "16:9", "focus": "middle"}), ("image.nope", {}))
    out = box.dispatch("run_pipeline", {"spec": bad})
    assert out.is_error and isinstance(out.content, str)
    head, *lines = out.content.splitlines()
    assert head == "invalid spec · 2 errors"
    assert any("focus" in ln and "center" in ln for ln in lines)  # allowed values are listed
    assert any("image.nope" in ln for ln in lines)
    assert box.attempts and box.attempts[0].result is None
    assert box.first_pass is False and box.best() is None


def test_failed_check_is_an_error_with_a_hint_and_keeps_the_earlier_pass(
    box: Toolbox, photo: str
) -> None:
    box.dispatch("run_pipeline", {"spec": linear(photo, GOOD)})
    out = box.dispatch("run_pipeline", {"spec": failing_spec(photo)})
    assert out.is_error and isinstance(out.content, str)
    assert out.content.startswith("fail · ") and "hint:" in out.content
    assert [a.passed for a in box.attempts] == [True, False]
    assert box.best() is box.attempts[0]  # the last run that passed, not the last run
    assert box.first_pass is True


def test_best_falls_back_to_the_last_completed_run(box: Toolbox, photo: str) -> None:
    box.dispatch("run_pipeline", {"spec": failing_spec(photo)})
    assert box.first_pass is False
    assert box.best() is box.attempts[0] and not box.attempts[0].passed


def test_dry_run_plans_without_running_or_recording(box: Toolbox, photo: str) -> None:
    out = box.dispatch("run_pipeline", {"spec": linear(photo, GOOD), "dry_run": True})
    assert not out.is_error and isinstance(out.content, str)
    assert out.content.startswith("valid · 1 step") and "to run" in out.content
    assert box.attempts == []
    invalid = box.dispatch("run_pipeline", {"spec": {"in": photo, "ops": []}, "dry_run": True})
    assert invalid.is_error and box.attempts == []


def test_spec_given_as_a_json_string_is_accepted(box: Toolbox, photo: str) -> None:
    import json

    out = box.dispatch("run_pipeline", {"spec": json.dumps(linear(photo, GOOD))})
    assert not out.is_error and box.attempts[0].passed


@pytest.mark.parametrize("spec", [None, [1, 2], "not json", 7])
def test_run_pipeline_rejects_non_object_specs(box: Toolbox, spec: Any) -> None:
    out = box.dispatch("run_pipeline", {"spec": spec})
    assert out.is_error and "JSON object" in str(out.content)
    assert box.attempts == []


def test_bad_tool_input_and_unknown_tool(box: Toolbox) -> None:
    assert box.dispatch("run_pipeline", ["x"]).is_error  # type: ignore[arg-type]
    unknown = box.dispatch("bash", {"command": "ls"})
    assert unknown.is_error and "describe_op" in str(unknown.content)


# -- describe_op / inspect_media ---------------------------------------------------------------


def test_describe_op_known_and_unknown_names(box: Toolbox) -> None:
    ok = box.dispatch("describe_op", {"names": ["image.crop", "og_image", "batch_thumbnails"]})
    assert not ok.is_error and isinstance(ok.content, str)
    assert "image.crop(" in ok.content and "og_image" in ok.content and "params:" in ok.content
    bad = box.dispatch("describe_op", {"names": ["image.crp", "zzz"]})
    assert bad.is_error and isinstance(bad.content, str)
    assert "did you mean image.crop?" in bad.content and "unknown 'zzz'" in bad.content


def test_describe_op_needs_names_and_caps_the_list(box: Toolbox) -> None:
    assert box.dispatch("describe_op", {"names": []}).is_error
    many = box.dispatch("describe_op", {"names": ["image.crop"] * 15})
    assert "3 more names ignored" in str(many.content)


def test_inspect_media_lines_and_errors(box: Toolbox, photo: str, logo: str) -> None:
    brief = box.dispatch("inspect_media", {"assets": [photo, logo]})
    assert not brief.is_error and isinstance(brief.content, str)
    lines = brief.content.splitlines()
    assert len(lines) == 2 and all(ln.startswith("image · ") for ln in lines)
    full = box.dispatch("inspect_media", {"assets": [photo], "detail": "full"})
    assert "sha256" in str(full.content) and "sha256" not in brief.content
    missing = box.dispatch("inspect_media", {"assets": ["asset:00000000", photo]})
    assert missing.is_error and str(missing.content).count("\n") == 1  # error line + ok line
    assert box.dispatch("inspect_media", {"assets": []}).is_error


# -- preview -----------------------------------------------------------------------------------


def test_preview_returns_a_small_image_and_spends_budget(box: Toolbox, photo: str) -> None:
    out = box.dispatch("preview", {"assets": [photo], "kind": "thumb"})
    assert not out.is_error and isinstance(out.content, list)
    image, caption = out.content
    assert image["type"] == "image" and image["source"]["type"] == "base64"
    assert image["source"]["media_type"] in ("image/png", "image/jpeg", "image/webp")
    assert caption["type"] == "text" and caption["text"]
    assert box.budget.images == 1


def test_preview_kinds(box: Toolbox, photo: str, logo: str) -> None:
    crop = box.dispatch("preview", {"assets": [photo], "kind": "crop", "region": [0, 0, 0.5, 0.5]})
    assert not crop.is_error
    both = box.dispatch("preview", {"assets": [photo, logo], "kind": "compare"})
    assert not both.is_error and isinstance(both.content, list)
    assert box.budget.images == 2


def test_preview_budget_is_enforced_and_errors_cost_nothing(box: Toolbox, photo: str) -> None:
    assert box.dispatch("preview", {"assets": [photo], "kind": "crop"}).is_error  # no region
    assert box.dispatch("preview", {"assets": ["asset:00000000"], "kind": "thumb"}).is_error
    assert box.dispatch("preview", {"assets": [photo], "kind": "poster"}).is_error
    assert box.budget.images == 0
    for _ in range(2):
        assert not box.dispatch("preview", {"assets": [photo], "kind": "thumb"}).is_error
    over = box.dispatch("preview", {"assets": [photo], "kind": "thumb"})
    assert over.is_error and "image budget used (2/2)" in str(over.content)
    assert box.budget.images == 2


# -- bookkeeping -------------------------------------------------------------------------------


def test_calls_are_recorded_and_results_become_tool_result_blocks(box: Toolbox) -> None:
    box.dispatch("describe_op", {"names": ["export"]})
    box.dispatch("bash", {})
    assert box.calls == ["describe_op", "bash"]
    ok = ToolOutcome("fine").block("toolu_1")
    assert ok == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "fine"}
    err = ToolOutcome("bad", is_error=True).block("toolu_2")
    assert err["is_error"] is True
