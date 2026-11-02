"""`export`: the only place final encoding happens, for every media type (docs/03 §3.4).

One op, one meaning: encode the input with a preset. The preset's `kind` (export.image,
export.video, export.audio) says how, and must match the input's media type. Settings come from
a versioned preset (principle P3), optionally with explicit `overrides`; the expanded settings
and the preset version are written into the canonical args, so they are part of the cache key
and of the stored spec.

Images are encoded here in one step. Video and audio are encoded by the ffmpeg graph runner
(`av/`), which fuses this step with the steps before it so the output is encoded once.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import Field, ValidationError

from atelier import presets
from atelier.av.audio_export import KIND as AUDIO_KIND
from atelier.av.audio_export import AudioExportSettings, build_audio_export
from atelier.av.export import KIND as VIDEO_KIND
from atelier.av.export import VideoExportSettings, av_summary, build_video_export
from atelier.docs import export as docs_export
from atelier.media import MediaType
from atelier.ops.image.export import ImageExportSettings, export_image, image_export_summary
from atelier.ops.registry import (
    INTERNAL,
    SAME,
    AnyOf,
    ArgError,
    Artifact,
    Determinism,
    OpContext,
    OpError,
    Params,
    op,
)
from atelier.spec.errors import closest

# preset kind → (settings model, the media type it encodes)
EXPORT_KINDS: dict[str, tuple[type[Params], MediaType]] = {
    "export.image": (ImageExportSettings, MediaType.IMAGE),
    VIDEO_KIND: (VideoExportSettings, MediaType.VIDEO),
    AUDIO_KIND: (AudioExportSettings, MediaType.AUDIO),
    docs_export.PDF_KIND: (docs_export.PdfExportSettings, MediaType.PDF),
    docs_export.DOCX_KIND: (docs_export.OfficeExportSettings, MediaType.DOCX),
    docs_export.XLSX_KIND: (docs_export.OfficeExportSettings, MediaType.XLSX),
    docs_export.PPTX_KIND: (docs_export.OfficeExportSettings, MediaType.PPTX),
}
_DOCUMENTS = frozenset({MediaType.PDF, MediaType.DOCX, MediaType.XLSX, MediaType.PPTX})
_EXPORTABLE = frozenset({MediaType.IMAGE, MediaType.VIDEO, MediaType.AUDIO}) | _DOCUMENTS


class ExportParams(Params):
    preset: str = Field(description="export preset id")
    overrides: dict[str, Any] = Field(default_factory=dict, description="replace preset keys")
    preset_version: int | None = Field(None, json_schema_extra=INTERNAL)
    settings: dict[str, Any] | None = Field(None, json_schema_extra=INTERNAL)


def _export_presets() -> dict[str, presets.Preset]:
    return {k: p for k, p in presets.all_presets().items() if p.kind in EXPORT_KINDS}


def _expand(args: dict[str, Any]) -> dict[str, Any]:
    available = _export_presets()
    preset = available.get(args["preset"])
    if preset is None:
        names = sorted(available)
        raise ArgError(
            "preset",
            "enum_mismatch",
            f"{args['preset']!r} invalid",
            names,
            closest(args["preset"], names),
        )
    model = EXPORT_KINDS[preset.kind][0]
    fields = sorted(model.model_fields)
    for key in args["overrides"]:
        if key not in fields:
            raise ArgError(
                f"overrides.{key}",
                "unknown_arg",
                f"export has no setting {key!r}",
                fields,
                closest(key, fields),
            )
    try:
        settings = model.model_validate({**preset.settings, **args["overrides"]})
    except ValidationError as e:
        first = e.errors(include_url=False)[0]
        where = ".".join(str(x) for x in first["loc"])
        msg = first["msg"].removeprefix("Value error, ")
        raise ArgError(
            "overrides", "invalid_arg", f"{where + ': ' if where else ''}{msg}"
        ) from None
    return {**args, "preset_version": preset.version, "settings": settings.model_dump(mode="json")}


def _check_media(args: dict[str, Any], input_types: Sequence[MediaType]) -> ArgError | None:
    """A preset only makes sense for the media it encodes: say which presets do fit."""
    preset = presets.all_presets()[args["preset"]]
    encodes = EXPORT_KINDS[preset.kind][1]
    actual = input_types[0]
    if encodes is actual:
        return None
    fitting = sorted(
        pid for pid, p in _export_presets().items() if EXPORT_KINDS[p.kind][1] is actual
    )
    fix = (
        " (take the sound out first with audio.extract)"
        if (encodes is MediaType.AUDIO and actual is MediaType.VIDEO)
        else ""
    )
    return ArgError(
        "preset",
        "preset_media",
        f"{preset.id!r} encodes {encodes.value}, but the input is {actual.value}{fix}",
        fitting,
        closest(preset.id, fitting),
    )


def _summary(result: Artifact) -> str:
    if result.media is MediaType.IMAGE:
        return image_export_summary(result)
    if result.media in _DOCUMENTS:
        from atelier.docs.doc_ops import doc_summary
        from atelier.docs.pdf_ops import pdf_summary

        text = pdf_summary(result) if result.media is MediaType.PDF else doc_summary(result)
        return f"{text} · {result.meta['export']['preset']} v{result.meta['export']['version']}"
    return av_summary(result)


def _graph(g: Any, p: ExportParams) -> None:
    """Video and audio export: add the final filters and encoder settings to the fused graph."""
    assert p.settings is not None and p.preset_version is not None
    if presets.all_presets()[p.preset].kind == AUDIO_KIND:
        build_audio_export(g, p.settings, p.preset, p.preset_version)
    else:
        build_video_export(g, p.settings, p.preset, p.preset_version)


@op(
    name="export",
    version="1.0.0",
    inputs=[AnyOf(_EXPORTABLE)],
    output=SAME,
    params=ExportParams,
    description="final output with a preset (image, video, audio or document)",
    determinism=Determinism.PERCEPTUAL,
    summary=_summary,
    canonicalize=_expand,
    media_check=_check_media,
    resources_by_media={
        MediaType.IMAGE: ("pillow", "icc"),
        MediaType.VIDEO: ("ffmpeg", "ffprobe"),
        MediaType.AUDIO: ("ffmpeg", "ffprobe"),
        MediaType.PDF: ("pdf", "pillow", "icc"),
        MediaType.DOCX: ("office",),
        MediaType.XLSX: ("office",),
        MediaType.PPTX: ("office",),
    },
    graph=_graph,
    graph_media=[MediaType.VIDEO, MediaType.AUDIO],
)
def export(ctx: OpContext, inputs: Sequence[Artifact], p: ExportParams) -> Artifact:
    """Encode the final output with an export preset. Which presets fit depends on the input:
    image presets encode images, video presets encode video, audio presets encode audio.
    `overrides` replace individual preset settings; the merged settings and the preset version
    become part of the spec, so results are reproducible and cached exactly.
    """
    assert p.settings is not None and p.preset_version is not None
    media = inputs[0].media
    if media is MediaType.IMAGE:
        return export_image(
            ctx, inputs, p.preset, p.preset_version, ImageExportSettings.model_validate(p.settings)
        )
    if media in _DOCUMENTS:
        return docs_export.export_document(ctx, inputs[0], p.preset, p.preset_version, p.settings)
    raise OpError(f"exporting {media.value} runs as part of an ffmpeg graph")
