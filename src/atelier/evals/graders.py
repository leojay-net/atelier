"""Deterministic graders (docs/09 §9.4, grader kinds 1 and 2).

Each grader gets its config from the task file and a `Context` (the agent's result and the
engine). Output graders open the delivered files with Pillow, so they judge what the user
would receive, not what an op claims to have produced. Spec graders read the canonical spec the
engine actually ran, so `"12%"` and `0.12` are the same to them.

A grader marked `soft: true` is reported but does not decide pass/fail (for example "did it use
the recipe?": interesting, but a correct pipeline by another route still passes).

`PORTABLE` names the graders that judge only the delivered files (or whether the system asked
instead of guessing). They apply equally to a system with no spec and no engine checks, which
is what lets the baseline (a general coding agent) be compared with the agent on equal terms.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from atelier.agent.loop import AgentResult
from atelier.av.graph import measure_file
from atelier.av.probe import ProbeError, probe_av
from atelier.engine.engine import Engine
from atelier.engine.sniff import sniff
from atelier.media import MediaType


@dataclass(frozen=True)
class GraderResult:
    name: str
    passed: bool
    detail: str = ""
    soft: bool = False


@dataclass
class Context:
    result: AgentResult
    engine: Engine | None = None
    # The deliverables as plain files, for a run that has no engine result (the baseline).
    files: Sequence[Path] | None = None

    def paths(self) -> list[Path]:
        if self.files is not None:
            return list(self.files)
        er = self.result.engine_result
        if er is None or self.engine is None:
            return []
        return [self.engine.store.path(rep.hash) for rep in er.outputs.values()]

    def images(self) -> list[Image.Image]:
        return [Image.open(p) for p in self.paths()]

    def media(self) -> list[Delivered]:
        return [describe(p) for p in self.paths()]

    def steps(self) -> list[Any]:
        er = self.result.engine_result
        return list(er.spec.steps) if er is not None else []


@dataclass(frozen=True)
class Delivered:
    """One delivered file as a viewer would meet it: what kind it is and how big/long."""

    path: Path
    kind: str  # image | video | audio | other
    format: str  # JPEG, PNG… for pictures; MP4, MP3, WAV… for the rest
    size: tuple[int, int] | None = None
    duration: float | None = None
    has_audio: bool = False
    has_video: bool = False
    pages: int | None = None  # PDF pages, PowerPoint slides
    text: str = ""  # everything a reader could select, for documents
    page_texts: tuple[str, ...] = ()  # the same, page by page (PDF) or slide by slide (PPTX)
    info: dict[str, Any] = field(default_factory=dict)  # the document's own description


_DOCUMENTS = (MediaType.PDF, MediaType.DOCX, MediaType.XLSX, MediaType.PPTX)


def _document(path: Path, media: MediaType, ext: str) -> Delivered:
    """A Word, Excel, PowerPoint or PDF file: its description and its text."""
    from atelier.docs import pdfdoc, sheet, slides, word
    from atelier.docs.probe import probe_document
    from atelier.ops.registry import OpError

    fmt = ext.lstrip(".").upper()
    info = probe_document(path, media)
    if "error" in info:
        return Delivered(path, "other", fmt, info=info)
    try:
        per_page: tuple[str, ...] = ()
        if media is MediaType.PDF:
            text, pages = pdfdoc.all_text(path), info["pages"]
            doc = pdfdoc.open_render(path)
            try:
                per_page = tuple(pdfdoc.page_text(doc, i) for i in range(min(len(doc), 60)))
            finally:
                doc.close()
        elif media is MediaType.DOCX:
            text, pages = "\n".join(word.paragraph_texts(word.open_docx(path))), None
        elif media is MediaType.PPTX:
            deck = slides.slide_texts(slides.open_pptx(path))
            per_page = tuple("\n".join(lines) for lines in deck)
            text, pages = "\n".join(per_page), info["slides"]
        else:
            wb = sheet.open_xlsx(path, values=True)
            cells = (c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row)
            text, pages = "\n".join(str(v) for v in cells if v is not None), None
    except OpError:
        return Delivered(path, "other", fmt, info=info)
    return Delivered(path, media.value, fmt, pages=pages, text=text, page_texts=per_page, info=info)


def describe(path: Path) -> Delivered:
    """Judge the file by its content, never by what its name or an op claims."""
    try:
        with Image.open(path) as im:
            return Delivered(path, "image", (im.format or "?").upper(), im.size)
    except (OSError, ValueError):
        pass
    media, ext = sniff(path)
    fmt = ext.lstrip(".").upper() or "?"
    if media in _DOCUMENTS:
        return _document(path, media, ext)
    if media not in (MediaType.VIDEO, MediaType.AUDIO):
        return Delivered(path, "other", fmt)
    try:
        meta = probe_av(path, ext)
    except ProbeError:
        return Delivered(path, "other", fmt)
    video, audio = meta.get("video"), meta.get("audio")
    return Delivered(
        path,
        "video" if video else "audio",
        fmt,
        tuple(video["display"]) if video else None,
        meta.get("duration"),
        audio is not None,
        video is not None,
    )


Verdict = tuple[bool, str]
Grader = Callable[[dict[str, Any], Context], Verdict]


def _agent_ok(cfg: dict[str, Any], ctx: Context) -> Verdict:
    r = ctx.result
    return r.ok, r.status + (f" ({r.detail})" if r.detail else "")


def _asks_question(cfg: dict[str, Any], ctx: Context) -> Verdict:
    r = ctx.result
    if r.status != "needs_input" or not r.question:
        return False, f"expected a question, got {r.status}"
    if r.outputs:
        return False, "asked a question but also produced output"
    return True, r.question


def _over_outputs(ctx: Context, check: Callable[[Delivered], str | None]) -> Verdict:
    delivered = ctx.media()
    if not delivered:
        return False, "no output"
    problems = [p for d in delivered if (p := check(d))]
    return (not problems), "; ".join(problems) or f"{len(delivered)} output(s) ok"


def _dims(cfg: dict[str, Any], ctx: Context) -> Verdict:
    tol = float(cfg.get("tol", 0.02))

    def check(d: Delivered) -> str | None:
        if d.size is None:
            return f"a {d.kind} file has no picture size"
        w, h = d.size
        if "width" in cfg and w != cfg["width"]:
            return f"width {w}, expected {cfg['width']}"
        if "height" in cfg and h != cfg["height"]:
            return f"height {h}, expected {cfg['height']}"
        if "max_edge" in cfg and max(w, h) > cfg["max_edge"]:
            return f"long edge {max(w, h)} exceeds {cfg['max_edge']}"
        if "aspect" in cfg:
            aw, ah = cfg["aspect"]
            if abs(w / h - aw / ah) > tol * (aw / ah):
                return f"aspect {w}x{h} is {w / h:.3f}, expected {aw}:{ah}"
        if cfg.get("orientation") == "portrait" and not h > w:
            return f"{w}x{h} is not portrait"
        if cfg.get("orientation") == "landscape" and not w > h:
            return f"{w}x{h} is not landscape"
        return None

    return _over_outputs(ctx, check)


def _format(cfg: dict[str, Any], ctx: Context) -> Verdict:
    wanted = {f.upper() for f in cfg["formats"]}

    def check(d: Delivered) -> str | None:
        return (
            None
            if d.format in wanted
            else f"format {d.format}, expected {'|'.join(sorted(wanted))}"
        )

    return _over_outputs(ctx, check)


def _max_bytes(cfg: dict[str, Any], ctx: Context) -> Verdict:
    paths = ctx.paths()
    if not paths:
        return False, "no output"
    big = [f"{p.stat().st_size} bytes" for p in paths if p.stat().st_size > cfg["bytes"]]
    return (not big), "; ".join(big) or "within budget"


def _alpha(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """`present: true` needs real transparency, not just an alpha band that is fully opaque."""

    def check(d: Delivered) -> str | None:
        if d.kind != "image":
            return f"a {d.kind} file has no alpha channel to judge"
        with Image.open(d.path) as im:
            transparent = bool(np.asarray(im.convert("RGBA"))[..., 3].min() < 255)
        if cfg.get("present", True) and not transparent:
            return "no transparency in the output"
        if not cfg.get("present", True) and transparent:
            return "unexpected transparency in the output"
        return None

    return _over_outputs(ctx, check)


def _output_count(cfg: dict[str, Any], ctx: Context) -> Verdict:
    n = len(ctx.paths())
    return n == cfg["n"], f"{n} output(s), expected {cfg['n']}"


def _kind(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """What the deliverable is: `is: image | video | audio`."""

    def check(d: Delivered) -> str | None:
        return None if d.kind == cfg["is"] else f"a {d.kind} file, expected {cfg['is']}"

    return _over_outputs(ctx, check)


def _duration(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Length in seconds: `seconds` ± `tol` (default 0.4), or a `min`/`max` window."""
    tol = float(cfg.get("tol", 0.4))

    def check(d: Delivered) -> str | None:
        if d.duration is None:
            return f"a {d.kind} file has no duration"
        if "seconds" in cfg and abs(d.duration - cfg["seconds"]) > tol:
            return f"{d.duration:.2f}s long, expected {cfg['seconds']:g}s ± {tol:g}"
        if "min" in cfg and d.duration < cfg["min"]:
            return f"{d.duration:.2f}s long, expected at least {cfg['min']:g}s"
        if "max" in cfg and d.duration > cfg["max"]:
            return f"{d.duration:.2f}s long, expected at most {cfg['max']:g}s"
        return None

    return _over_outputs(ctx, check)


