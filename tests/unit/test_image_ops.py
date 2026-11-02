"""Image ops end to end through the sandboxed worker."""

from __future__ import annotations

from typing import Any

import pytest
from PIL import Image

from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, StepFailed, StepReport
from atelier.spec.errors import SpecInvalid
from conftest import FIXTURES


@pytest.fixture
def photo(engine: Engine) -> str:
    return engine.store.put_file(FIXTURES / "photo.png").handle


@pytest.fixture
def logo(engine: Engine) -> str:
    return engine.store.put_file(FIXTURES / "logo.png").handle


def run1(engine: Engine, handle: str, *ops: Any) -> tuple[JobResult, StepReport]:
    result = engine.run(
        {"in": handle, "ops": [list(o) if isinstance(o, tuple) else o for o in ops]}
    )
    return result, next(iter(result.outputs.values()))


def image(engine: Engine, rep: StepReport) -> Image.Image:
    im = Image.open(engine.store.path(rep.hash))
    im.load()
    return im


def errors(engine: Engine, spec: Any) -> list[str]:
    with pytest.raises(SpecInvalid) as exc:
        engine.validate(spec)
    return [e.line() for e in exc.value.errors]


# -- loading ---------------------------------------------------------------------------


def test_exif_orientation_is_applied(engine: Engine) -> None:
    sideways = engine.store.put_file(FIXTURES / "photo_exif6.jpg").handle
    _, rep = run1(engine, sideways, ("image.resize", {"scale": 0.5}))
    assert (rep.meta["width"], rep.meta["height"]) == (320, 214)  # displayed landscape


# -- geometry --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "size"),
    [
        ({"aspect": "1:1"}, (427, 427)),
        ({"aspect": "16:9", "focus": "top"}, (640, 360)),
        ({"box": [0.25, 0.25, 0.5, 0.5]}, (320, 213)),
    ],
)
def test_crop(engine: Engine, photo: str, args: dict[str, Any], size: tuple[int, int]) -> None:
    _, rep = run1(engine, photo, ("image.crop", args))
    assert image(engine, rep).size == size


def test_attention_crop_finds_the_subject(engine: Engine, photo: str) -> None:
    # The fixture's high-detail "subject" (red disc) is right of center.
    _, rep = run1(engine, photo, ("image.crop", {"aspect": "1:1", "focus": "attention"}))
    red = image(engine, rep).convert("RGB").getpixel((300, 150))
    assert isinstance(red, tuple) and red[0] > 200 and red[1] < 120


def test_crop_needs_exactly_one_mode(engine: Engine, photo: str) -> None:
    assert errors(engine, {"in": photo, "ops": [["image.crop", {}]]}) == [
        "ops[0].args: {} invalid: give exactly one of box or aspect"
    ]


@pytest.mark.parametrize(
    ("args", "size"),
    [
        ({"width": 320}, (320, 214)),
        ({"width": 300, "height": 300}, (300, 200)),
        ({"width": 300, "height": 300, "mode": "fill"}, (300, 300)),
        ({"width": 100, "height": 400, "mode": "exact"}, (100, 400)),
        ({"scale": 2}, (1280, 854)),
        ({"width": 2000, "upscale": False}, (640, 427)),
    ],
)
def test_resize(engine: Engine, photo: str, args: dict[str, Any], size: tuple[int, int]) -> None:
    _, rep = run1(engine, photo, ("image.resize", args))
    assert image(engine, rep).size == size


def test_fill_with_attention_keeps_text_in_frame(engine: Engine, photo: str) -> None:
    text = ("image.text", {"text": "Corner title", "box": [0.02, 0.02, 0.4, 0.2]})
    _, center = run1(
        engine, photo, text, ("image.resize", {"width": 300, "height": 300, "mode": "fill"})
    )
    _, attention = run1(
        engine,
        photo,
        text,
        ("image.resize", {"width": 300, "height": 300, "mode": "fill", "focus": "attention"}),
    )
    assert center.meta["boxes"][0].get("clipped") is True
    assert "clipped" not in attention.meta["boxes"][0]


