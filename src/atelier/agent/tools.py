"""The tools (docs/04 §4.2, plus make_voiceover, find_images, make_image) and their dispatcher.

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
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atelier.agent.animation import STYLE as ANIMATION_STYLE
from atelier.agent.animation import AnimationError, AnimationService
from atelier.agent.budget import Budget
from atelier.agent.cast import Cast, CastMember, design_prompt
from atelier.agent.music import MusicError, MusicService
from atelier.agent.pictures import PictureError, PictureService
from atelier.agent.voice import (
    ACCENTS,
    BUILT_IN,
    DEFAULT_VOICE,
    MAX_CHARS,
    MY_VOICE,
    VOICES,
    VoiceError,
    VoiceService,
    caption_cues,
    sentences,
    srt,
    word_times,
)
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
MAX_FOUND = 12  # pictures found per job
MAX_CHARACTERS = 8  # cast members designed per request
MAX_ANIMATION_SECONDS = 90  # animated seconds per request (billed per second)
MAX_MADE = 12  # pictures generated per job (a 30 s reel needs 8-15 shots)
MAX_MUSIC = 2  # tracks composed per job

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
            "find_images",
            "Search the web for real photos (openly licensed, large, credited automatically) and "
            "get them as image assets. Use it for real things: places, landmarks, products, "
            'tools, events, real-world scenes. Short concrete queries work best ("laptop '
            'coding", "Lagos skyline"). Preview what comes back: if it looks dated or '
            "off-topic, search again or use make_image instead. shape: landscape, portrait or "
            "square (match the video).",
            {
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer", "description": "1 to 6"},
                    "shape": {"type": "string", "enum": ["landscape", "portrait", "square"]},
                },
                "required": ["query", "count", "shape"],
            },
            strict=True,
        ),
        _tool(
            "make_character",
            "Design a member of an animated series' cast: a character (a person, animal, robot…) "
            "or a place (a setting the scenes happen in). Makes its design picture in one "
            "animation style and saves it to the user's cast, so every scene and every later "
            "part of the series reuses the same look. look: a full visual description (age, "
            "body, face, hair, clothes and colors, or the place's features). voice: how the "
            "character sounds, for their lines (age, gender, accent, energy); empty for a place. "
            "The same name again redesigns it.",
            {
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["character", "place"]},
                    "look": {"type": "string"},
                    "voice": {"type": "string"},
                },
                "required": ["name", "kind", "look", "voice"],
            },
            strict=True,
        ),
        _tool(
            "animate",
            "Animate one scene (4-15 s) with members of the cast, by name: their design pictures "
            "keep them looking the same. scene: what happens, who does what, the camera and the "
            "mood, in the present tense. Optional line: the words one character (speaker) says; "
            "it is spoken in that character's voice and their mouth moves with it (empty "
            "speaker and line for none). Returns a video clip with the line as its sound "
            f"(silent otherwise). At most {MAX_ANIMATION_SECONDS} animated seconds per request.",
            {
                "properties": {
                    "scene": {"type": "string"},
                    "cast": _STRING_LIST,
                    "seconds": {"type": "integer"},
                    "shape": {"type": "string", "enum": ["landscape", "portrait", "square"]},
                    "speaker": {"type": "string"},
                    "line": {"type": "string"},
                },
                "required": ["scene", "cast", "seconds", "shape", "speaker", "line"],
            },
            strict=True,
        ),
        _tool(
            "make_image",
            "Generate one new picture with an AI image model and get it as an image asset; it "
            "always looks modern and photographic. Use it for illustrative or concept shots, "
            "people who need not be real, backgrounds for title slides, or when found photos "
            "look old. Describe the scene in detail (subject, setting, mood, light). Never ask "
            "for words or code in the picture: add text afterwards with image.text or "
            "video.text. shape: landscape, portrait or square (match the video).",
            {
                "properties": {
                    "prompt": {"type": "string"},
                    "shape": {"type": "string", "enum": ["landscape", "portrait", "square"]},
                },
                "required": ["prompt", "shape"],
            },
            strict=True,
        ),
        _tool(
            "make_music",
            "Compose an original instrumental track (royalty-free, made to length) and get it "
            "as an audio asset with its exact duration and beat length. style: genre, mood, "
            'instruments and energy, e.g. "warm upbeat lofi pop, soft piano, light drums" or '
            '"cinematic inspiring build, strings and piano". seconds: the video\'s length (10 '
            "to 180). bpm: 70-90 calm, 95-115 upbeat, 120-130 energetic. Cut on the beat: make "
            "shot lengths whole numbers of beats (slideshow durations).",
            {
                "properties": {
                    "style": {"type": "string"},
                    "seconds": {"type": "integer"},
                    "bpm": {"type": "integer"},
                },
                "required": ["style", "seconds", "bpm"],
            },
            strict=True,
        ),
        _tool(
            "make_voiceover",
            "Speak a script aloud with a natural, human-sounding AI voice and get it back as a "
            "new audio asset (handle and exact duration), plus word-timed captions (an SRT text "
            "asset) to burn in with video.captions. Write the narration yourself when the "
            "user asks for a voiceover without giving the words: plain, conversational "
            "sentences, about 2.5 words per second of video. Then put it on the video with "
            "video.soundtrack (mode replace, loop false) or under existing sound with audio.mix. "
            "voice: <accent>_<woman|man>, accents: " + ", ".join(ACCENTS) + "; my_voice is "
            "the user's own recorded voice. Use the voice the user chose; else match their "
            "accent or audience; default american_woman.",
            {
                "properties": {
                    "text": {"type": "string", "description": "the exact words to speak"},
                    "voice": {"type": "string", "enum": [*sorted(VOICES), MY_VOICE]},
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
    pictures: PictureService | None = None
    music: MusicService | None = None
    animator: AnimationService | None = None
    cast: Cast | None = None
    attempts: list[RunAttempt] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    # assets the tools made or fetched: (handle, filename), and credits for found photos
    generated: list[tuple[str, str]] = field(default_factory=list)
    credits: list[str] = field(default_factory=list)
    found: int = 0
    made: int = 0
    designed: int = 0
    animated: int = 0  # seconds
    new_cast: dict[str, CastMember] = field(default_factory=dict)

    # -- results the loop reads back ------------------------------------------------------

    def best(self) -> RunAttempt | None:
        """The last run whose checks all passed, else the last run that completed at all."""
        ran = [a for a in self.attempts if a.result is not None]
        pool = [a for a in ran if a.passed] or ran
        return pool[-1] if pool else None

    def finished_edit(self) -> RunAttempt | None:
        """When the turns run out: the last run, if it passed and used every voiceover and
        music track made for this request, so it is the full edit and not a test (live: a
        tutorial's complete render was thrown away because the reply turn never came)."""
        if not self.attempts or not self.attempts[-1].passed:
            return None
        last = self.attempts[-1]
        used = json.dumps(last.spec, default=str)
        sounds = [h for h, name in self.generated if name.endswith((".mp3", ".wav"))]
        complete = sounds and all(h.split(":")[-1][:12] in used for h in sounds)
        return last if complete else None

    @property
    def first_pass(self) -> bool | None:
        return self.attempts[0].passed if self.attempts else None

    # -- dispatch -------------------------------------------------------------------------

    def dispatch(self, name: str, tool_input: Any) -> ToolOutcome:
        self.calls.append(name)
        if not isinstance(tool_input, dict):
            return ToolOutcome(f"{name}: input must be an object", is_error=True)
        handler = {
            "animate": self._animate,
            "describe_op": self._describe_op,
            "find_images": self._find_images,
            "inspect_media": self._inspect_media,
            "make_character": self._make_character,
            "make_image": self._make_image,
            "make_music": self._make_music,
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

    def _find_images(self, args: dict[str, Any]) -> ToolOutcome:
        query = " ".join(str(args.get("query") or "").split())[:120]
        count = args.get("count") if isinstance(args.get("count"), int) else 3
        count = max(1, min(6, count, MAX_FOUND - self.found))
        shape = (
            args.get("shape") if args.get("shape") in ("landscape", "portrait", "square") else ""
        )
        if not query:
            return ToolOutcome("find_images: query must not be empty", is_error=True)
        if self.pictures is None:
            return ToolOutcome("find_images: not available on this server", is_error=True)
        if self.found >= MAX_FOUND:
            return ToolOutcome(
                f"find_images: at most {MAX_FOUND} pictures per request", is_error=True
            )
        try:
            results = self.pictures.search(query, count, shape)
        except PictureError as e:
            return ToolOutcome(f"find_images: {e}", is_error=True)
        if not results:
            return ToolOutcome(
                f"find_images: nothing usable for {query!r}; try a simpler query or make_image",
                is_error=True,
            )
        lines = []
        for f in results:
            try:
                info = self.engine.store.put_bytes(self.pictures.download(f))
            except (PictureError, OSError) as e:
                lines.append(f"(skipped one: {e})")
                continue
            if info.media is not MediaType.IMAGE:
                continue
            self.found += 1
            self.credits.append(f.credit)
            self.generated.append((info.handle, _slug(f.title, "found") + info.ext))
            lines.append(f"{info.handle} · image {f.width}x{f.height} · {f.credit}")
        if not any(line.startswith("asset:") for line in lines):
            return ToolOutcome(
                "find_images: none of the results could be downloaded", is_error=True
            )
        return ToolOutcome("\n".join(lines))

    def _make_image(self, args: dict[str, Any]) -> ToolOutcome:
        prompt = " ".join(str(args.get("prompt") or "").split())[:1500]
        shape = (
            args.get("shape")
            if args.get("shape") in ("landscape", "portrait", "square")
            else "square"
        )
        if not prompt:
            return ToolOutcome("make_image: prompt must not be empty", is_error=True)
        if self.pictures is None:
            return ToolOutcome("make_image: not available on this server", is_error=True)
        if self.made >= MAX_MADE:
            return ToolOutcome(
                f"make_image: at most {MAX_MADE} pictures per request", is_error=True
            )
        try:
            data = self.pictures.generate(prompt, shape)
            info = self.engine.store.put_bytes(data)
        except (PictureError, OSError) as e:
            return ToolOutcome(f"make_image: {e}", is_error=True)
        if info.media is not MediaType.IMAGE:
            return ToolOutcome("make_image: the service did not return a picture", is_error=True)
        self.made += 1
        self.generated.append((info.handle, _slug(prompt, "made") + info.ext))
        w, h = self.engine.probe(info).get("display") or (0, 0)
        return ToolOutcome(f"{info.handle} · image {w}x{h} · generated")

    # -- animation ---------------------------------------------------------------------------

    def _cast_by_name(self) -> dict[str, CastMember]:
        members = {m.name.lower(): m for m in (self.cast.members() if self.cast else [])}
        return members | {k.lower(): m for k, m in self.new_cast.items()}

    def _make_character(self, args: dict[str, Any]) -> ToolOutcome:
        name = " ".join(str(args.get("name") or "").split())[:60]
        kind = "place" if args.get("kind") == "place" else "character"
        look = " ".join(str(args.get("look") or "").split())[:1200]
        voice = " ".join(str(args.get("voice") or "").split())[:400] if kind == "character" else ""
        if not name or not look:
            return ToolOutcome("make_character: name and look are required", is_error=True)
        if self.pictures is None or self.cast is None:
            return ToolOutcome("make_character: not available on this server", is_error=True)
        if self.designed >= MAX_CHARACTERS:
            return ToolOutcome(
                f"make_character: at most {MAX_CHARACTERS} designs per request", is_error=True
            )
        shape = "landscape" if kind == "place" else "square"
        try:
            data = self.pictures.generate(design_prompt(kind, look), shape, style=ANIMATION_STYLE)
            info = self.engine.store.put_bytes(data)
        except (PictureError, OSError) as e:
            return ToolOutcome(f"make_character: {e}", is_error=True)
        if info.media is not MediaType.IMAGE:
            return ToolOutcome("make_character: no picture came back", is_error=True)
        member = CastMember(name, kind, look, voice, info.handle)  # type: ignore[arg-type]
        filename = _slug(name, "character") + info.ext
        self.cast.save(member, filename)
        self.new_cast[name] = member
        self.designed += 1
        self.generated.append((info.handle, filename))
        return ToolOutcome(f"{info.handle} · {kind} {name!r} designed and saved to the cast")

    def _animate(self, args: dict[str, Any]) -> ToolOutcome:
        scene = " ".join(str(args.get("scene") or "").split())[:3000]
        names = _strings(args.get("cast"))
        speaker = " ".join(str(args.get("speaker") or "").split())
        line = " ".join(str(args.get("line") or "").split())[:400]
        shape = str(args.get("shape")) if args.get("shape") in ANIMATION_SHAPES else "landscape"
        try:
            seconds = int(args.get("seconds") or 6)
        except (TypeError, ValueError):
            seconds = 6
        if not scene:
            return ToolOutcome("animate: describe the scene", is_error=True)
        if self.animator is None:
            return ToolOutcome("animate: animation is not available on this server", is_error=True)
        if not 4 <= seconds <= 15:
            return ToolOutcome("animate: seconds must be 4 to 15 (one scene)", is_error=True)
        if self.animated + seconds > MAX_ANIMATION_SECONDS:
            left = MAX_ANIMATION_SECONDS - self.animated
            return ToolOutcome(
                f"animate: {self.animated}s animated already; {left}s left in this request. "
                "Finish the episode with the scenes you have, or make the rest in a follow-up",
                is_error=True,
            )
        known = self._cast_by_name()
        missing = [n for n in names if n.lower() not in known]
        if missing:
            have = ", ".join(m.name for m in known.values()) or "none yet"
            return ToolOutcome(
                f"animate: not in the cast: {', '.join(missing)} (cast: {have}); "
                "make_character first",
                is_error=True,
            )
        members = [known[n.lower()] for n in names]
        if bool(speaker) != bool(line):
            return ToolOutcome("animate: give both speaker and line, or neither", is_error=True)
        talker = known.get(speaker.lower()) if speaker else None
        if speaker and (talker is None or talker.kind != "character"):
            return ToolOutcome(
                f"animate: {speaker!r} is not a character in the cast", is_error=True
            )
        work = Path(tempfile.mkdtemp(prefix="scene-", dir=self.engine.store.tmp))
        try:
            pictures = [
                self.engine.store.path(self.engine.store.resolve(m.handle).hash).read_bytes()
                for m in members
            ]
            spoken: Path | None = None
            if talker is not None:
                speak_as = getattr(self.voice, "speak_as", None)
                if speak_as is None:
                    return ToolOutcome("animate: voices are not available here", is_error=True)
                raw = work / "line-raw.mp3"
                raw.write_bytes(speak_as(line, talker.voice or "a natural, friendly voice"))
                spoken = work / "line.mp3"
                _ffmpeg(
                    ["-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                     "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", str(spoken)]
                )  # fmt: skip
                length = _seconds(spoken)
                if length > seconds - 0.3:
                    return ToolOutcome(
                        f"animate: the line takes {length:.1f}s; make the scene at least "
                        f"{min(15, int(length) + 1)}s or the line shorter",
                        is_error=True,
                    )
            looks = "; ".join(f"{m.name}: {m.look[:220]}" for m in members)
            prompt = f"{scene} Cast, as in the reference pictures: {looks}."
            if talker is not None:
                prompt += f' {talker.name} says, mouth moving in sync with the audio: "{line}"'
            clip = work / "clip.mp4"
            clip.write_bytes(
                self.animator.animate(
                    prompt, pictures, seconds, shape,
                    line=spoken.read_bytes() if spoken else None,
                )
            )  # fmt: skip
            out = work / "scene.mp4"
            sound = ["-i", str(spoken)] if spoken else [
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            ]  # fmt: skip
            _ffmpeg(["-i", str(clip), *sound, "-map", "0:v:0", "-map", "1:a:0",
                     "-c:v", "copy", "-af", "apad", "-c:a", "aac", "-b:a", "160k",
                     "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart",
                     str(out)])  # fmt: skip
            info = self.engine.store.put_file(out, media=MediaType.VIDEO, ext=".mp4", move=True)
        except (AnimationError, VoiceError, OSError, subprocess.CalledProcessError) as e:
            return ToolOutcome(f"animate: {e}", is_error=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.animated += seconds
        n = sum(1 for _, f in self.generated if f.startswith("scene-")) + 1
        self.generated.append((info.handle, f"scene-{n}.mp4"))
        meta = self.engine.probe(info)
        w, h = meta.get("display") or (0, 0)
        said = f" · {talker.name} says {line!r}" if talker else " · no line (silent)"
        return ToolOutcome(
            f"{info.handle} · video scene {float(meta.get('duration') or 0):.2f}s {w}x{h}"
            f"{said} · {MAX_ANIMATION_SECONDS - self.animated}s of animation left"
        )

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
        if voice == MY_VOICE and getattr(self.voice, "reference", None) is None:
            return ToolOutcome(
                "make_voiceover: the user has not recorded their voice (Settings > Your voice); "
                "use another voice or tell them how",
                is_error=True,
            )
        if voice not in VOICES and voice not in BUILT_IN and voice != MY_VOICE:
            return ToolOutcome(
                f"make_voiceover: unknown voice {voice!r}; use <accent>_<woman|man>, e.g. "
                "nigerian_man, british_woman",
                is_error=True,
            )
        if self.voice is None:
            return ToolOutcome(
                "make_voiceover: voiceovers are not available on this server (no voice "
                "service configured); tell the user instead",
                is_error=True,
            )
        if sum(1 for _, n in self.generated if n == "voiceover.mp3") >= MAX_VOICEOVERS:
            return ToolOutcome(
                f"make_voiceover: at most {MAX_VOICEOVERS} voiceovers per request; reuse one",
                is_error=True,
            )
        try:
            info, captions = self._speak(text, voice)
        except (VoiceError, subprocess.CalledProcessError, OSError) as e:
            return ToolOutcome(f"make_voiceover: {e}", is_error=True)
        self.generated.append((info.handle, "voiceover.mp3"))
        self.generated.append((captions.handle, "captions.srt"))
        duration = self.engine.probe(info).get("duration") or 0.0
        return ToolOutcome(
            f"{info.handle} · audio · voiceover {duration:.2f}s · voice {voice} · "
            f"{len(text.split())} words\n{captions.handle} · text · word-timed captions for it "
            "(burn in with video.captions, style bold_pop, starting where the voice starts)"
        )

    def _speak(self, text: str, voice: str) -> tuple[Any, Any]:
        """The voiceover and its captions. Each sentence is spoken on its own (four at a time),
        which gives every sentence's exact length for word-timed captions; the parts are joined,
        brought to -16 LUFS and re-encoded once (one clean header, an exact duration)."""
        assert self.voice is not None
        voice_service = self.voice
        work = Path(tempfile.mkdtemp(prefix="voice-", dir=self.engine.store.tmp))
        spoken = sentences(text)
        with ThreadPoolExecutor(max_workers=4) as pool:
            audio = list(pool.map(lambda s: voice_service.speak(s, voice), spoken))
        parts, lengths = [], []
        for i, data in enumerate(audio):
            part = work / f"part{i:03d}.mp3"
            part.write_bytes(data)
            parts.append(part)
            probe = subprocess.run(
                [find("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(part)],
                capture_output=True, text=True, timeout=60,
            )  # fmt: skip
            lengths.append(float(probe.stdout.strip() or 0.0))
        listing = work / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        out = work / "voiceover.mp3"
        subprocess.run(
            [
                find("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(listing),
                # TTS comes out quiet (about -26 dB): bring it to web-video loudness, and end on
                # a short silence so a fade at the end of the video never cuts the last word
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur=0.6",
                "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-map_metadata", "-1",
                str(out),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )  # fmt: skip
        info = self.engine.store.put_file(out, media=MediaType.AUDIO, ext=".mp3", move=True)
        cues = srt(caption_cues(word_times(spoken, lengths)))
        captions = self.engine.store.put_bytes(cues.encode(), media=MediaType.TEXT, ext=".srt")
        for p in [*parts, listing]:
            p.unlink(missing_ok=True)
        work.rmdir()
        return info, captions

    def _make_music(self, args: dict[str, Any]) -> ToolOutcome:
        style = " ".join(str(args.get("style") or "").split())[:600]
        seconds = args.get("seconds") if isinstance(args.get("seconds"), int) else 30
        bpm = args.get("bpm") if isinstance(args.get("bpm"), int) else 100
        if not style:
            return ToolOutcome("make_music: style must not be empty", is_error=True)
        if not 10 <= seconds <= 180:
            return ToolOutcome("make_music: seconds must be 10 to 180", is_error=True)
        if not 60 <= bpm <= 160:
            return ToolOutcome("make_music: bpm must be 60 to 160", is_error=True)
        if self.music is None:
            return ToolOutcome("make_music: not available on this server", is_error=True)
        if sum(1 for _, n in self.generated if n == "music.mp3") >= MAX_MUSIC:
            return ToolOutcome(f"make_music: at most {MAX_MUSIC} tracks per request", is_error=True)
        seed = int.from_bytes(style.encode()[:4].ljust(4, b"\0"), "big") % 100000
        try:
            data = self.music.compose(style, seconds, bpm, seed)
            info = self.engine.store.put_bytes(data, media=MediaType.AUDIO, ext=".mp3")
        except (MusicError, OSError) as e:
            return ToolOutcome(f"make_music: {e}", is_error=True)
        self.generated.append((info.handle, "music.mp3"))
        duration = self.engine.probe(info).get("duration") or float(seconds)
        beat = 60.0 / bpm
        return ToolOutcome(
            f"{info.handle} · audio · original music {duration:.2f}s · {bpm} bpm · one beat "
            f"{beat:.3f}s, a bar of 4 beats {4 * beat:.3f}s. Make shot lengths whole beats "
            "(e.g. 4 beats), put it under the voice with audio.mix (length music)."
        )

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


def _slug(text: str, prefix: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    return f"{prefix}-{'-'.join(words) or 'picture'}"[:60]


def _strings(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


ANIMATION_SHAPES = ("landscape", "portrait", "square")


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(
        [find("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-y", *args],
        check=True, capture_output=True, timeout=300,
    )  # fmt: skip


def _seconds(path: Path) -> float:
    probe = subprocess.run(
        [find("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    return float(probe.stdout.strip() or 0.0)