def _sound(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Has (or lacks) an audio track: `present: true|false`."""
    want = bool(cfg.get("present", True))

    def check(d: Delivered) -> str | None:
        if d.has_audio == want:
            return None
        return "no audio track" if want else "has an audio track that should be gone"

    return _over_outputs(ctx, check)


def _picture(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Has (or lacks) a picture: `present: true|false` (audio-only deliverables have none)."""
    want = bool(cfg.get("present", True))

    def check(d: Delivered) -> str | None:
        if d.has_video == want or (d.kind == "image" and want):
            return None
        return "no picture" if want else "has a picture but should be audio only"

    return _over_outputs(ctx, check)


def _loudness(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Integrated loudness (LUFS) between `min` and `max`, measured on the delivered file."""

    def check(d: Delivered) -> str | None:
        if not d.has_audio:
            return "no audio to measure"
        m = measure_file(d.path)
        if m is None:
            return "the audio is silent"
        lufs = m["input_i"]
        if not cfg.get("min", -99) <= lufs <= cfg.get("max", 0):
            return f"{lufs:.1f} LUFS, expected {cfg.get('min', -99):g} to {cfg.get('max', 0):g}"
        return None

    return _over_outputs(ctx, check)


def _count(cfg: dict[str, Any], have: int | None, what: str) -> str | None:
    if have is None:
        return f"no {what} to count"
    if "n" in cfg and have != cfg["n"]:
        return f"{have} {what}, expected {cfg['n']}"
    if "min" in cfg and have < cfg["min"]:
        return f"{have} {what}, expected at least {cfg['min']}"
    if "max" in cfg and have > cfg["max"]:
        return f"{have} {what}, expected at most {cfg['max']}"
    return None


def _pages(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Pages of a PDF: `n`, or `min`/`max`."""
    return _over_outputs(ctx, lambda d: _count(cfg, d.pages if d.kind == "pdf" else None, "pages"))


def _slides(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Slides of a presentation: `n`, or `min`/`max`."""
    return _over_outputs(
        ctx, lambda d: _count(cfg, d.pages if d.kind == "pptx" else None, "slides")
    )


def _text_has(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """The file's text contains every term (`any: true`: at least one), ignoring case."""

    def check(d: Delivered) -> str | None:
        if d.kind not in ("pdf", "docx", "xlsx", "pptx"):
            return f"a {d.kind} file has no text to search"
        hay = d.text.casefold()
        found = [t for t in cfg["terms"] if str(t).casefold() in hay]
        ok = bool(found) if cfg.get("any") else len(found) == len(cfg["terms"])
        if ok:
            return None
        missing = [t for t in cfg["terms"] if t not in found]
        return f"the text lacks {missing}"

    return _over_outputs(ctx, check)


def _text_lacks(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """None of the terms can be found in the file's text (redaction, filled placeholders)."""

    def check(d: Delivered) -> str | None:
        if d.kind not in ("pdf", "docx", "xlsx", "pptx"):
            return f"a {d.kind} file has no text to search"
        hay = d.text.casefold()
        left = [t for t in cfg["terms"] if str(t).casefold() in hay]
        return f"the text still contains {left}" if left else None

    return _over_outputs(ctx, check)


def _page_text(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """What is on page (or slide) `page` (1-based): it `has` this text, ignoring case."""

    def check(d: Delivered) -> str | None:
        if not d.page_texts or not 1 <= cfg["page"] <= len(d.page_texts):
            return f"no page {cfg['page']} to read"
        if str(cfg["has"]).casefold() in d.page_texts[cfg["page"] - 1].casefold():
            return None
        return f"page {cfg['page']} does not contain {cfg['has']!r}"

    return _over_outputs(ctx, check)


def _rotation(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """A PDF page is turned by `degrees` (clockwise, 0-270)."""
    import pikepdf

    def check(d: Delivered) -> str | None:
        if d.kind != "pdf":
            return f"a {d.kind} file has no page rotation"
        with pikepdf.Pdf.open(d.path) as pdf:
            if not 1 <= cfg["page"] <= len(pdf.pages):
                return f"no page {cfg['page']}"
            turned = int(pdf.pages[cfg["page"] - 1].get("/Rotate", 0)) % 360
        return (
            None
            if turned == cfg["degrees"] % 360
            else f"page turned {turned}°, expected {cfg['degrees']}°"
        )

    return _over_outputs(ctx, check)


def _info(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """A count from the document's own description (`key`: images, tables, fields…): `n`, `min`,
    `max`. A list (the form fields) counts its entries."""

    def have(d: Delivered) -> int | None:
        value = d.info.get(cfg["key"])
        return len(value) if isinstance(value, list | dict) else value

    return _over_outputs(ctx, lambda d: _count(cfg, have(d), f"{cfg['key']} in the file"))


def _placeholders(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """No {{placeholder}} left in a document (default: none)."""
    limit = int(cfg.get("max", 0))

    def check(d: Delivered) -> str | None:
        left = d.info.get("placeholders") or [m for m in ("{{",) if m in d.text]
        return f"{len(left)} placeholder(s) left: {left[:4]}" if len(left) > limit else None

    return _over_outputs(ctx, check)


def _fonts(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Every font of a PDF is embedded (print)."""

    def check(d: Delivered) -> str | None:
        if d.kind != "pdf":
            return f"a {d.kind} file has no embedded fonts to judge"
        loose = [f["name"] for f in d.info.get("fonts", []) if not f["embedded"]]
        return f"fonts not embedded: {loose}" if loose else None

    return _over_outputs(ctx, check)


def _form(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """PDF form fields have values (`values: {name: text}`); a flattened form has them as text."""

    def check(d: Delivered) -> str | None:
        if d.kind != "pdf":
            return f"a {d.kind} file is not a form"
        fields = {f["name"]: f["value"] for f in d.info.get("fields", [])}
        bad = []
        for name, want in cfg["values"].items():
            got = fields.get(name)
            if got is None:  # no such field any more: flattened, so the value is page text
                if str(want).casefold() not in d.text.casefold():
                    bad.append(f"{name}: not filled")
            elif str(got).casefold() != str(want).casefold() and not (want is True and got == "on"):
                bad.append(f"{name}: {got!r}, expected {want!r}")
        return "; ".join(bad) or None

    return _over_outputs(ctx, check)


def _cell(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """A workbook cell: `equals` a calculated value, or `formula` starting with some text."""
    from atelier.docs import sheet

    def check(d: Delivered) -> str | None:
        if d.kind != "xlsx":
            return f"a {d.kind} file is not a workbook"
        want_formula = cfg.get("formula")
        wb = sheet.open_xlsx(d.path, values=want_formula is None)
        ws = wb[cfg["sheet"]] if cfg.get("sheet") else wb.worksheets[0]
        value = ws[cfg["ref"]].value
        if want_formula is not None:
            ok = isinstance(value, str) and value.upper().startswith(str(want_formula).upper())
            return None if ok else f"{cfg['ref']} is {value!r}, expected a formula {want_formula!r}"
        if isinstance(cfg["equals"], int | float) and isinstance(value, int | float):
            ok = abs(float(value) - float(cfg["equals"])) <= float(cfg.get("tol", 1e-6))
        else:
            ok = value == cfg["equals"]
        return None if ok else f"{cfg['ref']} is {value!r}, expected {cfg['equals']!r}"

    return _over_outputs(ctx, check)


def _calculated(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Every formula has a stored result, and none of them is an error like #DIV/0!."""
    from atelier.docs import sheet

    def check(d: Delivered) -> str | None:
        if d.kind != "xlsx":
            return f"a {d.kind} file is not a workbook"
        formulas = sheet.open_xlsx(d.path)
        values = sheet.open_xlsx(d.path, values=True)
        errors = d.info.get("errors", [])
        if errors:
            return f"{len(errors)} formula error(s): {errors[0]['sheet']}!{errors[0]['cell']}"
        for ws in formulas.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.data_type == "f" and values[ws.title][c.coordinate].value is None:
                        return f"{ws.title}!{c.coordinate} has never been calculated"
        return None

    return _over_outputs(ctx, check)


def _ops_include(cfg: dict[str, Any], ctx: Context) -> Verdict:
    have = {s.op.name for s in ctx.steps()}
    missing = [o for o in cfg["ops"] if o not in have]
    return (not missing), f"missing {missing}" if missing else "all present"


def _ops_any(cfg: dict[str, Any], ctx: Context) -> Verdict:
    have = {s.op.name for s in ctx.steps()}
    ok = any(o in have for o in cfg["ops"])
    return ok, f"none of {cfg['ops']} used" if not ok else "found"


def _ops_exclude(cfg: dict[str, Any], ctx: Context) -> Verdict:
    have = {s.op.name for s in ctx.steps()}
    used = [o for o in cfg["ops"] if o in have]
    return (not used), f"used forbidden {used}" if used else "none used"


def _recipe(cfg: dict[str, Any], ctx: Context) -> Verdict:
    er = ctx.result.engine_result
    origin = er.spec.origin if er is not None else None
    got = origin["recipe"] if origin else None
    return got == cfg["name"], f"recipe {got!r}, expected {cfg['name']!r}"


def _step_arg(cfg: dict[str, Any], ctx: Context) -> Verdict:
    """Some step of `op` whose `arg` equals or contains the wanted value (text: any case)."""
    for s in ctx.steps():
        if s.op.name != cfg["op"] or cfg["arg"] not in s.args:
            continue
        value = s.args[cfg["arg"]]
        if "equals" in cfg and value == cfg["equals"]:
            return True, f"{cfg['op']}.{cfg['arg']} = {value!r}"
        if "contains" in cfg and str(cfg["contains"]).casefold() in str(value).casefold():
            return True, f"{cfg['op']}.{cfg['arg']} = {value!r}"
    return (
        False,
        f"no {cfg['op']} step with {cfg['arg']} matching "
        f"{cfg.get('equals', cfg.get('contains'))!r}",
    )


GRADERS: dict[str, Grader] = {
    "agent_ok": _agent_ok,
    "asks_question": _asks_question,
    "dims": _dims,
    "format": _format,
    "max_bytes": _max_bytes,
    "alpha": _alpha,
    "kind": _kind,
    "duration": _duration,
    "pages": _pages,
    "slides": _slides,
    "text_has": _text_has,
    "text_lacks": _text_lacks,
    "page_text": _page_text,
    "rotation": _rotation,
    "info": _info,
    "placeholders": _placeholders,
    "fonts": _fonts,
    "form": _form,
    "cell": _cell,
    "calculated": _calculated,
    "sound": _sound,
    "picture": _picture,
    "loudness": _loudness,
    "output_count": _output_count,
    "ops_include": _ops_include,
    "ops_any": _ops_any,
    "ops_exclude": _ops_exclude,
    "recipe": _recipe,
    "step_arg": _step_arg,
}


PORTABLE = frozenset(
    {
        "dims", "format", "max_bytes", "alpha", "output_count", "asks_question", "rubric",
        "kind", "duration", "sound", "picture", "loudness",
        "pages", "slides", "text_has", "text_lacks", "placeholders", "fonts", "form", "cell",
        "calculated", "page_text", "rotation", "info",
    }
)  # fmt: skip


def grade(configs: tuple[dict[str, Any], ...], ctx: Context) -> list[GraderResult]:
    results = []
    for cfg in configs:
        passed, detail = GRADERS[cfg["type"]](cfg, ctx)
        results.append(GraderResult(cfg["type"], passed, detail, bool(cfg.get("soft", False))))
    return results
