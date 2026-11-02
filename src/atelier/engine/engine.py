"""The engine facade: one object that validates, plans and runs specs against a home dir."""

from __future__ import annotations

import copy
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atelier.checks import Status, load_builtin_checks, summarize
from atelier.engine.cache import ResultCache
from atelier.engine.runner import JobResult, Observer, Plan, RunLimits, StepFailed, plan, probe, run
from atelier.engine.store import LocalStore
from atelier.fmt import human_bytes
from atelier.media import SHORT_HASH_LEN, AssetInfo, MediaType
from atelier.ops.registry import Registry, load_builtin_ops
from atelier.spec.errors import SpecInvalid
from atelier.spec.model import CanonicalSpec
from atelier.spec.validate import SpecLimits, validate


@dataclass
class Engine:
    home: Path
    registry: Registry
    store: LocalStore
    cache: ResultCache
    run_limits: RunLimits
    spec_limits: SpecLimits
    observer: Observer | None = None  # progress hook for a job (see runner.Observer)

    @classmethod
    def open(
        cls,
        home: Path,
        *,
        registry: Registry | None = None,
        run_limits: RunLimits = RunLimits(),
        spec_limits: SpecLimits = SpecLimits(),
    ) -> Engine:
        return cls(
            home=home,
            registry=registry if registry is not None else load_builtin_ops(),
            store=LocalStore(home / "store"),
            cache=ResultCache(home / "cache"),
            run_limits=run_limits,
            spec_limits=spec_limits,
        )

    def validate(self, raw: Any) -> CanonicalSpec:
        return validate(
            raw,
            registry=self.registry,
            store=self.store,
            known_checks=load_builtin_checks(),
            limits=self.spec_limits,
        )

    def plan(self, raw: Any) -> Plan:
        return plan(self.validate(raw), store=self.store, cache=self.cache)

    def run(self, raw: Any) -> JobResult:
        spec = raw if isinstance(raw, CanonicalSpec) else self.validate(raw)
        return run(
            spec,
            store=self.store,
            cache=self.cache,
            limits=self.run_limits,
            observer=self.observer,
        )

    def probe(self, info: AssetInfo) -> dict[str, Any]:
        return probe(info, store=self.store, cache=self.cache, limits=self.run_limits)

    def inspect(self, info: AssetInfo, *, full: bool = False) -> str:
        return inspect_line(info, self.probe(info), full=full)

    def preview(
        self, kind: str, handles: Sequence[str], args: dict[str, Any] | None = None
    ) -> JobResult:
        """Run a preview.* op on the given assets (sandboxed and cached like any step).

        A video is previewed as frames: `thumb` is one frame (at `time` seconds, else the
        middle), `sheet` is a labelled contact sheet of `count` evenly spaced frames.
        """
        op, extra = f"preview.{kind}", args or {}
        media = self.store.resolve(handles[0]).media if handles else None
        if media is MediaType.VIDEO:
            op, extra = self._video_preview(kind, extra)
        elif media in _DOCUMENT_MEDIA:
            return self._document_preview(kind, handles[0], extra, media)
        names = [f"a{i}" for i in range(len(handles))]
        return self.run(
            {
                "inputs": dict(zip(names, handles, strict=True)),
                "steps": [{"id": "preview", "op": op, "in": names, "args": extra}],
                "outputs": ["preview"],
                "checks": "none",
            }
        )

    def _document_preview(
        self, kind: str, handle: str, args: dict[str, Any], media: MediaType
    ) -> JobResult:
        """Pages of a document: `thumb` is one page (`page`, default 1), `sheet` the first pages.
        Word, PowerPoint and Excel files are printed to PDF first."""
        if kind == "thumb":
            pages: dict[str, Any] = {"at": [int(args.get("page", 1))], "cell_width": 720}
        elif kind == "sheet":
            keep = {k: v for k, v in args.items() if k in ("count", "columns", "cell_width", "at")}
            pages = keep
        else:
            raise ValueError(
                f"a document can be previewed as thumb (one page) or sheet, not {kind!r}"
            )
        steps: list[dict[str, Any]] = []
        source = "a0"
        if media is not MediaType.PDF:
            steps.append({"id": "printed", "op": "doc.convert", "in": ["a0"], "args": {}})
            source = "printed"
        steps.append({"id": "preview", "op": "preview.pages", "in": [source], "args": pages})
        return self.run(
            {"inputs": {"a0": handle}, "steps": steps, "outputs": ["preview"], "checks": "none"}
        )

    @staticmethod
    def _video_preview(kind: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if kind == "thumb":
            frames: dict[str, Any] = {
                "count": 1,
                "cell_width": min(int(args.get("long_edge", 512)), 512),
            }
            if "time" in args:
                frames["at"] = [args["time"]]
            return "preview.frames", frames
        if kind == "sheet":
            keep = {k: v for k, v in args.items() if k in ("count", "columns", "cell_width", "at")}
            return "preview.frames", keep
        raise ValueError(f"a video can be previewed as thumb (one frame) or sheet, not {kind!r}")

    def run_batch(
        self, template: Any, name: str, handles: Sequence[str], *, workers: int | None = None
    ) -> list[BatchItem]:
        """Run one spec over many assets bound to input `name` (docs/04 §4.7 batch pattern).

        Items run concurrently; each step is still its own sandboxed subprocess, so threads
        only wait on processes. Identical work across items is shared through the cache.
        """

        def one(handle: str) -> BatchItem:
            try:
                return BatchItem(handle, self.run(bind_input(template, name, handle)), None)
            except (SpecInvalid, StepFailed) as e:
                return BatchItem(handle, None, str(e))

        with ThreadPoolExecutor(max_workers=workers or min(8, os.cpu_count() or 1)) as pool:
            return list(pool.map(one, handles))


@dataclass(frozen=True)
class BatchItem:
    handle: str
    result: JobResult | None
    error: str | None


def bind_input(spec: Any, name: str, handle: str) -> Any:
    """Return a copy of a spec (any form) with input `name` set to `handle`."""
    if not isinstance(spec, dict):
        return spec
    bound = copy.deepcopy(spec)
    if "recipe" in bound:
        bound.setdefault("params", {})[name] = handle
    elif "ops" in bound and name == "in":
        bound["in"] = handle
    else:
        bound.setdefault("inputs", {})[name] = handle
    return bound


def _short(digest: str) -> str:
    return f"asset:{digest[:SHORT_HASH_LEN]}"


def format_result(result: JobResult) -> str:
    """The compact tool-result text (docs/04-agent-layer.md §4.2)."""
    lines = []
    for out_id, rep in result.outputs.items():
        failed = any(r.status is Status.FAIL for r in result.checks.get(out_id, []))
        lines.append(
            f"{'fail' if failed else 'ok'} · {out_id} → {_short(rep.hash)} "
            f"({rep.media.value} · {human_bytes(rep.size)})"
        )
        lines.append(f"  {rep.summary}")
        if result.checks.get(out_id):
            lines.append(f"  {summarize(result.checks[out_id])}")
    fused = (
        f" · {len(result.spec.steps)} steps in {len(result.steps)} runs (fused)"
        if result.fused_steps
        else ""
    )
    lines.append(
        f"cache: {result.reused}/{len(result.steps)} steps reused{fused} · {result.seconds:.1f}s"
    )
    return "\n".join(lines)


def format_plan(p: Plan) -> str:
    n, runs = len(p.spec.steps), len(p.steps)
    fused = f" → {runs} runs" if runs != n else ""
    return f"valid · {n} step{'s' * (n != 1)}{fused} · {p.cached} cached · {runs - p.cached} to run"


def _av_parts(meta: dict[str, Any]) -> list[str]:
    parts = []
    if meta.get("duration") is not None:
        parts.append(f"{meta['duration']:.2f}s")
    if v := meta.get("video"):
        w, h = v["display"]
        parts.append(f"{w}x{h}" + (f" @{v['fps']:g}fps" if v.get("fps") else ""))
        parts.append(f"{v['codec']}" + (" (VFR)" if v.get("vfr") else ""))
        if v.get("rotation"):
            parts.append(f"rotated {v['rotation']}° (auto-applied)")
    if a := meta.get("audio"):
        layout = a.get("channel_layout") or f"{a['channels']}ch"
        parts.append(f"audio {a['codec']} {layout} {(a.get('sample_rate') or 0) / 1000:g}kHz")
    elif meta.get("video"):
        parts.append("no audio")
    return parts


_DOCUMENT_MEDIA = frozenset({MediaType.PDF, MediaType.DOCX, MediaType.PPTX, MediaType.XLSX})


def _document_parts(media: MediaType, meta: dict[str, Any], full: bool) -> list[str]:
    """What a document contains, in the words the ops use (fields, placeholders, sheets)."""
    parts: list[str] = []
    if media is MediaType.PDF:
        n = meta["pages"]
        parts.append(f"{n} page{'s' * (n != 1)}")
        if meta["sizes_pt"]:
            parts.append(f"{meta['sizes_pt'][0][0]:g}x{meta['sizes_pt'][0][1]:g}pt")
        parts.append(
            f"{meta['text_chars']} chars of text" if meta["text_chars"] else "no text (scanned?)"
        )
        if meta["fields"]:
            names = ", ".join(f["name"] for f in meta["fields"][: 12 if full else 6])
            parts.append(f"form fields: {names}")
        if meta["javascript"]:
            parts.append("has JavaScript (removed on output)")
        return parts
    if media is MediaType.DOCX:
        parts += [
            f"{meta['paragraphs']} paragraphs",
            f"{meta['tables']} tables",
            f"{meta['images']} images",
            f"{meta['words']} words",
        ]
    elif media is MediaType.PPTX:
        parts += [f"{meta['slides']} slides", f"layouts: {', '.join(meta['layouts'][:6])}"]
    else:
        sheets = ", ".join(f"{s['name']} ({s['rows']}x{s['cols']})" for s in meta["sheets"][:6])
        parts += [f"sheets: {sheets}", f"{meta['formulas']} formulas"]
        if meta["errors"]:
            parts.append(f"{len(meta['errors'])} formula errors")
    if meta["placeholders"]:
        listed = ", ".join("{{" + name + "}}" for name in meta["placeholders"][: 20 if full else 8])
        parts.append(f"placeholders: {listed}")
    return parts


def inspect_line(info: AssetInfo, meta: dict[str, Any], *, full: bool = False) -> str:
    """One compact line per asset (docs/04 §4.2 inspect_media, brief)."""
    parts = [info.media.value]
    if "error" in meta:
        parts.append(f"unreadable: {meta['error']}")
    elif info.media is MediaType.IMAGE:
        w, h = meta["display"]
        parts += [f"{w}x{h}", f"{meta['format']} {meta['mode']}"]
        parts.append(f"ICC {meta['icc']}" if meta.get("icc") else "no ICC")
        if meta.get("orientation", 1) != 1:
            parts.append(f"EXIF orientation {meta['orientation']} (auto-applied)")
        if meta.get("frames", 1) > 1:
            parts.append(f"{meta['frames']} frames (first used)")
    elif info.media is MediaType.TEXT:
        n = meta["lines"]
        parts += [f"{n} line{'s' * (n != 1)}", f"{meta['chars']} chars"]
    elif info.media in (MediaType.VIDEO, MediaType.AUDIO):
        parts += _av_parts(meta)
    elif info.media in (MediaType.PDF, MediaType.DOCX, MediaType.PPTX, MediaType.XLSX):
        parts += _document_parts(info.media, meta, full)
    parts.append(human_bytes(info.size))
    line = " · ".join(parts) + f" · {info.short}"
    if full:
        extra = f"sha256 {info.hash} · ext {info.ext or '-'}"
        if meta.get("dpi"):
            extra += f" · {meta['dpi']} dpi"
        line += f"\n  {extra}"
    return line
