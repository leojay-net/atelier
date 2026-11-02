"""The ML plugins: pinned models, background removal, transcription, speech denoise.

The model tests need the weights (`atelier models fetch --all`) and the ML extra
(`pip install atelier[ml]`); without them they skip, and the pure tests still run.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from atelier import models
from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, RunLimits, StepFailed
from atelier.ops.registry import Determinism, load_builtin_ops
from conftest import FIXTURES

try:
    import faster_whisper  # noqa: F401
    import onnxruntime  # noqa: F401

    RUNTIME = True
except ImportError:
    RUNTIME = False


def have(*names: str) -> bool:
    return RUNTIME and all(models.is_installed(n) for n in names)


needs_u2net = pytest.mark.skipif(not have("u2netp"), reason="needs `atelier models fetch u2netp`")
needs_whisper = pytest.mark.skipif(
    not have("whisper-tiny") or shutil.which("ffmpeg") is None,
    reason="needs whisper-tiny and ffmpeg",
)
needs_rnnoise = pytest.mark.skipif(
    not have("rnnoise-bd") or shutil.which("ffmpeg") is None, reason="needs rnnoise-bd and ffmpeg"
)


# -- the model registry: pure -------------------------------------------------------------------


def test_the_manifest_pins_every_file_by_size_and_hash() -> None:
    manifest = models.manifest()["models"]
    assert set(manifest) == {"u2netp", "rnnoise-bd", "whisper-tiny", "yunet", "fsrcnn"}
    for name, entry in manifest.items():
        assert entry["license"] and entry["source"] and entry["description"], name
        for f in entry["files"]:
            assert f["url"].startswith("https://") and f["size"] > 0
            assert len(f["sha256"]) == 64 and int(f["sha256"], 16) >= 0
    for entry in manifest.values():  # a commit-pinned URL, never a moving branch
        assert all("/main/" not in f["url"] and "/master/" not in f["url"] for f in entry["files"])


def test_a_missing_model_says_how_to_get_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path))
    assert not models.is_installed("u2netp")
    with pytest.raises(models.ModelError, match=r"run `atelier models fetch u2netp`"):
        models.require("u2netp")
    with pytest.raises(models.ModelError, match="no model named 'nope'"):
        models.model_dir("nope")


def test_fetch_verifies_what_it_downloads_and_refuses_the_wrong_thing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path))

    class Reply(io.BytesIO):
        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(
        models.urllib.request, "urlopen", lambda url, timeout=0: Reply(b"x" * 4574861)
    )
    with pytest.raises(models.ModelError, match="does not match the pinned size and hash"):
        models.fetch("u2netp")
    assert not list((tmp_path / "u2netp").glob("*"))  # nothing half-downloaded is left


def test_only_https_downloads_are_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path))
    real = models.manifest()
    bad = json.loads(json.dumps(real))
    bad["models"]["u2netp"]["files"][0]["url"] = "http://example.com/u2netp.onnx"
    monkeypatch.setattr(models, "manifest", lambda: bad)
    with pytest.raises(models.ModelError, match="only https"):
        models.fetch("u2netp")


def test_the_cli_lists_models(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atelier.cli import main

    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path))
    assert main(["--home", str(tmp_path / "h"), "models"]) == 0
    out = capsys.readouterr().out
    assert "u2netp" in out and "not installed" in out and "Apache-2.0" in out
    assert main(["--home", str(tmp_path / "h"), "models", "fetch"]) == 1
    assert main(["--home", str(tmp_path / "h"), "models", "verify", "u2netp"]) == 1  # not there yet


def test_the_ml_ops_are_seeded_ml_and_import_no_runtime_at_import_time() -> None:
    registry = load_builtin_ops()
    ml_ops = (
        "image.remove_background", "transcribe", "video.auto_reframe",
        "video.blur_faces", "image.upscale", "video.upscale",
    )  # fmt: skip
    for name in ml_ops:
        assert registry.get(name).determinism is Determinism.SEEDED_ML  # type: ignore[union-attr]
    done = subprocess.run(
        [sys.executable, "-c", "import sys, atelier.ops.registry as r; r.load_builtin_ops(); "
         "sys.exit(any(m.split('.')[0] in ('onnxruntime', 'faster_whisper', 'ctranslate2', 'cv2') "
         "for m in sys.modules))"],
        capture_output=True,
    )  # fmt: skip
    assert done.returncode == 0  # a warm standby worker never loads a model runtime


def test_an_op_without_its_model_is_unavailable_with_the_instruction(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path))
    models.model_id.cache_clear()
    photo = engine.store.put_file(FIXTURES / "photo.png").handle
    with pytest.raises(StepFailed, match=r"atelier models fetch u2netp"):
        engine.run({"in": photo, "ops": [["image.remove_background", {}]]})


# -- background removal -------------------------------------------------------------------------


def subject_image(tmp: Path) -> Path:
    """A red disc with a highlight on a noisy gray gradient: an obvious subject."""
    rng = np.random.default_rng(1)
    grad = np.tile(np.linspace(90, 140, 400)[None, :, None], (300, 1, 3))
    im = Image.fromarray(np.clip(grad + rng.normal(0, 3, (300, 400, 3)), 0, 255).astype("uint8"))
    draw = ImageDraw.Draw(im)
    draw.ellipse((120, 60, 280, 240), fill=(200, 30, 30))
    draw.ellipse((170, 110, 200, 140), fill=(250, 220, 200))
    path = tmp / "disc.png"
    im.save(path)
    return path


def alpha_of(engine: Engine, result: JobResult) -> np.ndarray:
    out = next(iter(result.outputs.values()))
    return np.asarray(Image.open(engine.store.path(out.hash)))[..., 3]


@needs_u2net
def test_the_subject_stays_and_the_background_goes(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(subject_image(tmp_path)).handle
    result = engine.run({"in": handle, "ops": [["image.remove_background", {}]], "checks": "none"})
    a = alpha_of(engine, result)
    assert a.shape == (300, 400)
    assert a[150, 200] > 240 and a[100:200, 150:250].mean() > 240  # the subject
    assert max(a[5, 5], a[290, 395], a[5, 395], a[290, 5]) < 30  # the corners
    assert a[:40].mean() < 10 and a[260:].mean() < 10
    assert next(iter(result.outputs.values())).media.value == "image"


@needs_u2net
def test_threshold_feather_and_background_options(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(subject_image(tmp_path)).handle

    def run(**args: Any) -> JobResult:
        return engine.run(
            {"in": handle, "ops": [["image.remove_background", args]], "checks": "none"}
        )

    hard = alpha_of(engine, run(threshold=0.5))
    assert set(np.unique(hard)) <= {0, 255}  # a hard cut has no soft edge
    soft, feathered = alpha_of(engine, run()), alpha_of(engine, run(feather=8))
    assert (feathered[100, 100:300].astype(int).max() - feathered[100, 100:300].min()) >= 0
    assert (
        np.abs(np.diff(feathered[150].astype(int))).max()
        <= np.abs(np.diff(soft[150].astype(int))).max()
    )
    filled = run(background="#ffffff")
    out = Image.open(engine.store.path(next(iter(filled.outputs.values())).hash))
    assert out.mode == "RGB" and out.getpixel((5, 5)) == (255, 255, 255)  # no transparency left


@needs_u2net
def test_existing_transparency_is_kept(engine: Engine, tmp_path: Path) -> None:
    im = Image.open(subject_image(tmp_path)).convert("RGBA")
    im.paste(
        (0, 0, 0, 0), (0, 0, 60, 300)
    )  # a transparent strip on the left edge of the subject scene
    im.paste((0, 0, 0, 0), (140, 80, 260, 220))  # and a hole in the subject
    path = tmp_path / "holed.png"
    im.save(path)
    handle = engine.store.put_file(path).handle
    a = alpha_of(
        engine,
        engine.run({"in": handle, "ops": [["image.remove_background", {}]], "checks": "none"}),
    )
    assert a[150, 200] == 0 and a[150, 30] == 0


@needs_u2net
def test_the_same_image_gives_the_same_bytes_on_a_fresh_engine(
    engine: Engine, tmp_path: Path
) -> None:
    src = subject_image(tmp_path)

    def out(e: Engine) -> bytes:
        h = e.store.put_file(src).handle
        r = e.run({"in": h, "ops": [["image.remove_background", {}]], "checks": "none"})
        return e.store.path(next(iter(r.outputs.values())).hash).read_bytes()

    first = out(engine)
    fresh = Engine.open(tmp_path / "other", run_limits=RunLimits(wall_seconds=120))
    assert out(fresh) == first


@needs_u2net
def test_a_cutout_flows_into_the_image_ops(engine: Engine, tmp_path: Path) -> None:
    handle = engine.store.put_file(subject_image(tmp_path)).handle
    result = engine.run(
        {
            "in": handle,
            "ops": [["image.remove_background", {}], ["export", {"preset": "transparent_png"}]],
        }
    )
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    assert next(iter(result.outputs.values())).meta["mode"] in ("RGBA", "LA")


# -- transcription ----------------------------------------------------------------------------


@needs_whisper
def test_speech_becomes_captions(engine: Engine) -> None:
    speech = engine.store.put_file(FIXTURES / "av" / "speech.wav").handle
    result = engine.run({"in": speech, "ops": [["transcribe", {}]], "checks": "none"})
    rep = next(iter(result.outputs.values()))
    assert rep.media.value == "text" and rep.meta["language"] == "en" and rep.meta["cues"] >= 1
    text = engine.store.path(rep.hash).read_text().lower()
    assert "channel" in text and "engine" in text and "-->" in text
    assert rep.summary.startswith("captions ") and "language en" in rep.summary
    from atelier.av.subtitles import parse

    cues = parse(engine.store.path(rep.hash).read_text())  # our own parser reads what we wrote
    assert cues[0].start >= 0 and all(c.end > c.start for c in cues)


@needs_whisper
def test_transcription_is_repeatable_and_options_shape_the_captions(
    engine: Engine, tmp_path: Path
) -> None:
    speech = FIXTURES / "av" / "speech.wav"

    def srt(e: Engine, **args: Any) -> str:
        h = e.store.put_file(speech).handle
        r = e.run({"in": h, "ops": [["transcribe", args]], "checks": "none"})
        return e.store.path(next(iter(r.outputs.values())).hash).read_text()

    first = srt(engine)
    assert srt(Engine.open(tmp_path / "o", run_limits=RunLimits(wall_seconds=120))) == first
    short = srt(engine, max_chars=16)
    assert short.count("-->") > first.count("-->")  # narrower captions, more of them
    assert srt(engine, language="en") == first


@needs_whisper
def test_transcription_errors_are_actionable(engine: Engine, tmp_path: Path) -> None:
    from atelier.spec.errors import SpecInvalid

    silent = engine.store.put_file(FIXTURES / "av" / "clip_silent.mp4").handle
    with pytest.raises(StepFailed, match="no audio track to transcribe"):
        engine.run({"in": silent, "ops": [["transcribe", {}]]})
    tone = engine.store.put_file(FIXTURES / "av" / "tone.wav").handle
    with pytest.raises(StepFailed, match="no speech was recognized"):
        engine.run({"in": tone, "ops": [["transcribe", {}]]})
    with pytest.raises(SpecInvalid, match="is not a language code"):
        engine.validate({"in": tone, "ops": [["transcribe", {"language": "English"}]]})
    photo = engine.store.put_file(FIXTURES / "photo.png").handle
    with pytest.raises(SpecInvalid, match="must be video\\|audio"):
        engine.validate({"in": photo, "ops": [["transcribe", {}]]})


@needs_whisper
def test_a_video_is_captioned_from_its_own_speech_in_one_pipeline(
    engine: Engine, tmp_path: Path
) -> None:
    clip = tmp_path / "talk.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=640x360:r=30:d=3", "-i", str(FIXTURES / "av" / "speech.wav"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
        check=True,
    )  # fmt: skip
    handle = engine.store.put_file(clip).handle
    spec = {
        "version": 1,
        "inputs": {"v": handle},
        "steps": [
            {"id": "srt", "op": "transcribe", "in": "v", "args": {}},
            {"id": "cap", "op": "video.captions", "in": ["v", "srt"],
             "args": {"style": "bold_pop"}},
            {"id": "out", "op": "export", "in": "cap",
             "args": {"preset": "web_h264", "overrides": {"encoder_preset": "veryfast"}}},
        ],
        "outputs": ["out"],
    }  # fmt: skip
    result = engine.run(spec)
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    assert next(iter(result.outputs.values())).meta["boxes"]  # captions were drawn


# -- speech denoise ---------------------------------------------------------------------------


def rms(path: Path, start: float, end: float) -> float:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    x = np.frombuffer(raw, dtype=np.float32)[int(start * 16000) : int(end * 16000)]
    return float(np.sqrt(np.mean(x**2)))


@needs_rnnoise
def test_hiss_around_a_voice_is_removed_and_the_voice_stays(engine: Engine, tmp_path: Path) -> None:
    noisy = tmp_path / "noisy.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(FIXTURES / "av" / "speech.wav"),
         "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.05:sample_rate=16000:d=6",
         "-filter_complex", "[0]apad=whole_dur=6[v];[v][1]amix=inputs=2:normalize=0", "-ac", "1",
         "-c:a", "pcm_s16le", str(noisy)],
        check=True,
    )  # fmt: skip
    handle = engine.store.put_file(noisy).handle
    result = engine.run(
        {
            "in": handle,
            "ops": [["audio.denoise_speech", {}], ["export", {"preset": "wav_master"}]],
            "checks": "none",
        }
    )
    clean = engine.store.path(next(iter(result.outputs.values())).hash)
    assert rms(clean, 4.0, 5.8) < rms(noisy, 4.0, 5.8) * 0.2  # the hiss-only tail is nearly gone
    assert rms(clean, 0.3, 2.5) > rms(noisy, 0.3, 2.5) * 0.25  # the voice is still there


@needs_rnnoise
def test_a_missing_speech_model_is_reported_before_anything_runs(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ATELIER_MODELS", str(tmp_path))
    models.model_id.cache_clear()
    tone = engine.store.put_file(FIXTURES / "av" / "tone.wav").handle
    with pytest.raises(StepFailed, match=r"atelier models fetch rnnoise-bd"):
        engine.run({"in": tone, "ops": [["audio.denoise_speech", {}]]})


@needs_whisper
def test_the_auto_captioned_recipe_needs_no_caption_file(engine: Engine, tmp_path: Path) -> None:
    clip = tmp_path / "talk.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=640x360:r=30:d=3", "-i", str(FIXTURES / "av" / "speech.wav"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
        check=True,
    )  # fmt: skip
    handle = engine.store.put_file(clip).handle
    result = engine.run({"recipe": "auto_captioned_reel", "params": {"in": handle}})
    assert result.passed, [c.line() for cs in result.checks.values() for c in cs]
    meta = next(iter(result.outputs.values())).meta
    assert (meta["video"]["display"] == [1080, 1920]) and meta["boxes"]
    assert result.spec.origin == {"recipe": "auto_captioned_reel", "version": 1}
