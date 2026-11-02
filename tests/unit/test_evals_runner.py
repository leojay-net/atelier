"""The eval runner, report, exit gate, acceptance rule and rubric judge (no network)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from atelier.agent.client import MissingCredentials
from atelier.agent.config import AgentConfig
from atelier.agent.telemetry import JobRecord, RequestRecord
from atelier.canonical import sha256_file
from atelier.cli import main
from atelier.engine.engine import Engine
from atelier.evals import commands as eval_commands
from atelier.evals import rubric
from atelier.evals.graders import GraderResult
from atelier.evals.runner import (
    EvalReport,
    TaskResult,
    acceptance,
    check_gate,
    format_gate,
    format_report,
    portable_passed,
    run_eval,
    summarize_report,
    units,
)
from atelier.evals.task import TaskError, load_tasks
from harness.fake_model import ScriptedModel, final, message, run_pipeline, text, tool_use

EVALS = Path(__file__).resolve().parents[1] / "evals"
INPUTS = EVALS / "inputs"
TASKS = {t.id: t for t in load_tasks(EVALS / "tasks" / "image.yaml")}


def handle(engine: Engine, name: str) -> str:
    return engine.store.put_file(INPUTS / name).handle


def run(engine: Engine, model: Any, *ids: str, **kw: Any) -> EvalReport:
    return run_eval(
        [TASKS[i] for i in ids],
        engine=engine,
        client=model,
        config=AgentConfig(),
        inputs_dir=INPUTS,
        **kw,
    )


# -- running tasks -----------------------------------------------------------------------------


def test_a_solved_a_failed_and_a_question_task(engine: Engine) -> None:
    landscape = handle(engine, "landscape.jpg")
    model = ScriptedModel(
        run_pipeline(
            {"recipe": "batch_thumbnails", "params": {"in": landscape, "size": "og_image"}}
        ),
        final("Card made.", usage=(100, 60, 2000, 0)),
        # wrong format on purpose: the task wants a JPEG
        run_pipeline({"in": landscape, "ops": [["export", {"preset": "web_webp"}]]}),
        final("Done."),
        final(question="What size do you need?"),
    )
    seen: list[str] = []
    report = run(
        engine, model, "og-card-no-title", "web-jpeg-landscape", "ask-resize",
        on_result=lambda r: seen.append(r.task_id),
    )  # fmt: skip
    assert seen == ["og-card-no-title", "web-jpeg-landscape", "ask-resize"]
    solved, wrong, asked = report.results
    assert solved.passed and solved.status == "ok" and solved.units and not solved.over_budget
    assert not wrong.passed and wrong.status == "ok"  # the agent was satisfied; the grader was not
    assert "format" in wrong.failure() and "WEBP" in wrong.failure()
    assert asked.passed and asked.status == "needs_input"
    assert report.model == "claude-opus-5" and len(report.catalogue) == 12


def test_the_suite_measures_the_model_not_the_plan_cache(engine: Engine) -> None:
    landscape = handle(engine, "landscape.jpg")
    spec = {"in": landscape, "ops": [["export", {"preset": "web_jpeg"}]]}
    model = ScriptedModel(run_pipeline(spec), final(), run_pipeline(spec), final())
    report = run(engine, model, "web-jpeg-landscape", "web-jpeg-landscape")
    assert model.calls == 4 and [r.passed for r in report.results] == [True, True]


def test_units_weigh_cache_and_output_like_the_price_list() -> None:
    rec = JobRecord("j", "ask", "m", "high", "c", requests=[
        RequestRecord(1, "m", "end_turn", 100, 10, 1000, 200),
    ])  # fmt: skip
    assert units(rec) == 100 + 1.25 * 200 + 0.1 * 1000 + 5 * 10


def test_over_budget_is_flagged_per_task(engine: Engine) -> None:
    landscape = handle(engine, "landscape.jpg")
    model = ScriptedModel(
        run_pipeline({"in": landscape, "ops": [["export", {"preset": "web_jpeg"}]]}), final()
    )
    tiny = replace(TASKS["web-jpeg-landscape"], budget=1)
    report = run_eval([tiny], engine=engine, client=model, config=AgentConfig(), inputs_dir=INPUTS)
    assert report.results[0].over_budget and report.results[0].passed
    assert summarize_report(report)["over_budget"] == 1


def test_one_flaky_request_costs_one_task_not_the_whole_run(engine: Engine) -> None:
    class Flaky:
        def __init__(self) -> None:
            self.inner = ScriptedModel(final(question="Which size?"))
            self.n = 0

        def create(self, request: dict[str, Any]) -> Any:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("connection reset")
            return self.inner.create(request)

    report = run(engine, Flaky(), "ask-crop", "ask-resize")
    broken, fine = report.results
    assert broken.status == "error" and "RuntimeError: connection reset" in broken.detail
    assert not broken.passed and broken.record is None
    assert fine.passed


def test_a_broken_run_is_not_swallowed(engine: Engine, tmp_path: Path) -> None:
    class NoKey:
        def create(self, request: dict[str, Any]) -> Any:
            raise MissingCredentials("no credentials")

    with pytest.raises(MissingCredentials):
        run(engine, NoKey(), "ask-crop")
    missing = replace(TASKS["ask-crop"], inputs={"photo": "does-not-exist.jpg"})
    with pytest.raises(TaskError, match=r"does-not-exist\.jpg"):
        run_eval(
            [missing],
            engine=engine,
            client=ScriptedModel(),
            config=AgentConfig(),
            inputs_dir=INPUTS,
        )


def test_report_roundtrips_through_json(engine: Engine, tmp_path: Path) -> None:
    landscape = handle(engine, "landscape.jpg")
    model = ScriptedModel(
        run_pipeline({"in": landscape, "ops": [["export", {"preset": "web_jpeg"}]]}), final("Ok.")
    )
    report = run(engine, model, "web-jpeg-landscape")
    path = tmp_path / "report.json"
    report.save(path)
    assert EvalReport.load(path) == report
    assert json.loads(path.read_text())["results"][0]["task_id"] == "web-jpeg-landscape"


# -- the rubric judge --------------------------------------------------------------------------


RUBRIC_TASK = TASKS["instagram-pop"]


def solved_engine_run(engine: Engine) -> ScriptedModel:
    square = handle(engine, "square.png")
    spec = {
        "in": square,
        "ops": [
            ["image.adjust", {"contrast": 1.15, "saturation": 1.3}],
            ["export", {"preset": "web_jpeg"}],
        ],
    }
    return ScriptedModel(run_pipeline(spec), final("Punchier."))


def test_rubric_is_skipped_without_a_judge_and_never_blocks(engine: Engine) -> None:
    report = run(engine, solved_engine_run(engine), "instagram-pop")
    result = report.results[0]
    (skipped,) = [g for g in result.graders if g.name == "rubric"]
    assert skipped.soft and "skipped" in skipped.detail and result.passed


def test_rubric_judge_passes_and_fails_the_task(engine: Engine) -> None:
    verdicts = [
        {"passed": True, "failed_criteria": []},
        {"passed": False, "failed_criteria": ["The result is not more vivid."]},
    ]
    outcomes = []
    for v in verdicts:
        judge = ScriptedModel(message([text(json.dumps(v))]))
        report = run(engine, solved_engine_run(engine), "instagram-pop", judge_client=judge)
        outcomes.append(report.results[0])
        request = judge.requests[0]
        blocks = request["messages"][0]["content"]
        assert [b["type"] for b in blocks].count("image") == 2  # the original and the result
        assert "1. The result has an aspect ratio" in blocks[-1]["text"]
        assert (
            "tools" not in request and request["output_config"]["format"]["type"] == "json_schema"
        )
    assert outcomes[0].passed and not outcomes[1].passed
    assert "not more vivid" in outcomes[1].failure()


@pytest.mark.parametrize(
    "reply",
    [message([text("definitely")]), message([text("{}")]), message([], "max_tokens")],
    ids=["not-json", "missing-keys", "cut-off"],
)
def test_an_unusable_judge_reply_fails_closed(engine: Engine, reply: Any) -> None:
    graded = rubric.judge(
        ScriptedModel(reply),
        model="m",
        engine=engine,
        request="x",
        rubric=["a"],
        originals=[handle(engine, "square.png")],
        outputs=[handle(engine, "square.png")],
    )
    assert isinstance(graded, GraderResult) and not graded.passed


def test_rubric_with_nothing_to_judge_fails(engine: Engine) -> None:
    model = ScriptedModel(final("I gave up.", question=""))
    report = run(engine, model, "instagram-pop", judge_client=ScriptedModel())
    assert not report.results[0].passed
    assert any(
        g.name == "rubric" and g.detail == "nothing to judge" for g in report.results[0].graders
    )


# -- gate and acceptance -----------------------------------------------------------------------


def make_report(
    *,
    n: int = 10,
    turns: int = 2,
    images: int = 0,
    first_pass: bool | list[bool] = True,
    read: int = 2000,
    unit: float = 1000.0,
    passed: bool | list[bool] = True,
    split: str = "test",
) -> EvalReport:
    results = []
    for i in range(n):
        fp = first_pass[i] if isinstance(first_pass, list) else first_pass
        ok = passed[i] if isinstance(passed, list) else passed
        requests = [
            RequestRecord(t, "claude-opus-5", "end_turn", 100, 50, read, 0) for t in range(turns)
        ]
        rec = JobRecord(
            f"j{i}",
            "ask",
            "claude-opus-5",
            "high",
            "c",
            status="ok" if ok else "checks_failed",
            first_pass=fp,
            images=images,
            requests=requests,
        )
        graders = [GraderResult("agent_ok", ok), GraderResult("format", ok)]
        results.append(
            TaskResult(f"t{i}", split, "recipe", rec.status, "", graders, rec, unit, False)
        )
    return EvalReport("claude-opus-5", "high", "c", results)


def by_name(checks: list[Any]) -> dict[str, bool | None]:
    return {c.name: c.passed for c in checks}


def test_gate_passes_when_every_criterion_is_met() -> None:
    checks = by_name(check_gate(make_report(), make_report(unit=6000.0)))
    assert checks == {
        "median turns per job": True,
        "median images per job": True,
        "first-pass check rate": True,
        "prefix cache-read ratio": True,
        "tokens per completed job vs baseline": True,
        "pass rate vs baseline (portable graders)": True,
    }


@pytest.mark.parametrize(
    ("kwargs", "failing"),
    [
        ({"turns": 4}, "median turns per job"),
        ({"images": 1}, "median images per job"),
        ({"first_pass": [True] * 7 + [False] * 3}, "first-pass check rate"),
        ({"read": 0}, "prefix cache-read ratio"),
    ],
    ids=["turns", "images", "first-pass", "cache"],
)
def test_gate_fails_each_criterion_independently(kwargs: dict[str, Any], failing: str) -> None:
    checks = by_name(check_gate(make_report(**kwargs)))
    assert checks[failing] is False
    assert [k for k, v in checks.items() if v is False] == [failing]


def test_gate_token_advantage_and_pass_rate_against_a_baseline() -> None:
    mine = make_report(unit=1000.0)
    assert by_name(check_gate(mine, make_report(unit=5000.0)))[
        "tokens per completed job vs baseline"
    ]
    assert (
        by_name(check_gate(mine, make_report(unit=4900.0)))["tokens per completed job vs baseline"]
        is False
    )
    weaker = make_report(unit=1000.0, passed=[True] * 8 + [False] * 2)
    strong = make_report(unit=9000.0)
    assert by_name(check_gate(weaker, strong))["pass rate vs baseline (portable graders)"] is False
    assert by_name(check_gate(strong, weaker))["pass rate vs baseline (portable graders)"] is True


def graded(name: str, passed: bool, status: str = "ok", *, soft: bool = False) -> TaskResult:
    return TaskResult(
        "t", "test", "custom", status, "", [GraderResult(name, passed, "", soft)], None, 1.0, False
    )


def test_portable_passed_looks_only_at_graders_every_system_can_be_judged_by() -> None:
    assert portable_passed(graded("format", True))
    assert not portable_passed(graded("format", False))
    assert not portable_passed(graded("agent_ok", True))  # engine checks: no baseline equivalent
    assert not portable_passed(graded("ops_include", True))  # spec graders likewise
    assert not portable_passed(graded("format", True, soft=True))  # nothing decisive left
    for status in ("error", "budget", "refused"):
        assert not portable_passed(graded("format", True, status))
    assert portable_passed(graded("asks_question", True, "needs_input"))


def test_the_baseline_comparison_ignores_engine_checks_on_both_sides() -> None:
    """An output the engine's own checks reject can still be a fine deliverable by the same standard
    the baseline is held to; the comparison must not punish the agent for having stricter checks."""
    mine = make_report(unit=1000.0)
    for r in mine.results:  # every agent_ok fails, every portable grader passes
        r.graders[0] = GraderResult("agent_ok", False)
        r.status = "checks_failed"
    baseline = make_report(unit=6000.0)
    checks = by_name(check_gate(mine, baseline))
    assert checks["pass rate vs baseline (portable graders)"] is True
    assert checks["tokens per completed job vs baseline"] is True  # units of portable-passed tasks
    assert summarize_report(mine)["pass_rate"] == 0  # while the strict rate is still reported
    assert summarize_report(mine)["portable_pass_rate"] == 1.0


def test_gate_says_so_when_it_cannot_judge() -> None:
    checks = by_name(check_gate(make_report(split="train")))  # nothing in the test split
    assert set(checks.values()) == {None}
    assert by_name(check_gate(make_report()))["tokens vs baseline"] is None
    assert "no baseline given" in format_gate(check_gate(make_report()))


def test_gate_follows_the_requested_split() -> None:
    assert by_name(check_gate(make_report(split="train"), split="train"))["median turns per job"]


def test_acceptance_rule() -> None:
    old = make_report(unit=1000.0)
    same = by_name(acceptance(make_report(unit=1000.0), old))
    assert same == {"pass rate did not regress": True, "median tokens per job": True}
    cheaper = acceptance(make_report(unit=800.0), old)
    assert all(c.passed for c in cheaper)
    within = acceptance(make_report(unit=1100.0), old)
    assert all(c.passed for c in within)  # exactly +10% is allowed
    assert by_name(acceptance(make_report(unit=1200.0), old))["median tokens per job"] is False
    regress = make_report(unit=800.0, passed=[True] * 9 + [False])
    assert by_name(acceptance(regress, old))["pass rate did not regress"] is False
    better_but_dearer = acceptance(
        make_report(unit=1500.0), make_report(unit=1000.0, passed=[True] * 9 + [False])
    )
    assert all(c.passed for c in better_but_dearer)  # a matching quality gain excuses the cost


def test_format_report_lists_failures_and_aggregates() -> None:
    report = make_report(passed=[True] * 9 + [False], n=10)
    text_out = format_report(report, "test")
    assert "passed 9/10 (90%)" in text_out and "recipe 90%" in text_out
    assert "failed:" in text_out and "t9:" in text_out
    assert "turns median 2" in text_out


# -- the eval command --------------------------------------------------------------------------


def use_model(monkeypatch: pytest.MonkeyPatch, *models: Any) -> None:
    pool = list(models)
    monkeypatch.setattr(
        eval_commands, "AnthropicMessages", lambda **_: pool[0] if len(pool) == 1 else pool.pop(0)
    )


def test_eval_run_prints_results_saves_the_report_and_evaluates_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    use_model(monkeypatch, ScriptedModel(final(question="Which photo?")))
    out = tmp_path / "r.json"
    code = main(["--home", str(tmp_path / "h"), "eval", "run", "--id", "ask-which-photo",
                 "--tasks", str(EVALS / "tasks" / "image.yaml"), "--inputs", str(INPUTS),
                 "--out", str(out), "--gate"])  # fmt: skip
    text_out = capsys.readouterr().out
    assert code == 0
    assert "PASS  ask-which-photo" in text_out and "needs_input" in text_out
    assert "exit gate (test split)" in text_out and out.is_file()
    assert EvalReport.load(out).results[0].passed


def test_eval_run_gate_flag_sets_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A successful preview makes the median images per job 1, which the exit gate forbids.
    photo = f"asset:{sha256_file(INPUTS / 'landscape.jpg')}"  # the eval engine's handle for it
    look = message([tool_use("preview", {"assets": [photo], "kind": "thumb"})], "tool_use")
    argv = ["--home", str(tmp_path / "h"), "eval", "run", "--id", "ask-which-photo",
            "--tasks", str(EVALS / "tasks" / "image.yaml"), "--inputs", str(INPUTS),
            "--out", str(tmp_path / "r.json")]  # fmt: skip
    use_model(monkeypatch, ScriptedModel(look, final(question="Which photo?")))
    assert main(argv) == 0  # reported, not enforced, without --gate
    assert "FAIL  median images per job: 1" in capsys.readouterr().out
    use_model(monkeypatch, ScriptedModel(look, final(question="Which photo?")))
    assert main([*argv, "--gate"]) == 3


def test_eval_report_command_with_baseline_and_previous(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_report(unit=1000.0).save(tmp_path / "new.json")
    make_report(unit=6000.0).save(tmp_path / "base.json")
    make_report(unit=1000.0).save(tmp_path / "old.json")
    argv = ["--home", str(tmp_path / "h"), "eval", "report", str(tmp_path / "new.json"),
            "--baseline", str(tmp_path / "base.json"), "--previous", str(tmp_path / "old.json"),
            "--gate"]  # fmt: skip
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "PASS  tokens per completed job vs baseline: 6.0x lower" in out
    assert "acceptance vs previous run" in out
    make_report(turns=5).save(tmp_path / "slow.json")
    assert (
        main(
            ["--home", str(tmp_path / "h"), "eval", "report", str(tmp_path / "slow.json"), "--gate"]
        )
        == 3
    )


def test_eval_run_reports_bad_filters_and_missing_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks_file, out = str(EVALS / "tasks" / "image.yaml"), str(tmp_path / "r.json")
    base = ["--home", str(tmp_path / "h"), "eval", "run", "--tasks", tasks_file]
    base += ["--inputs", str(INPUTS), "--out", out]
    assert main([*base, "--id", "no-such-task"]) == 2
    assert "no tasks match" in capsys.readouterr().err

    class NoKey:
        def create(self, request: dict[str, Any]) -> Any:
            raise MissingCredentials("no Anthropic credentials")

    use_model(monkeypatch, NoKey())
    assert main([*base, "--id", "ask-crop"]) == 1
    assert "no Anthropic credentials" in capsys.readouterr().err


def test_suite_picks_the_task_file_and_a_video_question_task_runs_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--suite video` reads tasks/video.yaml; an ambiguous task needs no ffmpeg and no tokens."""
    monkeypatch.chdir(EVALS.parent.parent)  # the default task directory is relative to the repo
    use_model(monkeypatch, ScriptedModel(final(question="How long should it be?")))
    code = main(["--home", str(tmp_path / "h"), "eval", "run", "--suite", "video",
                 "--id", "ask-shorter", "--inputs", str(INPUTS),
                 "--out", str(tmp_path / "r.json")])  # fmt: skip
    assert code == 0
    assert "PASS  ask-shorter" in capsys.readouterr().out