@pytest.mark.parametrize(
    ("args", "size"),
    [
        ({"aspect": "9:16"}, (640, 1138)),
        ({"margin": 0.1, "fill": "mirror"}, (726, 513)),
        ({"width": 800, "height": 500, "fill": "blur", "anchor": "top_left"}, (800, 500)),
    ],
)
def test_pad(engine: Engine, photo: str, args: dict[str, Any], size: tuple[int, int]) -> None:
    _, rep = run1(engine, photo, ("image.pad", args))
    assert image(engine, rep).size == size


def test_pad_smaller_canvas_fails_clearly(engine: Engine, photo: str) -> None:
    with pytest.raises(StepFailed, match="smaller than the image 640x427"):
        run1(engine, photo, ("image.pad", {"width": 100, "height": 100}))


def test_pad_transparent_adds_alpha(engine: Engine, photo: str) -> None:
    _, rep = run1(engine, photo, ("image.pad", {"margin": 0.1, "color": "transparent"}))
    assert rep.meta["mode"] == "RGBA"


def test_rotate(engine: Engine, photo: str) -> None:
    _, quarter = run1(engine, photo, ("image.rotate", {"degrees": 90}))
    _, tilt = run1(engine, photo, ("image.rotate", {"degrees": 15}))
    assert image(engine, quarter).size == (427, 640)
    assert tilt.meta["mode"] == "RGBA"
    src = Image.open(FIXTURES / "photo.png").convert("RGB")
    rotated = src.transpose(Image.Transpose.ROTATE_270)
    assert image(engine, quarter).tobytes() == rotated.tobytes()  # lossless


def test_flip_mirrors_boxes(engine: Engine, photo: str) -> None:
    _, rep = run1(
        engine,
        photo,
        ("image.text", {"text": "Left", "box": [0.0, 0.0, 0.3, 0.2], "align": "left"}),
        ("image.flip", {}),
    )
    x, _, w, _ = rep.meta["boxes"][0]["box"]
    assert x + w == pytest.approx(1.0, abs=1e-5)


# -- color -----------------------------------------------------------------------------


def test_neutral_adjust_is_identity(engine: Engine, photo: str) -> None:
    _, rep = run1(engine, photo, ("image.adjust", {}))
    assert (
        image(engine, rep).tobytes() == Image.open(FIXTURES / "photo.png").convert("RGB").tobytes()
    )


def test_adjust_saturation_zero_is_gray(engine: Engine, photo: str) -> None:
    _, rep = run1(engine, photo, ("image.adjust", {"saturation": 0}))
    r, g, b = image(engine, rep).convert("RGB").getpixel((470, 210))  # type: ignore[misc]
    assert abs(r - g) <= 1 and abs(g - b) <= 1


def test_convert_flatten(engine: Engine, logo: str) -> None:
    _, rep = run1(engine, logo, ("image.convert", {"alpha": "flatten", "background": "#000"}))
    assert rep.meta["mode"] == "RGB"
    assert image(engine, rep).getpixel((0, 0)) == (0, 0, 0)  # transparent corner → black


# -- layering and text -----------------------------------------------------------------


def test_composite_records_overlay_box(engine: Engine, photo: str, logo: str) -> None:
    result = engine.run(
        {
            "inputs": {"p": photo, "l": logo},
            "steps": [
                {
                    "id": "c",
                    "op": "image.composite",
                    "in": ["p", "l"],
                    "args": {"anchor": "bottom_left", "scale": 0.25, "margin": 0},
                }
            ],
            "outputs": ["c"],
        }
    )
    box = result.outputs["c"].meta["boxes"][0]
    assert box["kind"] == "overlay"
    assert box["box"] == pytest.approx([0.0, 1 - 64 / 427, 0.25, 64 / 427], abs=1e-3)


