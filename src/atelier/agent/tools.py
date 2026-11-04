"""The five tools (docs/04 §4.2, plus make_voiceover) and the dispatcher that runs them.

Definitions are sorted by name and built from constants only, so the serialized tool list is
byte-stable across processes and releases (it is the first thing in the cached prefix).

Strictness is per tool. Three tools are `strict: true` with `additionalProperties: false`.
`run_pipeline` is not: a spec's `args` are op-specific and free-form, which strict schemas
cannot express. The engine's validator is that tool's contract instead, and it reports every
error with its path (docs/03 §3.2).

Results are plain, short text meant to be read once. Anything the model should correct
(validation errors, failed steps, failed checks) comes back with `is_error: true`.
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atelier.agent.budget import Budget
from atelier.agent.voice import DEFAULT_VOICE, MAX_CHARS, VOICES, VoiceError, VoiceService, chunks
from atelier.av.binaries import find
from atelier.engine.engine import Engine, format_plan, format_result
from atelier.engine.runner import JobResult, StepFailed
from atelier.media import HandleError, MediaType
from atelier.ops.catalogue import describe_name
from atelier.presets import all_presets
from atelier.recipes import all_recipes
from atelier.spec.errors import SpecInvalid, closest

MAX_ASSETS_PER_CALL = 20
MAX_NAMES_PER_CALL = 12
MAX_VOICEOVERS = 4  # per job

PREVIEW_KINDS = {"thumb": "thumb", "crop": "crop", "compare": "compare", "contact_sheet": "sheet"}
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _tool(name: str, description: str, schema: dict[str, Any], *, strict: bool) -> dict[str, Any]:
    schema = {"type": "object", **schema, "additionalProperties": False}
    tool: dict[str, Any] = {"name": name, "description": description, "input_schema": schema}
    if strict:
        tool["strict"] = True
    return tool


_STRING_LIST = {"type": "array", "items": {"type": "string"}}

TOOLS: list[dict[str, Any]] = sorted(
    [
        _tool(
            "describe_op",
            "Full parameter docs for ops, presets or recipes, by name. Use it before relying on "
            "a parameter you are unsure of.",
            {"properties": {"names": _STRING_LIST}, "required": ["names"]},
            strict=True,
        ),
        _tool(
            "inspect_media",
            "One compact line per asset (type, size, format, color profile). detail=full adds "
            "the hash and more. Handles look like asset:5891b5b5c3d2.",
            {
                "properties": {
                    "assets": _STRING_LIST,
                    "detail": {"type": "string", "enum": ["brief", "full"]},
                },
                "required": ["assets"],
            },
            strict=True,
        ),
        _tool(
            "make_voiceover",
            "Speak a script aloud with a natural, human-sounding AI voice and get it back as a "
            "new audio asset (handle and exact duration). Write the narration yourself when the "
            "user asks for a voiceover without giving the words: plain, conversational "
            "sentences, about 2.5 words per second of video. Then put it on the video with "
            "video.soundtrack (mode replace, loop false) or under existing sound with audio.mix. "
            f"voice: one of {', '.join(f'{k} ({v})' for k, v in VOICES.items())}.",
            {
                "properties": {
                    "text": {"type": "string", "description": "the exact words to speak"},
                    "voice": {"type": "string", "enum": sorted(VOICES)},
                },
                "required": ["text", "voice"],
            },
            strict=True,
        ),
        _tool(
            "preview",
            "A small image of an asset, for visual judgment only; the number of previews is "
            "limited, so try the checks first. kind: thumb (one asset; for a video, one frame "
            "at `time` seconds, default the middle), crop (one image plus region [x, y, w, h] "
            "as fractions), compare (two images side by side), contact_sheet (several images in "
            "a grid; for a video, timestamped frames evenly spaced through it). For a PDF or "
            "document, thumb is one page (`page`, default 1) and contact_sheet the first pages.",
            {
                "properties": {
                    "assets": _STRING_LIST,
                    "kind": {"type": "string", "enum": sorted(PREVIEW_KINDS)},
                    "region": {"type": "array", "items": {"type": "number"}},
                    "time": {"type": "number"},
                    "page": {"type": "integer"},
                },
                "required": ["assets", "kind"],
            },
            strict=True,
        ),
        _tool(
            "run_pipeline",
            "Validate and run a spec (recipe, linear or full form). Returns every validation "
            "error, or one summary per output with check results and fix hints. dry_run=true "
            "validates and plans without running anything.",
            {
                "properties": {
                    "spec": {"type": "object", "description": "the pipeline spec"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["spec"],
            },
            strict=False,
        ),
    ],
    key=lambda t: t["name"],
)


@dataclass
class ToolOutcome:
    content: str | list[dict[str, Any]]
    is_error: bool = False

    def block(self, tool_use_id: str) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            block["is_error"] = True
        return block


@dataclass
class RunAttempt:
    spec: Any
    result: JobResult | None  # None when the spec was invalid or a step failed
    passed: bool


@dataclass
class Toolbox:
    engine: Engine
    budget: Budget
    voice: VoiceService | None = None
    attempts: list[RunAttempt] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)  # handles of assets the tools made

    # -- results the loop reads back ------------------------------------------------------

    def best(self) -> RunAttempt | None:
        """The last run whose checks all passed, else the last run that completed at all."""
        ran = [a for a in self.attempts if a.result is not None]
        pool = [a for a in ran if a.passed] or ran
        return pool[-1] if pool else None

    @property
    def first_pass(self) -> bool | None:
        return self.attempts[0].passed if self.attempts else None

    # -- dispatch -------------------------------------------------------------------------

    def dispatch(self, name: str, tool_input: Any) -> ToolOutcome:
        self.calls.append(name)
        if not isinstance(tool_input, dict):
            return ToolOutcome(f"{name}: input must be an object", is_error=True)
        handler = {
            "describe_op": self._describe_op,
            "inspect_media": self._inspect_media,
            "make_voiceover": self._make_voiceover,
            "preview": self._preview,
            "run_pipeline": self._run_pipeline,
        }.get(name)
        if handler is None:
            known = ", ".join(t["name"] for t in TOOLS)
            return ToolOutcome(f"unknown tool {name!r}; tools: {known}", is_error=True)
        return handler(tool_input)

    def _describe_op(self, args: dict[str, Any]) -> ToolOutcome:
        names = _strings(args.get("names"))
        if not names:
            return ToolOutcome("describe_op: names must list at least one name", is_error=True)
        options = [
            *(d.name for d in self.engine.registry.ops()),
            *all_presets(),
            *all_recipes(),
        ]
        parts, error = [], False
        for name in names[:MAX_NAMES_PER_CALL]:
            text = describe_name(self.engine.registry, name)
            if text is None:
                error = True
                hint = closest(name, options)
                text = f"unknown {name!r}" + (f" (did you mean {hint}?)" if hint else "")
            parts.append(text)
        if len(names) > MAX_NAMES_PER_CALL:
            parts.append(
                f"… {len(names) - MAX_NAMES_PER_CALL} more names ignored (max 12 per call)"
            )
        return ToolOutcome("\n\n".join(parts), is_error=error)

    def _inspect_media(self, args: dict[str, Any]) -> ToolOutcome:
        handles = _strings(args.get("assets"))
        if not handles:
            return ToolOutcome("inspect_media: assets must list at least one handle", is_error=True)
        full = args.get("detail") == "full"
        lines, error = [], False
        for handle in handles[:MAX_ASSETS_PER_CALL]:
            try:
                lines.append(self.engine.inspect(self.engine.store.resolve(handle), full=full))
            except (HandleError, ValueError) as e:
                error = True
                lines.append(f"{handle}: {e}")
        if len(handles) > MAX_ASSETS_PER_CALL:
            lines.append(f"… {len(handles) - MAX_ASSETS_PER_CALL} more assets ignored (max 20)")
        return ToolOutcome("\n".join(lines), is_error=error)

    def _make_voiceover(self, args: dict[str, Any]) -> ToolOutcome:
        text = " ".join(str(args.get("text") or "").split())
        voice = args.get("voice") or DEFAULT_VOICE
        if not text:
            return ToolOutcome("make_voiceover: text must not be empty", is_error=True)
        if len(text) > MAX_CHARS:
            return ToolOutcome(
                f"make_voiceover: the script is {len(text)} characters; keep it under "
                f"{MAX_CHARS} (about 4 minutes of speech)",
                is_error=True,
            )
        if voice not in VOICES:
            return ToolOutcome(
                f"make_voiceover: unknown voice {voice!r}; use one of {', '.join(VOICES)}",
                is_error=True,
            )
        if self.voice is None:
            return ToolOutcome(
                "make_voiceover: voiceovers are not available on this server (no voice "
                "service configured); tell the user instead",
                is_error=True,
            )
        if len(self.generated) >= MAX_VOICEOVERS:
            return ToolOutcome(
                f"make_voiceover: at most {MAX_VOICEOVERS} voiceovers per request; reuse one",
                is_error=True,
            )
        try:
            info = self._speak(text, voice)
        except (VoiceError, subprocess.CalledProcessError, OSError) as e:
            return ToolOutcome(f"make_voiceover: {e}", is_error=True)
        self.generated.append(info.handle)
        duration = self.engine.probe(info).get("duration") or 0.0
        return ToolOutcome(
            f"{info.handle} · audio · voiceover {duration:.2f}s · voice {voice} · "
            f"{len(text.split())} words"
        )

    def _speak(self, text: str, voice: str) -> Any:
        """One MP3 from the whole script: each chunk spoken, then joined and re-encoded once
        so the file has a single clean header and an exact duration."""
        assert self.voice is not None
        work = Path(tempfile.mkdtemp(prefix="voice-", dir=self.engine.store.tmp))
        parts = []
        for i, chunk in enumerate(chunks(text)):
            part = work / f"part{i:03d}.mp3"
            part.write_bytes(self.voice.speak(chunk, voice))
            parts.append(part)
        listing = work / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        out = work / "voiceover.mp3"
        subprocess.run(
            [
                find("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-map_metadata", "-1",
                str(out),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )  # fmt: skip
        info = self.engine.store.put_file(out, media=MediaType.AUDIO, ext=".mp3", move=True)
        for p in [*parts, listing]:
            p.unlink(missing_ok=True)
        work.rmdir()
        return info

    def _preview(self, args: dict[str, Any]) -> ToolOutcome:
        kind, handles = args.get("kind"), _strings(args.get("assets"))
        if kind not in PREVIEW_KINDS or not handles:
            return ToolOutcome(
                f"preview: needs assets and a kind of {'|'.join(sorted(PREVIEW_KINDS))}",
                is_error=True,
            )
        if not self.budget.image_allowed():
            return ToolOutcome(
                f"preview: image budget used ({self.budget.images}/{self.budget.limits.max_images})"
                "; decide from the checks",
                is_error=True,
            )
        extra: dict[str, Any] = {}
        if kind == "crop":
            region = args.get("region")
            if not (isinstance(region, list) and len(region) == 4):
                return ToolOutcome("preview: crop needs region [x, y, w, h]", is_error=True)
            extra["region"] = region
        if isinstance(args.get("time"), (int, float)) and not isinstance(args.get("time"), bool):
            extra["time"] = float(args["time"])
        page = args.get("page")
        if isinstance(page, int) and not isinstance(page, bool):
            extra["page"] = page
        try:
            infos = [self.engine.store.resolve(h) for h in handles]
            result = self.engine.preview(PREVIEW_KINDS[kind], [i.handle for i in infos], extra)
        except (HandleError, ValueError, SpecInvalid, StepFailed) as e:
            return ToolOutcome(f"preview: {e}", is_error=True)
        rep = result.outputs["preview"]
        media_type = MEDIA_TYPES.get(rep.ext)
        if media_type is None:
            return ToolOutcome(f"preview: unsupported preview format {rep.ext!r}", is_error=True)
        self.budget.images += 1
        data = base64.standard_b64encode(self.engine.store.path(rep.hash).read_bytes()).decode()
        return ToolOutcome(
            [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                },
                {"type": "text", "text": rep.summary},
            ]
        )

    def _run_pipeline(self, args: dict[str, Any]) -> ToolOutcome:
        spec = args.get("spec")
        if isinstance(spec, str):  # tolerated: the schema is not strict for this tool
            try:
                spec = json.loads(spec)
            except ValueError:
                return ToolOutcome("run_pipeline: spec must be a JSON object", is_error=True)
        if not isinstance(spec, dict):
            return ToolOutcome("run_pipeline: spec must be a JSON object", is_error=True)
        dry_run = args.get("dry_run") is True
        try:
            if dry_run:
                return ToolOutcome(format_plan(self.engine.plan(spec)))
            result = self.engine.run(spec)
        except SpecInvalid as e:
            self._attempt(spec, None, dry_run)
            n = len(e.errors)
            return ToolOutcome(f"invalid spec · {n} error{'s' * (n != 1)}\n{e}", is_error=True)
        except StepFailed as e:
            self._attempt(spec, None, dry_run)
            return ToolOutcome(f"failed · {e}", is_error=True)
        self._attempt(spec, result, dry_run)
        return ToolOutcome(format_result(result), is_error=not result.passed)

    def _attempt(self, spec: Any, result: JobResult | None, dry_run: bool) -> None:
        if not dry_run:
            self.attempts.append(RunAttempt(spec, result, result is not None and result.passed))


def _strings(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