def test_an_unknown_suite_is_refused_by_the_parser(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--home", str(tmp_path / "h"), "eval", "run", "--suite", "documents"])


def test_the_baseline_refuses_video_tasks_when_its_image_has_no_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An old baseline image (images only) would fail every video task for the wrong reason."""
    from atelier.evals.sandbox import CommandResult

    class Bare:
        def run(self, command: str) -> CommandResult:
            return CommandResult("", 1, False)

        def close(self) -> None:
            pass

    monkeypatch.setattr(eval_commands, "image_available", lambda _image: True)
    monkeypatch.setattr(eval_commands, "docker_factory", lambda _image: Bare)
    monkeypatch.chdir(EVALS.parent.parent)
    code = main(["--home", str(tmp_path / "h"), "eval", "baseline", "--suite", "video",
                 "--id", "reel-talk-middle", "--inputs", str(INPUTS)])  # fmt: skip
    err = capsys.readouterr().err
    assert code == 1 and "lacks ffmpeg and ffprobe" in err and "docker build" in err


def test_the_baseline_does_not_check_for_ffmpeg_on_image_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from atelier.evals.commands import _uses_av
    from atelier.evals.task import load_tasks

    assert not _uses_av(load_tasks(EVALS / "tasks" / "image.yaml"))
    assert _uses_av(load_tasks(EVALS / "tasks" / "video.yaml"))
