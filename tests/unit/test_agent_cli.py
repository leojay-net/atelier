"""`atelier ask` and `atelier stats`, end to end through `main()` with a scripted model."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from atelier.agent import client as agent_client
from atelier.agent import commands
from atelier.agent.client import MissingCredentials
from atelier.cli import main
from atelier.engine.engine import Engine
from conftest import FIXTURES
from harness.fake_model import ScriptedModel, final, message, refusal, run_pipeline


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def image(tmp_path: Path) -> Path:
    path = tmp_path / "photo.png"
    path.write_bytes((FIXTURES / "photo.png").read_bytes())
    return path


def handle_of(home: Path, path: Path) -> str:
    return Engine.open(home).store.put_file(path).handle


def good(handle: str) -> dict[str, Any]:
    return {
        "in": handle,
        "ops": [["image.resize", {"width": 320}], ["export", {"preset": "web_jpeg"}]],
    }


def failing(handle: str) -> dict[str, Any]:
    top = ["image.text", {"text": "Top headline", "valign": "top", "box": [0.05, 0.0, 0.9, 0.12]}]
    return {"in": handle, "ops": [top, ["export", {"preset": "thumb_youtube"}]]}


def use(monkeypatch: pytest.MonkeyPatch, model: Any) -> None:
    # `build_adapter` (`agent/providers/`) imports `AnthropicMessages` from `client` fresh on
    # every call, so the patch target is the source module, not `commands`' old direct import.
    monkeypatch.setattr(agent_client, "AnthropicMessages", lambda **_: model)


def ask(home: Path, out: Path, *argv: str) -> int:
    return main(["--home", str(home), "ask", *argv, "--out", str(out)])


def test_ask_runs_the_agent_writes_the_output_and_reports_usage(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    image: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    h = handle_of(home, image)
    use(monkeypatch, ScriptedModel(run_pipeline(good(h)), final("Resized to 320 wide.")))
    assert ask(home, tmp_path / "out", "make it 320 wide", str(image)) == 0
    out = capsys.readouterr().out
    assert out.startswith("ok · 2 turns · in 200 · cache read/write 0/0 · out 100")
    assert "Resized to 320 wide." in out and "checks 3/3 pass" in out
    written = list((tmp_path / "out").iterdir())
    assert [p.suffix for p in written] == [".jpg"]


def test_second_identical_ask_is_a_free_plan_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    image: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    h = handle_of(home, image)
    use(monkeypatch, ScriptedModel(run_pipeline(good(h)), final("Done.")))
    assert ask(home, tmp_path / "out", "make it 320 wide", str(image)) == 0
    capsys.readouterr()
    silent = ScriptedModel()  # would raise if called
    use(monkeypatch, silent)
    assert ask(home, tmp_path / "out", "make it 320 wide", str(image)) == 0
    assert "ok · plan cache hit (0 tokens)" in capsys.readouterr().out and silent.calls == 0
    again = ScriptedModel(final(question="Really?"))
    use(monkeypatch, again)
    code = ask(home, tmp_path / "out", "make it 320 wide", str(image), "--no-plan-cache")
    assert code == commands.EXIT_NEEDS_INPUT and again.calls == 1


@pytest.mark.parametrize(
    ("script", "code", "needle"),
    [
        (lambda h: [final(question="Which size?")], 4, "question: Which size?"),
        (lambda h: [run_pipeline(failing(h)), final("Margin still off.")], 3, "checks_failed"),
        (lambda h: [refusal("cyber", "declined")], 1, "refused · refusal (cyber): declined"),
        (lambda h: [message([], "max_tokens"), message([], "max_tokens")], 1, "error"),
    ],
    ids=["question", "checks_failed", "refused", "error"],
)
def test_exit_codes_follow_the_outcome(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    image: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    script: Any,
    code: int,
    needle: str,
) -> None:
    use(monkeypatch, ScriptedModel(*script(handle_of(home, image))))
    assert ask(home, tmp_path / "out", "do something", str(image)) == code
    assert needle in capsys.readouterr().out


def test_each_runs_one_agent_pass_then_the_engine_over_every_file(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shots = tmp_path / "shots"
    shots.mkdir()
    for i in range(3):
        buf = io.BytesIO()
        Image.new("RGB", (200 + 20 * i, 150), (40 * i, 90, 160)).save(buf, "PNG")
        (shots / f"shot{i}.png").write_bytes(buf.getvalue())
    monkeypatch.chdir(tmp_path)
    first = handle_of(home, shots / "shot0.png")
    model = ScriptedModel(run_pipeline(good(first)), final("Resized."))
    use(monkeypatch, model)
    assert ask(home, tmp_path / "out", "resize them", "--each", "shots/*.png") == 0
    out = capsys.readouterr().out
    assert model.calls == 2 and "3/3 ran · 0 failed checks · 0 errors" in out
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "shot0.jpg",
        "shot1.jpg",
        "shot2.jpg",
    ]


def test_input_errors_are_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    image: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = ScriptedModel()
    use(monkeypatch, model)
    assert ask(home, tmp_path / "out", "x", "no-such-file.png") == 2
    assert "no such file or asset handle" in capsys.readouterr().err
    monkeypatch.chdir(tmp_path)
    assert ask(home, tmp_path / "out", "x", "--each", "zzz*.png") == 2
    assert "matched no files" in capsys.readouterr().err
    assert model.calls == 0


def test_missing_credentials_is_a_clean_failure_that_is_not_counted(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    image: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class NoKey:
        def create(self, request: dict[str, Any]) -> Any:
            raise MissingCredentials("no Anthropic credentials: set ANTHROPIC_API_KEY")

    use(monkeypatch, NoKey())
    assert ask(home, tmp_path / "out", "x", str(image)) == 1
    assert "no Anthropic credentials" in capsys.readouterr().err
    assert not (home / "telemetry" / "jobs.jsonl").exists()


def test_stats_reports_recorded_jobs(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    image: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--home", str(home), "stats"]) == 0
    assert "jobs 0" in capsys.readouterr().out
    h = handle_of(home, image)
    use(monkeypatch, ScriptedModel(run_pipeline(good(h)), final("Done.")))
    ask(home, tmp_path / "out", "make it 320 wide", str(image))
    capsys.readouterr()
    assert main(["--home", str(home), "stats", "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["jobs"] == 1 and stats["completed"] == 1 and stats["first_pass_rate"] == 1.0
    assert stats["tokens_per_completed_job"]["output"] == 100
    assert main(["--home", str(home), "stats", "--last", "1"]) == 0
    assert "jobs 1 · completed 1" in capsys.readouterr().out


def test_ask_rules_flag_appends_rules_to_prompt(
    monkeypatch: pytest.MonkeyPatch, home: Path, image: Path, tmp_path: Path
) -> None:
    h = handle_of(home, image)
    model = ScriptedModel(run_pipeline(good(h)), final("Done."))
    use(monkeypatch, model)
    out = tmp_path / "out"
    rules_file = tmp_path / "rules.txt"
    rules_file.write_text("No emojis\nCap runtime at 15s\n")
    code = ask(
        home,
        out,
        "make banner",
        str(image),
        "--rules",
        "Max width 1080px",
        "--rules-file",
        str(rules_file),
    )
    assert code == 0
    prompt_sent = model.requests[0]["messages"][0]["content"]
    assert "Rules & Restrictions (strictly adhere to these):" in prompt_sent
    assert "- Max width 1080px" in prompt_sent
    assert "- No emojis" in prompt_sent
    assert "- Cap runtime at 15s" in prompt_sent
