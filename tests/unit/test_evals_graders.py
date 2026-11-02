"""Graders must be able to fail: every grader is exercised on outputs it should accept and on
outputs it should reject. A grader that cannot fail would make the eval meaningless."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from atelier.agent.loop import AgentResult
from atelier.engine.engine import Engine
from atelier.evals.graders import GRADERS, Context, GraderResult, grade
from atelier.evals.task import TaskError, load_tasks

EVALS = Path(__file__).resolve().parents[1] / "evals"


def outcome(engine: Engine, spec: Any, status: str | None = None) -> Context:
    result = engine.run(spec)
    ar = AgentResult(
        status=status or ("ok" if result.passed else "checks_failed"),
        outputs={i: r.handle for i, r in result.outputs.items()},
        spec=spec,
        engine_result=result,
    )
    return Context(ar, engine)


@pytest.fixture
def jpeg(engine: Engine, photo: str) -> Context:
    """A 640x427 JPEG from a two-op pipeline (the photo fixture is 640x427)."""
    return outcome(
        engine, {"in": photo, "ops": [["image.flip", {}], ["export", {"preset": "web_jpeg"}]]}
    )


@pytest.fixture
def logo_png(engine: Engine, logo: str) -> Context:
    return outcome(engine, {"in": logo, "ops": [["export", {"preset": "transparent_png"}]]})


def verdict(ctx: Context, **cfg: Any) -> tuple[bool, str]:
    return GRADERS[cfg["type"]](cfg, ctx)


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"width": 640}, True),
        ({"width": 641}, False),
        ({"height": 427}, True),
        ({"height": 428}, False),
        ({"width": 640, "height": 427}, True),
        ({"max_edge": 640}, True),
        ({"max_edge": 639}, False),
        ({"aspect": [3, 2]}, True),
        ({"aspect": [1, 1]}, False),
        ({"aspect": [16, 9], "tol": 0.2}, True),  # 1.5 vs 1.78 is within 20%
        ({"orientation": "landscape"}, True),
        ({"orientation": "portrait"}, False),
    ],
)
def test_dims(jpeg: Context, cfg: dict[str, Any], expected: bool) -> None:
    assert verdict(jpeg, type="dims", **cfg)[0] is expected


def test_format_and_size(jpeg: Context) -> None:
    assert verdict(jpeg, type="format", formats=["jpeg"])[0]  # case-insensitive
    assert verdict(jpeg, type="format", formats=["PNG", "JPEG"])[0]
    ok, detail = verdict(jpeg, type="format", formats=["PNG", "WEBP"])
    assert not ok and "JPEG" in detail and "PNG|WEBP" in detail
    assert verdict(jpeg, type="max_bytes", bytes=10_000_000)[0]
    assert not verdict(jpeg, type="max_bytes", bytes=10)[0]


def test_alpha_needs_real_transparency(jpeg: Context, logo_png: Context) -> None:
    assert verdict(logo_png, type="alpha", present=True)[0]
    assert not verdict(logo_png, type="alpha", present=False)[0]
    assert not verdict(jpeg, type="alpha", present=True)[0]
    assert verdict(jpeg, type="alpha", present=False)[0]


def test_output_count(jpeg: Context) -> None:
    assert verdict(jpeg, type="output_count", n=1)[0]
    assert not verdict(jpeg, type="output_count", n=2)[0]


def test_spec_graders_read_the_canonical_spec(jpeg: Context) -> None:
    assert verdict(jpeg, type="ops_include", ops=["image.flip", "export"])[0]
    ok, detail = verdict(jpeg, type="ops_include", ops=["image.flip", "image.text"])
    assert not ok and "image.text" in detail
    assert verdict(jpeg, type="ops_any", ops=["image.text", "image.flip"])[0]
    assert not verdict(jpeg, type="ops_any", ops=["image.text", "image.crop"])[0]
    assert verdict(jpeg, type="ops_exclude", ops=["image.text"])[0]
    assert not verdict(jpeg, type="ops_exclude", ops=["image.flip"])[0]
    assert verdict(jpeg, type="step_arg", op="image.flip", arg="direction", equals="horizontal")[0]
    assert not verdict(jpeg, type="step_arg", op="image.flip", arg="direction", equals="vertical")[
        0
    ]
    assert not verdict(jpeg, type="step_arg", op="image.text", arg="text", contains="x")[0]


def test_step_arg_contains_ignores_case(engine: Engine, photo: str) -> None:
    ctx = outcome(
        engine,
        {
            "in": photo,
            "ops": [["image.text", {"text": "Summer SALE"}], ["export", {"preset": "web_jpeg"}]],
        },
    )
    assert verdict(ctx, type="step_arg", op="image.text", arg="text", contains="summer sale")[0]
    assert not verdict(ctx, type="step_arg", op="image.text", arg="text", contains="winter")[0]


def test_recipe_grader(engine: Engine, photo: str, jpeg: Context) -> None:
    via_recipe = outcome(engine, {"recipe": "batch_thumbnails", "params": {"in": photo}})
    assert verdict(via_recipe, type="recipe", name="batch_thumbnails")[0]
    assert not verdict(via_recipe, type="recipe", name="watermark_batch")[0]
    ok, detail = verdict(jpeg, type="recipe", name="batch_thumbnails")
    assert not ok and "None" in detail


def test_agent_ok_reflects_the_agents_status(jpeg: Context, engine: Engine, photo: str) -> None:
    assert verdict(jpeg, type="agent_ok")[0]
    failed = outcome(
        engine, {"in": photo, "ops": [["export", {"preset": "web_jpeg"}]]}, status="budget"
    )
    ok, detail = verdict(failed, type="agent_ok")
    assert not ok and "budget" in detail


def test_asks_question(engine: Engine, jpeg: Context) -> None:
    def ctx(**kw: Any) -> Context:
        return Context(AgentResult(**kw), engine)

    assert verdict(ctx(status="needs_input", question="Which size?"), type="asks_question")[0]
    assert not verdict(ctx(status="ok", question="Which size?"), type="asks_question")[0]
    assert not verdict(ctx(status="needs_input", question=""), type="asks_question")[0]
    with_output = ctx(status="needs_input", question="?", outputs={"s": "asset:x"})
    ok, detail = verdict(with_output, type="asks_question")
    assert not ok and "also produced output" in detail
    assert not verdict(jpeg, type="asks_question")[0]


@pytest.mark.parametrize(
    "cfg",
    [
        {"type": "dims", "width": 1},
        {"type": "format", "formats": ["JPEG"]},
        {"type": "max_bytes", "bytes": 10**9},
        {"type": "alpha", "present": False},
    ],
    ids=lambda c: c["type"],
)
def test_output_graders_fail_when_there_is_no_output(engine: Engine, cfg: dict[str, Any]) -> None:
    empty = Context(AgentResult(status="no_output"), engine)
    ok, detail = GRADERS[cfg["type"]](cfg, empty)
    assert not ok and "no output" in detail


def test_grade_reports_names_and_soft_flags(jpeg: Context) -> None:
    results = grade(
        ({"type": "agent_ok"}, {"type": "format", "formats": ["PNG"], "soft": True}), jpeg
    )
    assert results == [
        GraderResult("agent_ok", True, "ok", False),
        GraderResult("format", False, results[1].detail, True),
    ]


# -- video and audio deliverables --------------------------------------------------------------

AV = Path(__file__).resolve().parents[1] / "fixtures" / "av"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def files(engine: Engine, *paths: Path) -> Context:
    """The deliverables as plain files: how the baseline sees them."""
    return Context(AgentResult(status="ok"), engine, files=list(paths))


@needs_ffmpeg
def test_video_graders_judge_size_length_sound_and_kind(engine: Engine) -> None:
    clip = files(engine, AV / "clip_landscape.mp4")  # 640x360, 4 s, with sound
    assert verdict(clip, type="kind", **{"is": "video"})[0]
    assert not verdict(clip, type="kind", **{"is": "audio"})[0]
    assert verdict(clip, type="dims", width=640, height=360)[0]
    assert (
        verdict(clip, type="dims", aspect=[16, 9])[0]
        and verdict(clip, type="dims", orientation="landscape")[0]
    )
    assert not verdict(clip, type="dims", width=1080)[0]
    assert verdict(clip, type="format", formats=["MP4"])[0]
    ok, detail = verdict(clip, type="format", formats=["MP3", "WAV"])
    assert not ok and "format MP4" in detail
    assert verdict(clip, type="duration", seconds=4)[0]
    ok, detail = verdict(clip, type="duration", seconds=6)
    assert not ok and "4.00s long, expected 6s" in detail
    assert verdict(clip, type="duration", min=3, max=5)[0]
    assert (
        not verdict(clip, type="duration", max=3)[0]
        and not verdict(clip, type="duration", min=5)[0]
    )
    assert (
        verdict(clip, type="sound", present=True)[0]
        and not verdict(clip, type="sound", present=False)[0]
    )
    assert (
        verdict(clip, type="picture", present=True)[0]
        and not verdict(clip, type="picture", present=False)[0]
    )


@needs_ffmpeg
def test_a_video_without_sound_and_audio_without_picture(engine: Engine) -> None:
    silent = files(engine, AV / "clip_silent.mp4")
    assert verdict(silent, type="sound", present=False)[0]
    ok, detail = verdict(silent, type="sound", present=True)
    assert not ok and "no audio track" in detail
    tone = files(engine, AV / "tone.wav")
    assert verdict(tone, type="kind", **{"is": "audio"})[0]
    assert verdict(tone, type="format", formats=["WAV"])[0]
    assert verdict(tone, type="picture", present=False)[0]
    ok, detail = verdict(tone, type="dims", width=10)
    assert not ok and "no picture size" in detail
    assert verdict(tone, type="duration", seconds=3)[0]


@needs_ffmpeg
def test_loudness_grader_measures_the_delivered_file(engine: Engine) -> None:
    tone = files(engine, AV / "tone.wav")  # a plain 440 Hz sine, measured at -21.75 LUFS
    ok, detail = verdict(tone, type="loudness", min=-23, max=-20)
    assert ok, detail
    ok, detail = verdict(tone, type="loudness", min=-16, max=-12)
    assert not ok and "-21.8 LUFS, expected -16 to -12" in detail
    ok, detail = verdict(files(engine, AV / "clip_silent.mp4"), type="loudness", min=-30)
    assert not ok and "no audio to measure" in detail


def test_a_file_that_is_neither_picture_nor_media_is_other(engine: Engine, tmp_path: Path) -> None:
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00\x01\x02" * 100)
    ctx = files(engine, junk)
    ok, detail = verdict(ctx, type="kind", **{"is": "video"})
    assert not ok and "a other file" in detail
    assert not verdict(ctx, type="duration", seconds=1)[0]


def test_mixed_deliverables_are_all_judged(engine: Engine, photo: str) -> None:
    ctx = files(engine, Path(engine.store.path(engine.store.resolve(photo).hash)))
    assert verdict(ctx, type="kind", **{"is": "image"})[0]
    assert not verdict(ctx, type="sound", present=True)[0]
    assert verdict(ctx, type="output_count", n=1)[0]


def test_the_new_graders_are_portable_so_the_baseline_is_judged_by_them_too() -> None:
    from atelier.evals.graders import PORTABLE

    assert {"kind", "duration", "sound", "picture", "loudness"} <= PORTABLE
    assert PORTABLE - {"rubric"} <= set(GRADERS)


# -- document deliverables ---------------------------------------------------------------------

DOCS = Path(__file__).resolve().parents[1] / "fixtures" / "docs"


def test_document_graders_read_the_text_pages_and_kind(engine: Engine) -> None:
    report = files(engine, DOCS / "report.pdf")
    assert (
        verdict(report, type="kind", **{"is": "pdf"})[0] and verdict(report, type="pages", n=3)[0]
    )
    ok, detail = verdict(report, type="pages", n=5)
    assert not ok and "3 pages, expected 5" in detail
    assert (
        verdict(report, type="pages", min=2, max=4)[0]
        and not verdict(report, type="pages", max=2)[0]
    )
    assert verdict(report, type="format", formats=["PDF"])[0]
    assert verdict(report, type="text_has", terms=["quick brown fox", "PAGE 2"])[0]  # any case
    ok, detail = verdict(report, type="text_has", terms=["quick", "slow turtle"])
    assert not ok and "lacks ['slow turtle']" in detail
    assert verdict(report, type="text_has", terms=["turtle", "fox"], any=True)[0]
    assert verdict(report, type="text_lacks", terms=["turtle"])[0]
    ok, detail = verdict(report, type="text_lacks", terms=["lazy dog"])
    assert not ok and "still contains" in detail
    assert verdict(report, type="page_text", page=2, has="Page 2")[0]
    assert not verdict(report, type="page_text", page=2, has="Page 3")[0]
    ok, detail = verdict(report, type="page_text", page=9, has="x")
    assert not ok and "no page 9" in detail


def test_document_graders_on_office_files_and_forms(engine: Engine) -> None:
    letter = files(engine, DOCS / "letter.docx")
    assert verdict(letter, type="kind", **{"is": "docx"})[0]
    assert verdict(letter, type="text_has", terms=["Regards, the team", "confidential"])[
        0
    ]  # body, header
    ok, detail = verdict(letter, type="placeholders")
    assert not ok and "placeholder(s) left" in detail
    assert verdict(letter, type="placeholders", max=9)[0]
    assert verdict(letter, type="info", key="tables", n=1)[0]
    assert not verdict(letter, type="info", key="images", min=1)[0]
    deck = files(engine, DOCS / "deck.pptx")
    assert (
        verdict(deck, type="slides", n=3)[0]
        and verdict(deck, type="page_text", page=2, has="Agenda")[0]
    )
    assert not verdict(letter, type="slides", n=1)[0] and not verdict(deck, type="pages", n=3)[0]
    form = files(engine, DOCS / "form.pdf")
    ok, detail = verdict(form, type="form", values={"full_name": "Ada"})
    assert (not ok and "full_name: not filled" in detail) or "full_name" in detail
    assert verdict(form, type="fonts")[0] is False  # the fixtures use Helvetica, not embedded
    assert verdict(form, type="rotation", page=1, degrees=0)[0]
    assert not verdict(form, type="rotation", page=1, degrees=90)[0]


def test_workbook_graders_read_values_and_formulas(engine: Engine) -> None:
    book = files(engine, DOCS / "book.xlsx")  # written by openpyxl: formulas, never calculated
    assert verdict(book, type="cell", ref="B3", equals=10)[0]
    assert verdict(book, type="cell", ref="B6", formula="=SUM(")[0]
    ok, detail = verdict(book, type="cell", ref="B6", equals=60)
    assert not ok and "None" in detail  # no stored result yet
    ok, detail = verdict(book, type="calculated")
    assert not ok and "never been calculated" in detail
    assert verdict(book, type="cell", ref="A1", equals="Report for {{client}}")[0]


def test_a_document_that_cannot_be_read_is_not_a_document(engine: Engine, tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really")
    ctx = files(engine, bad)
    assert not verdict(ctx, type="kind", **{"is": "pdf"})[0]
    assert not verdict(ctx, type="pages", n=1)[0]


def test_the_document_graders_are_portable() -> None:
    from atelier.evals.graders import PORTABLE

    names = {"pages", "slides", "text_has", "text_lacks", "placeholders", "fonts", "form", "cell",
             "calculated", "page_text", "rotation", "info"}  # fmt: skip
    assert names <= PORTABLE and names <= set(GRADERS)


# -- the task loader ---------------------------------------------------------------------------


def load(tmp_path: Path, tasks: list[dict[str, Any]]) -> Any:
    path = tmp_path / "tasks.yaml"
    path.write_text(yaml.safe_dump(tasks))
    return load_tasks(path)


BASE = {
    "id": "t",
    "split": "train",
    "kind": "custom",
    "request": "Do it.",
    "inputs": {"photo": "landscape.jpg"},
    "graders": [{"type": "format", "formats": ["JPEG"]}],
    "reference": {"in": "${photo}", "ops": [["export", {"preset": "web_jpeg"}]]},
}


def test_loader_adds_agent_ok_but_not_to_ask_tasks(tmp_path: Path) -> None:
    (task,) = load(tmp_path, [BASE])
    assert [g["type"] for g in task.graders] == ["agent_ok", "format"]
    ask = {**BASE, "kind": "ambiguous", "graders": [{"type": "asks_question"}], "reference": None}
    (asker,) = load(tmp_path, [ask])
    assert [g["type"] for g in asker.graders] == ["asks_question"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"split": "dev"}, "split must be one of"),
        ({"kind": "hard"}, "kind must be one of"),
        ({"graders": [{"type": "vibes"}]}, "unknown grader type 'vibes'"),
        ({"reference": None}, "needs a reference spec"),
        ({"colour": "red"}, "unknown keys"),
        ({"inputs": {"photo": 3}}, "inputs must map names to file names"),
    ],
)
def test_loader_rejects_bad_tasks(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    with pytest.raises(TaskError, match=message):
        load(tmp_path, [{**BASE, **change}])


def test_loader_rejects_missing_keys_duplicates_and_non_lists(tmp_path: Path) -> None:
    with pytest.raises(TaskError, match="missing request"):
        load(tmp_path, [{k: v for k, v in BASE.items() if k != "request"}])
    with pytest.raises(TaskError, match="duplicate task id 't'"):
        load(tmp_path, [BASE, BASE])
    (tmp_path / "x.yaml").write_text("a: 1")
    with pytest.raises(TaskError, match="expected a list"):
        load_tasks(tmp_path / "x.yaml")