def test_text_autofit_and_contrast(engine: Engine, photo: str) -> None:
    _, rep = run1(engine, photo, ("image.text", {"text": "Hello", "box": [0.1, 0.1, 0.8, 0.3]}))
    box = rep.meta["boxes"][0]
    assert box["fit"] is True
    assert box["font_px"] >= 60
    assert box["contrast"] > 1


def test_text_overflow_is_reported(engine: Engine, photo: str) -> None:
    result, _ = run1(
        engine,
        photo,
        (
            "image.text",
            {
                "text": "far too much text for a tiny box " * 4,
                "box": [0.4, 0.4, 0.1, 0.05],
                "size": 0.1,
            },
        ),
    )
    fit = [c for c in next(iter(result.checks.values())) if c.name == "text_fit"]
    assert fit and fit[0].status == "fail"


def test_text_font_errors(engine: Engine, photo: str) -> None:
    assert errors(
        engine, {"in": photo, "ops": [["image.text", {"text": "x", "font": "Arial"}]]}
    ) == ["ops[0].args.font: font 'Arial' invalid → use one of Inter|Noto Sans|Noto Serif"]
    lines = errors(
        engine,
        {
            "in": photo,
            "ops": [["image.text", {"text": "x", "font": "Noto Sans", "weight": "semibold"}]],
        },
    )
    assert lines == [
        "ops[0].args: {'text': 'x', 'font': 'Noto Sans', 'wei… invalid: "
        "Noto Sans has weights regular|bold, not semibold"
    ]


def test_background_auto_raises_contrast(engine: Engine) -> None:
    white = engine.store.put_bytes(_png(Image.new("RGB", (400, 200), "white"))).handle
    args = {"text": "Faint", "color": "#dddddd"}
    _, plain = run1(engine, white, ("image.text", args))
    _, guarded = run1(engine, white, ("image.text", {**args, "background": "auto"}))
    assert plain.meta["boxes"][0]["contrast"] < 1.5
    box = guarded.meta["boxes"][0]
    assert box["contrast"] >= box["min_contrast"]  # 3:1 here: auto-fit makes it large text
    _, small = run1(engine, white, ("image.text", {**args, "size": 0.05, "background": "auto"}))
    assert small.meta["boxes"][0]["min_contrast"] == 4.5
    assert small.meta["boxes"][0]["contrast"] >= 4.5


def _png(im: Image.Image) -> bytes:
    import io

    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_watermark_needs_logo_or_text(engine: Engine, photo: str) -> None:
    assert errors(engine, {"in": photo, "ops": ["image.watermark"]}) == [
        "ops[0]: image.watermark needs a logo as 2nd input, or args.text"
    ]


def test_watermark_unknown_preset(engine: Engine, photo: str) -> None:
    assert errors(
        engine, {"in": photo, "ops": [["image.watermark", {"preset": "corner_subtl", "text": "x"}]]}
    ) == [
        "ops[0].args.preset: 'corner_subtl' invalid → use one of "
        "center_large|corner_bold|corner_subtle|tiled_proof (did you mean corner_subtle?)"
    ]


@pytest.mark.parametrize("preset", ["corner_subtle", "corner_bold", "center_large", "tiled_proof"])
def test_watermark_presets(engine: Engine, photo: str, logo: str, preset: str) -> None:
    for spec in (
        {"in": photo, "ops": [["image.watermark", {"preset": preset, "text": "© Atelier"}]]},
        {
            "inputs": {"p": photo, "l": logo},
            "outputs": ["w"],
            "steps": [
                {"id": "w", "op": "image.watermark", "in": ["p", "l"], "args": {"preset": preset}}
            ],
        },
    ):
        rep = next(iter(engine.run(spec).outputs.values()))
        assert rep.meta["boxes"][-1]["kind"] == "watermark"


