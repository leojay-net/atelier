"""The baseline harness: a general coding agent doing the eval tasks in a sandbox.

Logic is tested against a fake sandbox and a scripted model. The real container is covered in
test_evals_sandbox.py, plus one end-to-end test here, when Docker and the image are available.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from atelier.agent.budget import BudgetLimits
from atelier.agent.client import MissingCredentials
from atelier.agent.config import AgentConfig
from atelier.engine.engine import Engine
from atelier.evals import baseline as bl
from atelier.evals.baseline import BASELINE_SYSTEM, BASH_TOOL, converse, run_baseline
from atelier.evals.graders import PORTABLE
from atelier.evals.sandbox import CommandResult, clip, image_available
from atelier.evals.task import TaskError, load_tasks
from harness.fake_model import ScriptedModel, message, refusal, text, tool_use

EVALS = Path(__file__).resolve().parents[1] / "evals"
INPUTS = EVALS / "inputs"
TASKS = {t.id: t for t in load_tasks(EVALS / "tasks" / "image.yaml")}

Handler = Callable[["FakeSandbox", str], CommandResult]


class FakeSandbox:
    """Keeps its 'container' filesystem in a temp dir: in/ for inputs, out/ for deliverables."""

    def __init__(self, root: Path, handler: Handler) -> None:
        self.root, self.handler = root, handler
        (root / "in").mkdir(parents=True)
        (root / "out").mkdir()
        self.commands: list[str] = []
        self.uploaded: list[str] = []
        self.downloaded_to: Path | None = None
        self.closed = False

    def run(self, command: str) -> CommandResult:
        self.commands.append(command)
        return self.handler(self, command)

    def upload(self, files: Mapping[str, Path]) -> None:
        for name, src in files.items():
            shutil.copyfile(src, self.root / "in" / name)
        self.uploaded = sorted(files)

    def download(self, dest: Path) -> list[Path]:
        self.downloaded_to = dest
        copies = []
        for src in sorted((self.root / "out").iterdir()):
            copies.append(Path(shutil.copyfile(src, dest / src.name)))
        return copies

    def close(self) -> None:
        self.closed = True


def say(reply: str = "Done.", **kw: Any) -> Any:
    """A plain-prose reply: the baseline has no structured final answer."""
    return message([text(reply)], "end_turn", **kw)


def bash(command: str, id: str | None = None) -> Any:
    return message([tool_use("bash", {"command": command}, id=id)], "tool_use")


def ok(output: str = "") -> Handler:
    return lambda sb, cmd: CommandResult(output, 0)


def write_jpeg(
    sb: FakeSandbox, size: tuple[int, int] = (300, 200), name: str = "result.jpg"
) -> None:
    Image.new("RGB", size, (40, 90, 160)).save(sb.root / "out" / name, "JPEG")


def harness(handler: Handler, base: Path) -> tuple[list[FakeSandbox], Callable[[], FakeSandbox]]:
    made: list[FakeSandbox] = []

    def factory() -> FakeSandbox:
        made.append(FakeSandbox(base / f"sandbox{len(made)}", handler))
        return made[-1]

    return made, factory


def run_tasks(
    engine: Engine, tmp_path: Path, model: Any, handler: Handler, *ids: str, **kw: Any
) -> tuple[Any, list[FakeSandbox]]:
    made, factory = harness(handler, tmp_path)
    report = run_baseline(
        [TASKS[i] for i in ids],
        client=model,
        config=AgentConfig(),
        inputs_dir=INPUTS,
        engine=engine,
        sandbox_factory=factory,
        **kw,
    )
    return report, made


# -- the conversation --------------------------------------------------------------------------


def test_request_shape_is_a_plain_bash_agent_under_the_same_conditions(tmp_path: Path) -> None:
    model = ScriptedModel(bash("ls in"), say("Done."))
    sandbox = FakeSandbox(tmp_path, ok("landscape.jpg"))
    convo = converse("Do it.", model, AgentConfig(), sandbox, "j")
    first = model.requests[0]
    assert first["tools"] == [BASH_TOOL] and "strict" not in BASH_TOOL
    assert first["system"] == BASELINE_SYSTEM
    assert first["model"] == "claude-opus-5" and first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"}  # same effort; no structured format
    assert first["cache_control"] == {"type": "ephemeral"}  # same automatic caching
    assert convo.status == "ok" and convo.final_text == "Done." and convo.record.turns == 2
    (result,) = model.requests[1]["messages"][-1]["content"]
    assert result["type"] == "tool_result" and result["content"] == "landscape.jpg"
    assert sandbox.commands == ["ls in"]


def test_baseline_gets_the_same_ask_dont_guess_instruction() -> None:
    assert "ask one short question" in BASELINE_SYSTEM and "fresh shell" in BASELINE_SYSTEM


@pytest.mark.parametrize(
    ("result", "needle", "is_error"),
    [
        (CommandResult("boom", 2), "[exit code 2]", False),
        (CommandResult("", 124, timed_out=True), "[timed out]", True),
        (CommandResult("fine", 0), "fine", False),
    ],
    ids=["failure", "timeout", "success"],
)
def test_command_results_are_reported_like_a_terminal_would(
    tmp_path: Path, result: CommandResult, needle: str, is_error: bool
) -> None:
    model = ScriptedModel(bash("x"), say())
    converse("t", model, AgentConfig(), FakeSandbox(tmp_path, lambda s, c: result), "j")
    (block,) = model.requests[1]["messages"][-1]["content"]
    assert needle in block["content"] and block.get("is_error", False) is is_error


def test_restart_bad_input_and_unknown_tools_are_answered_not_crashed_on(tmp_path: Path) -> None:
    sandbox = FakeSandbox(tmp_path, ok())
    model = ScriptedModel(
        message(
            [
                tool_use("bash", {"restart": True}, id="a"),
                tool_use("bash", {}, id="b"),
                tool_use("bash", {"command": "  "}, id="c"),
                tool_use("python", {"code": "1"}, id="d"),
            ],
            "tool_use",
        ),
        say(),
    )
    converse("t", model, AgentConfig(), sandbox, "j")
    blocks = model.requests[1]["messages"][-1]["content"]
    assert [b["tool_use_id"] for b in blocks] == ["a", "b", "c", "d"]
    assert "nothing to restart" in blocks[0]["content"] and "is_error" not in blocks[0]
    assert all(b.get("is_error") for b in blocks[1:])
    assert sandbox.commands == []  # none of that reached the sandbox


def test_the_turn_budget_stops_a_runaway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bl, "BASELINE_LIMITS", BudgetLimits(max_turns=3, max_output_tokens=10**6))
    model = ScriptedModel(bash("a"), bash("b"), bash("c"))
    sandbox = FakeSandbox(tmp_path, ok())
    convo = converse("t", model, AgentConfig(), sandbox, "j")
    assert convo.status == "budget" and model.calls == 3
    assert sandbox.commands == ["a", "b", "c"]  # the last requested command still ran


@pytest.mark.parametrize(
    ("script", "status"),
    [
        ([refusal()], "refused"),
        ([message([], "max_tokens"), message([], "max_tokens")], "error"),
        ([message([], "stop_sequence")], "error"),
        ([message([text("hm")], "tool_use")], "error"),
    ],
    ids=["refusal", "max_tokens-twice", "unexpected-stop", "tool_use-without-block"],
)
def test_every_stop_reason_is_handled(tmp_path: Path, script: list[Any], status: str) -> None:
    convo = converse("t", ScriptedModel(*script), AgentConfig(), FakeSandbox(tmp_path, ok()), "j")
    assert convo.status == status


def test_max_tokens_is_retried_once_and_pause_turn_resumes(tmp_path: Path) -> None:
    model = ScriptedModel(
        message([text("cut off")], "max_tokens"),
        message([text("working")], "pause_turn"),
        say("Done."),
    )
    convo = converse("t", model, AgentConfig(), FakeSandbox(tmp_path, ok()), "j")
    assert convo.status == "ok" and model.requests[1]["max_tokens"] == 20_000
    assert model.requests[2]["messages"][-1]["role"] == "assistant"


def test_clip_keeps_both_ends() -> None:
    assert clip("short") == "short"
    long = "A" * 30_000 + "Z" * 30_000
    clipped = clip(long)
    assert len(clipped) < 10_100 and clipped.startswith("AAAA") and clipped.endswith("ZZZZ")
    assert "characters omitted" in clipped


# -- running tasks -----------------------------------------------------------------------------


def make_jpeg_handler(sb: FakeSandbox, cmd: str) -> CommandResult:
    write_jpeg(sb)
    return CommandResult("", 0)


def test_a_solved_task_is_graded_on_the_delivered_file_only(engine: Engine, tmp_path: Path) -> None:
    model = ScriptedModel(bash("python make.py"), say("Made a JPEG.", usage=(500, 200, 1000, 0)))
    report, made = run_tasks(engine, tmp_path, model, make_jpeg_handler, "web-jpeg-landscape")
    (result,) = report.results
    assert result.passed and result.status == "ok" and result.units and result.units > 0
    assert {g.name for g in result.graders} == {"format", "max_bytes"}  # only portable graders
    assert {g.name for g in result.graders} <= PORTABLE
    sandbox = made[0]
    assert sandbox.closed and sandbox.downloaded_to is not None
    assert not sandbox.downloaded_to.exists()  # the host scratch copy is removed afterwards
    assert report.catalogue == "baseline" and result.record is not None
    assert result.record.route == "baseline" and result.record.images == 0


def test_the_task_and_input_files_reach_the_agent(engine: Engine, tmp_path: Path) -> None:
    model = ScriptedModel(bash("ls"), say("Nothing."))
    report, made = run_tasks(engine, tmp_path, model, ok(), "wm-corner-subtle-jpeg")
    assert made[0].uploaded == ["landscape.jpg", "logo.png"]  # by file name, into in/
    assert sorted(p.name for p in (made[0].root / "in").iterdir()) == ["landscape.jpg", "logo.png"]
    opening = model.requests[0]["messages"][0]["content"]
    assert opening.startswith("Watermark this photo") and "- in/landscape.jpg" in opening
    assert report.results[0].status == "no_output"


@pytest.mark.parametrize(
    ("make", "expect_pass", "why"),
    [
        (lambda sb: write_jpeg(sb, name="a.jpg"), True, "a JPEG"),
        (
            lambda sb: Image.new("RGB", (9, 9)).save(sb.root / "out" / "a.png"),
            False,
            "wrong format",
        ),
        (lambda sb: None, False, "nothing delivered"),
    ],
    ids=["jpeg", "png", "nothing"],
)
def test_a_wrong_or_missing_deliverable_fails(
    engine: Engine,
    tmp_path: Path,
    make: Callable[[FakeSandbox], None],
    expect_pass: bool,
    why: str,
) -> None:
    def handler(sb: FakeSandbox, cmd: str) -> CommandResult:
        make(sb)
        return CommandResult("", 0)

    model = ScriptedModel(bash("go"), say("Finished."))
    report, _ = run_tasks(engine, tmp_path, model, handler, "web-jpeg-landscape")
    assert report.results[0].passed is expect_pass, why


def test_asking_instead_of_guessing_passes_an_ambiguous_task(
    engine: Engine, tmp_path: Path
) -> None:
    model = ScriptedModel(say("What size should it be?"))
    report, _ = run_tasks(engine, tmp_path, model, ok(), "ask-resize")
    (result,) = report.results
    assert result.passed and result.status == "needs_input"
    assert [g.name for g in result.graders] == ["asks_question"]


def test_guessing_on_an_ambiguous_task_fails(engine: Engine, tmp_path: Path) -> None:
    model = ScriptedModel(bash("resize to 800"), say("Resized to 800px."))
    report, _ = run_tasks(engine, tmp_path, model, make_jpeg_handler, "ask-resize")
    (result,) = report.results
    assert not result.passed and result.status == "ok"


def test_finishing_with_nothing_and_no_question_is_no_output(
    engine: Engine, tmp_path: Path
) -> None:
    model = ScriptedModel(say("I could not."))
    report, _ = run_tasks(engine, tmp_path, model, ok(), "web-jpeg-landscape")
    assert report.results[0].status == "no_output" and not report.results[0].passed


def test_a_refusal_fails_the_task_but_keeps_the_run(engine: Engine, tmp_path: Path) -> None:
    report, _ = run_tasks(engine, tmp_path, ScriptedModel(refusal()), ok(), "web-jpeg-landscape")
    assert report.results[0].status == "refused" and not report.results[0].passed


def test_a_crash_costs_one_task_and_still_stops_the_sandbox(engine: Engine, tmp_path: Path) -> None:
    class Flaky:
        def create(self, request: dict[str, Any]) -> Any:
            raise RuntimeError("connection reset")

    report, made = run_tasks(engine, tmp_path, Flaky(), ok(), "web-jpeg-landscape")
    assert report.results[0].status == "error" and "connection reset" in report.results[0].detail
    assert made[0].closed


def test_a_broken_run_is_not_swallowed(engine: Engine, tmp_path: Path) -> None:
    class NoKey:
        def create(self, request: dict[str, Any]) -> Any:
            raise MissingCredentials("no credentials")

    with pytest.raises(MissingCredentials):
        run_tasks(engine, tmp_path, NoKey(), ok(), "web-jpeg-landscape")
    missing = replace(TASKS["web-jpeg-landscape"], inputs={"photo": "nope.jpg"})
    made, factory = harness(ok(), tmp_path / "again")
    with pytest.raises(TaskError, match=r"nope\.jpg"):
        run_baseline(
            [missing],
            client=ScriptedModel(),
            config=AgentConfig(),
            inputs_dir=INPUTS,
            engine=engine,
            sandbox_factory=factory,
        )
    assert made == []  # checked before any container was started


def test_a_sandbox_that_fails_to_start_is_not_a_bad_answer(engine: Engine, tmp_path: Path) -> None:
    from atelier.evals.sandbox import SandboxError

    def broken() -> Any:
        raise SandboxError("docker is unavailable")

    with pytest.raises(SandboxError, match="docker is unavailable"):
        run_baseline(
            [TASKS["web-jpeg-landscape"]],
            client=ScriptedModel(),
            config=AgentConfig(),
            inputs_dir=INPUTS,
            engine=engine,
            sandbox_factory=broken,
        )


def test_the_rubric_judge_sees_the_delivered_file(engine: Engine, tmp_path: Path) -> None:
    judge = ScriptedModel(message([text(json.dumps({"passed": True, "failed_criteria": []}))]))
    model = ScriptedModel(bash("go"), say("Punchier."))
    report, _ = run_tasks(
        engine, tmp_path, model, make_jpeg_handler, "instagram-pop", judge_client=judge
    )
    assert {g.name for g in report.results[0].graders} >= {"format", "rubric"}
    blocks = judge.requests[0]["messages"][0]["content"]
    assert [b["type"] for b in blocks].count("image") == 2  # the original and the result


def test_the_baseline_report_feeds_the_gate(engine: Engine, tmp_path: Path) -> None:
    from atelier.evals.runner import EvalReport

    model = ScriptedModel(bash("go"), say("Done."))
    report, _ = run_tasks(engine, tmp_path, model, make_jpeg_handler, "web-jpeg-landscape")
    path = tmp_path / "baseline.json"
    report.save(path)
    assert EvalReport.load(path) == report


# -- one end-to-end run through a real container -------------------------------------------------


@pytest.mark.skipif(
    not image_available(), reason="Docker or the atelier-baseline image is not available"
)
def test_end_to_end_a_scripted_model_drives_a_real_container(engine: Engine) -> None:
    """Everything but the live model: real container, real Pillow, real files, real grading."""
    command = (
        "python -c \"from PIL import Image; im = Image.open('in/landscape.jpg'); "
        "im.convert('RGB').save('out/result.jpg', quality=80)\" && ls out"
    )
    model = ScriptedModel(bash(command), say("Saved an optimized JPEG."))
    report = run_baseline(
        [TASKS["web-jpeg-landscape"]],
        client=model,
        config=AgentConfig(),
        inputs_dir=INPUTS,
        engine=engine,  # default sandbox factory: the real Docker sandbox
    )
    (result,) = report.results
    assert result.passed, result.failure()
    assert model.requests[1]["messages"][-1]["content"][0]["content"] == "result.jpg\n"
