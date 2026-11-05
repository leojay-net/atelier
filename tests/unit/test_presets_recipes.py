from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from atelier import presets
from atelier.av.audio_export import AudioExportSettings
from atelier.av.export import VideoExportSettings
from atelier.docs.export import OfficeExportSettings, PdfExportSettings
from atelier.engine.engine import Engine
from atelier.ops.catalogue import describe_name, full_catalogue, public_fields
from atelier.ops.image.composite import WatermarkSettings
from atelier.ops.image.export import ImageExportSettings
from atelier.ops.registry import load_builtin_ops
from atelier.recipes import all_recipes
from atelier.spec.errors import SpecInvalid

KIND_MODELS = {
    "export.image": ImageExportSettings,
    "export.video": VideoExportSettings,
    "export.audio": AudioExportSettings,
    "export.pdf": PdfExportSettings,
    "export.docx": OfficeExportSettings,
    "export.xlsx": OfficeExportSettings,
    "export.pptx": OfficeExportSettings,
    "watermark": WatermarkSettings,
}


@pytest.mark.parametrize(
    "preset", sorted(presets.all_presets().values(), key=lambda p: p.id), ids=lambda p: p.id
)
def test_every_preset_validates(preset: presets.Preset) -> None:
    KIND_MODELS[preset.kind].model_validate(preset.settings)


def test_every_public_param_is_documented() -> None:
    """Phase 1 exit criterion: 100% of op args documented in the catalogue."""
    missing = [
        (d.name, n)
        for d in load_builtin_ops().ops()
        for n, f in public_fields(d).items()
        if not f.description
    ]
    assert missing == []


def test_every_catalogue_name_can_be_described() -> None:
    """describe_op backs onto this: a name the catalogue advertises must never crash it."""
    registry = load_builtin_ops()
    names = [
        *(d.name for d in registry.ops(include_hidden=True)),
        *presets.all_presets(),
        *all_recipes(),
    ]
    for name in names:
        assert describe_name(registry, name), name


def test_catalogue_stays_small() -> None:
    """Roadmap budget: the system-prompt catalogue stays under ~3k tokens (≈ 4 chars/token).

    Raised from 3000 to 3020 when video.auto_reframe and video.blur_faces shipped (video.stabilize
    was hidden from the catalogue and both visible descriptions were trimmed first), then to 3040
    when image.upscale and video.upscale shipped (video.upscale hidden, same trade as stabilize:
    the more specialized of the pair; both descriptions trimmed first). Each bump covers only what
    hiding the niche op and trimming left over, not headroom for the next one. Then to 3240 for
    video.crop, video.pip and video.lower_third (video.transition hidden: video.concat's
    `transition` covers it; most of the rest is the generated argument lists of pip/lower_third).
    Then to 3290 for pptx.create (presentations from scratch; description trimmed first), and to
    3340 for video.sfx and the slideshow's motion/durations (production quality, docs/14)."""
    assert len(full_catalogue(load_builtin_ops())) / 4 < 3340


def errors(engine: Engine, spec: Any) -> list[str]:
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(spec)
    return [e.line() for e in exc.value.errors]


def test_recipes_load() -> None:
    assert set(all_recipes()) == {
        "watermark_batch",
        "batch_thumbnails",
        "social_clip",
        "extract_audio",
        "captioned_reel",
        "podcast_clean",
        "voiceover_music",
        "music_video",
        "invoice_from_template",
        "report_docx_to_pdf",
        "slides_from_outline",
        "contract_redact_and_stamp",
        "auto_captioned_reel",
    }


def test_recipe_expands_and_records_origin(engine: Engine, photo: str, logo: str) -> None:
    spec = engine.validate(
        {"recipe": "watermark_batch", "params": {"in": photo, "logo": logo, "format": "web_webp"}}
    )
    assert [s.op.name for s in spec.steps] == ["image.watermark", "export"]
    assert spec.steps[1].args["preset"] == "web_webp"
    assert spec.origin == {"recipe": "watermark_batch", "version": 1}


def test_recipe_when_skips_steps(engine: Engine, photo: str) -> None:
    plain = engine.validate({"recipe": "batch_thumbnails", "params": {"in": photo}})
    titled = engine.validate({"recipe": "batch_thumbnails", "params": {"in": photo, "title": "Hi"}})
    assert [s.op.name for s in plain.steps] == ["export"]
    # a title crops to the preset's aspect first, so the text is laid out in the final frame
    assert [s.op.name for s in titled.steps] == ["image.crop", "image.text", "export"]
    assert titled.steps[1].args["text"] == "Hi"