def test_grid_labels_must_match_inputs(engine: Engine, photo: str, logo: str) -> None:
    spec = {
        "inputs": {"a": photo, "b": logo},
        "outputs": ["g"],
        "steps": [{"id": "g", "op": "image.grid", "in": ["a", "b"], "args": {"labels": ["one"]}}],
    }
    assert errors(engine, spec) == ["steps[0].in: labels has 1 entries for 2 inputs"]
    spec["steps"][0]["args"]["labels"] = ["one", "two"]  # type: ignore[index]
    rep = engine.run(spec).outputs["g"]
    assert (rep.meta["width"], rep.meta["height"]) == (836, 456)


# -- export ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset", "fmt", "size"),
    [
        ("web_jpeg", "JPEG", (640, 427)),
        ("web_webp", "WEBP", (640, 427)),
        ("web_avif", "AVIF", (640, 427)),
        ("og_image", "JPEG", (1200, 630)),
        ("thumb_youtube", "JPEG", (1280, 720)),
        ("transparent_png", "PNG", (640, 427)),
        ("print_300dpi", "TIFF", (640, 427)),
    ],
)
def test_export_presets(
    engine: Engine, photo: str, preset: str, fmt: str, size: tuple[int, int]
) -> None:
    result, rep = run1(engine, photo, ("export", {"preset": preset}))
    im = image(engine, rep)
    assert (im.format, im.size) == (fmt, size)
    assert im.info.get("icc_profile")  # sRGB embedded
    tags = im.getexif()
    assert not {0x0132, 0x0131, 0x013B, 0x8769, 0x8825} & set(tags)  # no dates/software/GPS
    assert result.passed, result.checks


def test_export_budget_search(engine: Engine, photo: str) -> None:
    _, loose = run1(engine, photo, ("export", {"preset": "web_jpeg"}))
    budget = loose.meta["bytes"] - 2000
    _, tight = run1(
        engine, photo, ("export", {"preset": "web_jpeg", "overrides": {"max_bytes": budget}})
    )
    assert 60 <= tight.meta["quality"] < 85
    assert tight.meta["bytes"] <= budget


def test_export_override_errors(engine: Engine, photo: str) -> None:
    assert errors(
        engine,
        {"in": photo, "ops": [["export", {"preset": "web_jpeg", "overrides": {"qualty": 50}}]]},
    ) == [
        "ops[0].args.overrides.qualty: export has no setting 'qualty' → use one of "
        "alpha|background|checks|compress_level|compression|dpi|format|icc|lossless|max_bytes|"
        "method|min_quality|optimize|progressive|quality|safe_area|size|speed|subsampling "
        "(did you mean quality?)"
    ]
    assert errors(
        engine,
        {"in": photo, "ops": [["export", {"preset": "web_jpeg", "overrides": {"alpha": True}}]]},
    ) == ["ops[0].args.overrides: jpeg cannot keep alpha; set alpha: false or use png/webp/avif"]


def test_export_expands_preset_into_canonical_args(engine: Engine, photo: str) -> None:
    step = engine.validate({"in": photo, "ops": [["export", {"preset": "og_image"}]]}).steps[0]
    assert step.args["preset_version"] == 1
    assert step.args["settings"]["size"] == {
        "width": 1200,
        "height": 630,
        "mode": "fill",
        "focus": "attention",
    }


# -- previews --------------------------------------------------------------------------


def test_previews(engine: Engine, photo: str, logo: str) -> None:
    thumb = engine.preview("thumb", [photo], {"long_edge": 256}).outputs["preview"]
    assert max(thumb.meta["width"], thumb.meta["height"]) == 256
    crop = engine.preview("crop", [photo], {"region": [0.5, 0.2, 0.4, 0.5]}).outputs["preview"]
    assert (crop.meta["width"], crop.meta["height"]) == (256, 214)
    compare = engine.preview("compare", [photo, logo]).outputs["preview"]
    assert compare.meta["height"] == 384 + 28
    sheet = engine.preview("sheet", [photo, photo, logo]).outputs["preview"]
    assert "tokens" in sheet.summary
