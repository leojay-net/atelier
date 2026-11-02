"""Worker entry point: runs exactly one op in a fresh, resource-limited process.

Invoked as `python -m atelier.engine.worker <work_dir>/job.json`, or with `--standby`
(see `pool.py`): import everything first, then wait for the job path on stdin. Either way the
process runs one job and exits. Writes result.json next to the job file. The parent never
trusts stdout, which libraries may pollute.
"""

from __future__ import annotations

import errno
import importlib
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from atelier.engine.probe import probe
from atelier.media import MediaType
from atelier.ops.registry import OP_ATTR, Artifact, OpContext, OpDef, OpError

MAX_SUMMARY = 120


def _apply_limits(limits: dict[str, int]) -> None:
    try:
        import resource
    except ImportError:  # not POSIX; rely on the container's limits
        return

    def cap(which: int, value: int) -> None:
        try:
            _soft, hard = resource.getrlimit(which)
            new = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(which, (new, hard))
        except (ValueError, OSError):
            pass  # e.g. RLIMIT_AS is not enforceable on macOS; containers use cgroups

    cap(resource.RLIMIT_CPU, limits["cpu_seconds"])
    cap(resource.RLIMIT_FSIZE, limits["output_bytes"])
    cap(resource.RLIMIT_AS, limits["memory_bytes"])
    if hasattr(signal, "SIGXFSZ"):
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # oversized writes raise instead


def _resolve(module: str, qualname: str) -> OpDef:
    obj: Any = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    opdef = getattr(obj, OP_ATTR, None)
    if not isinstance(opdef, OpDef):
        raise RuntimeError(f"{module}.{qualname} is not a registered op")
    return opdef


def run_job(job: dict[str, Any], work: Path) -> dict[str, Any]:
    _apply_limits(job["limits"])
    opdef = _resolve(job["module"], job["qualname"])
    params = opdef.params.model_validate(job["args"])
    inputs = [
        Artifact(Path(i["path"]), MediaType(i["media"]), i["ext"], i["meta"]) for i in job["inputs"]
    ]
    out_dir = work / "out"
    result = opdef.fn(OpContext(out_dir, job["resources"]), inputs, params)

    if not isinstance(result, Artifact):
        raise RuntimeError(f"{opdef.name} returned {type(result).__name__}, not Artifact")
    declared = opdef.output_for([i.media for i in inputs])
    if result.media != declared:
        raise RuntimeError(f"{opdef.name} produced {result.media}, declared {declared}")
    path = result.path.resolve()
    if not path.is_file() or not path.is_relative_to(out_dir.resolve()):
        raise RuntimeError(f"{opdef.name} output must be a file inside the op work dir")
    summary = opdef.summary(result)
    if len(summary) > MAX_SUMMARY:
        summary = summary[: MAX_SUMMARY - 1] + "…"
    return {
        "ok": True,
        "path": str(path),
        "media": result.media.value,
        "ext": result.ext,
        "meta": dict(result.meta),
        "summary": summary,
    }


def _prepare_source(spec: dict[str, Any], work: Path, index: int) -> Any:
    """An input of a graph job, made ready for ffmpeg.

    Video/audio are probed here if their metadata is not already known. Images go through Pillow
    first (orientation applied, converted to sRGB, saved as a fresh PNG under a name we choose),
    so ffmpeg only ever sees a clean, predictable file and never the original.
    """
    from atelier.av.graph import Source

    media = MediaType(spec["media"])
    path, ext, meta = Path(spec["path"]), spec["ext"], dict(spec["meta"])
    if media is MediaType.IMAGE:
        from atelier.ops.image._io import load

        im = load(Artifact(path, media, ext, meta))
        clean = work / f"source{index}.png"
        im.save(clean, "PNG")
        return Source(clean, media, ".png", {"size": [im.width, im.height]})
    if media is MediaType.TEXT:
        return Source(path, media, ext, {})  # read by an op (captions); never given to ffmpeg
    if "video" not in meta and "audio" not in meta:
        from atelier.av.probe import probe_av

        meta = probe_av(path, ext)
    return Source(path, media, ext, meta)


def run_graph_job(job: dict[str, Any], work: Path) -> dict[str, Any]:
    """Run a fused chain of ffmpeg steps as ONE graph: one decode, one encode."""
    _apply_limits(job["limits"])
    from atelier.av.graph import Graph
    from atelier.av.render import render

    sources = [_prepare_source(s, work, i) for i, s in enumerate(job["inputs"])]
    g = Graph(sources, work)
    last: OpDef | None = None
    made: list[Any] = []  # the streams each step left behind, for steps that read them later
    for step in job["steps"]:
        opdef = _resolve(step["module"], step["qualname"])
        if opdef.graph is None:
            raise RuntimeError(f"{opdef.name} cannot run inside an ffmpeg graph")
        refs: list[int] = step["in"]  # >= 0: a file; < 0: the output of step (-1 - ref)
        if refs[0] >= 0:
            g.start(refs[0])  # a step reading a file picks up its streams
        else:
            g.restore(made[-1 - refs[0]])
        g.set_extras([r if r >= 0 else made[-1 - r] for r in refs[1:]])
        opdef.graph(g, opdef.params.model_validate(step["args"]))
        made.append(g.snapshot())
        last = opdef
    assert last is not None
    out_dir = work / "out"
    art = render(g, out_dir)
    summary = last.summary(art)
    if len(summary) > MAX_SUMMARY:
        summary = summary[: MAX_SUMMARY - 1] + "…"
    return {
        "ok": True,
        "path": str(art.path.resolve()),
        "media": art.media.value,
        "ext": art.ext,
        "meta": dict(art.meta),
        "summary": summary,
    }


def _warm() -> None:
    """Pay the import cost now, before any job (and so any untrusted input) arrives.

    Only the builtin ops. Heavy optional plugins (ML models) must not become builtin without
    revisiting this: every standby would load them.
    """
    from PIL import Image

    from atelier.ops.registry import load_builtin_ops

    load_builtin_ops()
    Image.init()  # register every format plugin now instead of on the first Image.open


def standby() -> int:
    """Warm up, then run exactly one job whose path arrives on stdin. Exit if none does."""
    _warm()
    line = sys.stdin.readline().strip()
    if not line:  # the parent went away, or the pool was shut down
        return 0
    job_path = Path(line)
    os.chdir(job_path.parent)
    return main([str(job_path)])


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args == ["--standby"]:
        return standby()
    job_path = Path(args[0])
    work = job_path.parent
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        if job.get("kind") == "graph":
            result, code = run_graph_job(job, work), 0
        elif job.get("kind") == "probe":
            _apply_limits(job["limits"])
            meta = probe(Path(job["path"]), MediaType(job["media"]), job.get("ext", ""))
            result, code = {"ok": True, "meta": meta}, 0
        else:
            result, code = run_job(job, work), 0
    except OpError as e:
        result, code = {"ok": False, "error": str(e)}, 1
    except MemoryError:
        result, code = {"ok": False, "error": "exceeded the memory limit"}, 1
    except OSError as e:
        if e.errno == errno.EFBIG:
            result, code = {"ok": False, "error": "exceeded the output size limit"}, 1
        else:
            result, code = {"ok": False, "error": f"internal error: {type(e).__name__}: {e}"}, 1
    except Exception as e:
        result, code = {"ok": False, "error": f"internal error: {type(e).__name__}: {e}"}, 1
    (work / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