@pytest.mark.parametrize(
    ("size", "aspect"), [("og_image", 40 / 21), ("thumb_youtube", 16 / 9)], ids=["og_image", "yt"]
)
def test_recipe_when_list_needs_every_condition(
    engine: Engine, photo: str, size: str, aspect: float
) -> None:
    """`when: [a, b]` means a and b: exactly one crop step applies per size, only with a title."""
    titled = engine.validate(
        {"recipe": "batch_thumbnails", "params": {"in": photo, "size": size, "title": "Hi"}}
    )
    crops = [s for s in titled.steps if s.op.name == "image.crop"]
    assert [c.args["aspect"] for c in crops] == [pytest.approx(aspect, abs=1e-6)]  # canonical float
    untitled = engine.validate(
        {"recipe": "batch_thumbnails", "params": {"in": photo, "size": size}}
    )
    assert [s.op.name for s in untitled.steps] == ["export"]


def _photo_of(engine: Engine, size: tuple[int, int], tmp: Path) -> str:
    im = Image.new("RGB", size, (70, 120, 200))
    d = ImageDraw.Draw(im)
    d.ellipse((size[0] * 0.55, size[1] * 0.3, size[0] * 0.85, size[1] * 0.75), fill=(230, 80, 40))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    path = tmp / f"{size[0]}x{size[1]}.png"
    path.write_bytes(buf.getvalue())
    return engine.store.put_file(path).handle


@pytest.mark.parametrize("size", ["og_image", "thumb_youtube"])
@pytest.mark.parametrize(
    "dims",
    [(640, 427), (700, 700), (540, 960), (600, 800), (1050, 450)],
    ids=lambda d: f"{d[0]}x{d[1]}",
)
def test_titled_thumbnail_passes_its_own_checks_at_any_aspect(
    engine: Engine, tmp_path: Path, size: str, dims: tuple[int, int]
) -> None:
    """Regression: v1 laid the title out before the preset's fill-crop, so it left the safe area
    (or was cut off) for 11 of 16 size x aspect combinations, including 3:2, 4:3 and 1:1."""
    handle = _photo_of(engine, dims, tmp_path)
    result = engine.run(
        {
            "recipe": "batch_thumbnails",
            "params": {"in": handle, "size": size, "title": "Launch day"},
        }
    )
    failures = [r.line() for rs in result.checks.values() for r in rs if r.status.value != "pass"]
    assert result.passed, failures
    assert not failures


def test_recipe_and_equivalent_spec_share_cache(engine: Engine, photo: str) -> None:
    engine.run({"recipe": "batch_thumbnails", "params": {"in": photo}})
    again = engine.run({"in": photo, "ops": [["export", {"preset": "thumb_youtube"}]]})
    assert again.reused == 1


def test_recipe_param_errors(engine: Engine, photo: str) -> None:
    assert errors(
        engine, {"recipe": "batch_thumbnails", "params": {"in": photo, "size": "og", "titel": "x"}}
    ) == [
        "params.titel: recipe batch_thumbnails has no param 'titel' → use one of in|size|title "
        "(did you mean title?)",
        "params.size: 'og' invalid → use one of thumb_youtube|og_image (did you mean og_image?)",
    ]
    assert errors(engine, {"recipe": "watermark_batch", "params": {"in": photo}}) == [
        "params.logo: required by recipe watermark_batch"
    ]


def test_recipe_asset_errors_point_at_params(engine: Engine) -> None:
    assert errors(engine, {"recipe": "batch_thumbnails", "params": {"in": "photo.jpg"}}) == [
        "params.in: 'photo.jpg' is not an asset handle; upload the file and use its "
        "asset:<sha256> handle (paths and URLs are not allowed)"
    ]


def test_unknown_recipe(engine: Engine) -> None:
    (line,) = errors(engine, {"recipe": "batch_thumbnail"})
    assert line.startswith("recipe: recipe 'batch_thumbnail' not found → use one of ")
    assert line.endswith("(did you mean batch_thumbnails?)")
    assert "|social_clip|" in line  # the list is every recipe; it grows with the catalogue
