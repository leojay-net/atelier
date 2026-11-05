"""Planner and runner.

`plan` computes step keys and finds cached steps without running anything. `run` walks
the canonical spec in topological order and executes each uncached step in its own
worker subprocess with CPU, memory, output-size and wall-time limits. The process is fresh
and single-use; it may have been started ahead of time (`pool.py`) to hide its import cost.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from atelier.checks import CheckResult, Status, run_checks
from atelier.engine.cache import CachedStep, ResultCache, group_key, step_key
from atelier.engine.pool import get_pool
from atelier.engine.probe import PROBE_VERSION
from atelier.engine.store import LocalStore
from atelier.media import AssetInfo, MediaType
from atelier.resources import ResourceUnavailable, resolve
from atelier.spec.model import CanonicalSpec, CanonicalStep

log = logging.getLogger(__name__)

# Workers get a minimal environment: no API keys or cloud credentials (docs/10-security.md).
_ENV_PASSTHROUGH = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH", "SYSTEMROOT", "TMPDIR",
    "ATELIER_FFMPEG", "ATELIER_FFPROBE", "ATELIER_SOFFICE",
)  # fmt: skip
# MALLOC_ARENA_MAX: glibc reserves a 64 MB arena per thread, and RLIMIT_AS counts reservations,
# so a fused ffmpeg render (dozens of filter and x264 threads) hit the 4 GB cap with ~1 GB really
# in use ("Cannot allocate memory" on a 10-shot portrait reel with captions, live 2026-11-05).
_ENV_FIXED = {
    "TZ": "UTC", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "MALLOC_ARENA_MAX": "2",
}  # fmt: skip


@dataclass(frozen=True)
class RunLimits:
    cpu_seconds: int = 300
    wall_seconds: float = 600
    memory_bytes: int = 4 << 30
    output_bytes: int = 8 << 30


@dataclass(frozen=True)
class StepReport:
    id: str
    op: str
    key: str
    hash: str
    media: MediaType
    ext: str
    size: int
    summary: str
    cached: bool
    seconds: float
    meta: dict[str, Any]
    fused: tuple[str, ...] = ()  # ids of the earlier steps folded into this run (one encode)

    @property
    def handle(self) -> str:
        return f"asset:{self.hash}"


@dataclass(frozen=True)
class JobResult:
    spec: CanonicalSpec
    steps: tuple[StepReport, ...]
    outputs: dict[str, StepReport]
    seconds: float
    checks: dict[str, list[CheckResult]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(r.status is not Status.FAIL for rs in self.checks.values() for r in rs)

    @property
    def reused(self) -> int:
        return sum(s.cached for s in self.steps)

    @property
    def fused_steps(self) -> int:
        """How many steps ran inside another step's ffmpeg graph instead of on their own."""
        return sum(len(s.fused) for s in self.steps)


@dataclass(frozen=True)
class PlannedStep:
    step: CanonicalStep  # the last step of its group
    key: str | None  # None until upstream outputs are known
    cached: bool
    fused: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    spec: CanonicalSpec
    steps: tuple[PlannedStep, ...]

    @property
    def cached(self) -> int:
        return sum(s.cached for s in self.steps)


# Progress hook: `observer(kind, data)`. Kinds: "plan" (groups, steps), "step_start" (step, op,
# index, groups), "step_done" (the step's report as plain data). It may raise `RunCancelled` to
# stop the run between two steps; a step that is already running is not interrupted.
Observer = Callable[[str, dict[str, Any]], None]


class RunCancelled(Exception):
    """Raised by an observer to stop a run at the next step boundary."""


class StepFailed(Exception):
    def __init__(self, step: CanonicalStep, message: str) -> None:
        super().__init__(f"step {step.id!r} ({step.op.name}) failed: {message}")
        self.step_id = step.id
        self.op = step.op.name
        self.reason = message


def _resources(step: CanonicalStep, media: MediaType) -> dict[str, str]:
    """Pinned resources for this step given its main input's media (an image `export` needs
    Pillow, a video `export` needs ffmpeg, and neither requires the other's tools)."""
    try:
        return resolve(step.op.resources_for(media))
    except ResourceUnavailable as e:
        raise StepFailed(step, str(e)) from None


def _lookup(cache: ResultCache, store: LocalStore, key: str) -> CachedStep | None:
    hit = cache.get(key)
    return hit if hit is not None and store.has(hit.hash) else None


@dataclass(frozen=True)
class Group:
    """One unit of execution: a single step, or a chain of steps fused into one ffmpeg graph."""

    steps: tuple[CanonicalStep, ...]
    graph: bool
    inputs: tuple[str, ...]  # references outside the group, in order of first use

    @property
    def last(self) -> CanonicalStep:
        return self.steps[-1]


def _types(spec: CanonicalSpec) -> dict[str, MediaType]:
    types = {name: info.media for name, info in spec.inputs.items()}
    for step in spec.steps:
        types[step.id] = step.op.output_for([types[r] for r in step.refs])
    return types


def fuse(spec: CanonicalSpec) -> list[Group]:
    """Split the spec into execution groups, fusing ffmpeg steps into one graph per output.

    A graph step absorbs the group that produced any of its inputs when that group is a graph
    group whose last step this is the only consumer of, and which is not a requested output. So
    a chain fuses, and so does a tree: `concat` over three `trim`s is one graph. Everything else
    (a shared intermediate, an image step, a requested output) runs on its own exactly as before.
    Groups run in order of their last step, so any extra input of a fused step (a logo made by
    a step listed later than the group's first step) is ready by then.
    """
    types = _types(spec)
    position = {s.id: i for i, s in enumerate(spec.steps)}
    consumers = Counter(r for s in spec.steps for r in s.refs)
    outputs = set(spec.outputs)
    by_last: dict[str, list[CanonicalStep]] = {}  # every group, keyed by its last step
    open_graphs: set[str] = set()  # the graph groups (a step outside them cannot be absorbed)
    for step in spec.steps:
        graph = step.op.fuses(types[step.refs[0]])
        members = [step]
        if graph:
            for ref in dict.fromkeys(step.refs):
                if ref in open_graphs and consumers[ref] == 1 and ref not in outputs:
                    open_graphs.discard(ref)
                    members.extend(by_last.pop(ref))
            members.sort(key=lambda m: position[m.id])
            open_graphs.add(step.id)
        by_last[step.id] = members
    groups = []
    for last_id in sorted(by_last, key=position.__getitem__):
        chain = by_last[last_id]
        if last_id in open_graphs:
            # a graph addresses its inputs by slot, so each distinct input appears once
            inside = {s.id for s in chain}
            external = tuple(dict.fromkeys(r for s in chain for r in s.refs if r not in inside))
            groups.append(Group(tuple(chain), True, external))
        else:
            # an ordinary step gets its inputs exactly as written, repeats included (a grid may
            # use one photo for every cell)
            groups.append(Group(tuple(chain), False, tuple(chain[0].refs)))
    return groups


def _refs(group: Group) -> list[list[int]]:
    """Each step's inputs as a graph addresses them: a slot >= 0 for a file passed in, or
    `-1 - k` for the output of step k of the same group."""
    slots = {name: i for i, name in enumerate(group.inputs)}
    inside = {s.id: k for k, s in enumerate(group.steps)}
    return [[-1 - inside[r] if r in inside else slots[r] for r in s.refs] for s in group.steps]


def _group_resources(group: Group, types: dict[str, MediaType]) -> dict[str, str]:
    """Pinned resources for the whole group (the ffmpeg build is in a fused group's key)."""
    names: set[str] = set()
    for step in group.steps:
        for media in {types[r] for r in step.refs}:  # a logo made by Pillow counts too
            names.update(step.op.resources_for(media))
    try:
        return resolve(names)
    except ResourceUnavailable as e:
        raise StepFailed(group.last, str(e)) from None


def _key(group: Group, hashes: list[str], resources: dict[str, str]) -> str:
    if not group.graph:
        (step,) = group.steps
        return step_key(step.op, step.args, hashes, resources)
    steps = [(s.op, s.args, refs) for s, refs in zip(group.steps, _refs(group), strict=True)]
    return group_key(steps, hashes, resources)


def plan(spec: CanonicalSpec, *, store: LocalStore, cache: ResultCache) -> Plan:
    known = {name: info.hash for name, info in spec.inputs.items()}
    types = _types(spec)
    planned = []
    for group in fuse(spec):
        key = None
        hit = None
        if all(r in known for r in group.inputs):
            key = _key(group, [known[r] for r in group.inputs], _group_resources(group, types))
            hit = _lookup(cache, store, key)
            if hit is not None:
                known[group.last.id] = hit.hash
        fused = tuple(s.id for s in group.steps[:-1])
        planned.append(PlannedStep(group.last, key, hit is not None, fused))
    return Plan(spec, tuple(planned))


def run(
    spec: CanonicalSpec,
    *,
    store: LocalStore,
    cache: ResultCache,
    limits: RunLimits = RunLimits(),
    observer: Observer | None = None,
) -> JobResult:
    started = time.perf_counter()
    assets: dict[str, tuple[AssetInfo, dict[str, Any]]] = {
        name: (info, {}) for name, info in spec.inputs.items()
    }
    types = _types(spec)
    reports: dict[str, StepReport] = {}
    groups = fuse(spec)
    if observer is not None:
        observer("plan", {"groups": len(groups), "steps": len(spec.steps)})
    for index, group in enumerate(groups, start=1):
        t0 = time.perf_counter()
        last = group.last
        if observer is not None:
            observer(
                "step_start",
                {
                    "step": last.id,
                    "op": last.op.name,
                    "index": index,
                    "groups": len(groups),
                    "ops": [s.op.name for s in group.steps],  # every op fused into this run
                },
            )
        inputs = [assets[r] for r in group.inputs]
        resources = _group_resources(group, types)
        key = _key(group, [i.hash for i, _ in inputs], resources)
        hit = _lookup(cache, store, key)
        cached = hit is not None
        if hit is None:
            hit = _execute(group, inputs, store, limits, resources)
            cache.put(key, hit)
        reports[last.id] = StepReport(
            id=last.id,
            op=last.op.name,
            key=key,
            hash=hit.hash,
            media=hit.media,
            ext=hit.ext,
            size=hit.size,
            summary=hit.summary,
            cached=cached,
            seconds=time.perf_counter() - t0,
            meta=hit.meta,
            fused=tuple(s.id for s in group.steps[:-1]),
        )
        assets[last.id] = (AssetInfo(hit.hash, hit.media, hit.size, hit.ext), hit.meta)
        if observer is not None:
            done = reports[last.id]
            observer(
                "step_done",
                {
                    "step": done.id, "op": done.op, "index": index, "groups": len(groups),
                    "cached": done.cached, "seconds": round(done.seconds, 3),
                    "summary": done.summary, "handle": done.handle,
                },
            )  # fmt: skip
    outputs = {o: reports[o] for o in spec.outputs}
    return JobResult(
        spec=spec,
        steps=tuple(reports[g.last.id] for g in fuse(spec)),
        outputs=outputs,
        seconds=time.perf_counter() - started,
        checks={o: run_checks(spec.checks, r.media, r.meta) for o, r in outputs.items()},
    )


def _worker_env() -> dict[str, str]:
    from atelier import models

    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    return env | _ENV_FIXED | {"ATELIER_MODELS": str(models.models_dir())}  # resolved here, once


class WorkerFailed(Exception):
    pass


def _spawn(work: Path, job: dict[str, Any], limits: RunLimits, label: str) -> dict[str, Any]:
    """Run one worker job in `work` and return its result.json, or raise WorkerFailed."""
    job = {**job, "limits": {k: int(v) for k, v in asdict(limits).items() if k != "wall_seconds"}}
    job_path = work / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    pool = get_pool()
    proc = pool.acquire(_worker_env())  # a warm standby if one is ready, else a fresh process
    try:
        _, stderr = proc.communicate(
            input=f"{job_path.resolve()}\n".encode(), timeout=limits.wall_seconds
        )
    except subprocess.TimeoutExpired:
        pool.kill(proc)
        raise WorkerFailed(f"timed out (wall-time limit {limits.wall_seconds:g}s)") from None
    finally:
        pool.release()
    if stderr:
        log.debug("worker stderr for %s: %s", label, stderr[-4000:])
    result = _read_result(work / "result.json")
    if proc.returncode != 0 or not result or not result.get("ok"):
        raise WorkerFailed(_failure(proc.returncode, result, limits))
    return result


def _job_for(
    group: Group,
    inputs: list[tuple[AssetInfo, dict[str, Any]]],
    store: LocalStore,
    resources: dict[str, str],
) -> dict[str, Any]:
    described = [
        {
            "path": str(store.path(info.hash)),
            "media": info.media.value,
            "ext": info.ext,
            "meta": meta,
        }
        for info, meta in inputs
    ]
    if not group.graph:
        (step,) = group.steps
        return {
            "module": step.op.fn.__module__,
            "qualname": step.op.fn.__qualname__,
            "args": step.args,
            "resources": resources,
            "inputs": described,
        }
    return {
        "kind": "graph",
        "resources": resources,
        "inputs": described,
        "steps": [
            {
                "module": step.op.fn.__module__,
                "qualname": step.op.fn.__qualname__,
                "args": step.args,
                "in": refs,
            }
            for step, refs in zip(group.steps, _refs(group), strict=True)
        ],
    }


def _execute(
    group: Group,
    inputs: list[tuple[AssetInfo, dict[str, Any]]],
    store: LocalStore,
    limits: RunLimits,
    resources: dict[str, str],
) -> CachedStep:
    step = group.last
    work = store.work_dir(f"{step.id}-")
    try:
        (work / "out").mkdir()
        try:
            result = _spawn(work, _job_for(group, inputs, store, resources), limits, step.id)
        except WorkerFailed as e:
            raise StepFailed(step, str(e)) from None
        out = Path(result["path"])
        if not out.resolve().is_relative_to((work / "out").resolve()):
            raise StepFailed(step, "internal error: output outside the work dir")
        info = store.put_file(out, media=MediaType(result["media"]), ext=result["ext"], move=True)
        return CachedStep(
            info.hash, info.media, info.ext, info.size, result["summary"], result["meta"]
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def probe(
    info: AssetInfo, *, store: LocalStore, cache: ResultCache, limits: RunLimits
) -> dict[str, Any]:
    """Metadata for an asset, parsed in a sandboxed worker (inputs are untrusted) and cached."""
    key = f"probe-{PROBE_VERSION}-{info.hash}"
    if (hit := cache.get_raw(key)) is not None:
        return hit
    work = store.work_dir("probe-")
    try:
        job = {
            "kind": "probe",
            "path": str(store.path(info.hash)),
            "media": info.media.value,
            "ext": info.ext,
        }
        try:
            meta: dict[str, Any] = _spawn(work, job, limits, f"probe {info.short}")["meta"]
        except WorkerFailed as e:
            meta = {"error": str(e)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if "error" not in meta:
        cache.put_raw(key, meta)
    return meta


def _read_result(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _failure(returncode: int, result: dict[str, Any] | None, limits: RunLimits) -> str:
    if result and result.get("error"):
        return str(result["error"])
    if returncode < 0:
        sig = -returncode
        if sig == getattr(signal, "SIGXCPU", None):
            return f"exceeded the CPU limit ({limits.cpu_seconds}s)"
        if sig == signal.SIGKILL:
            return "worker was killed (likely the memory limit)"
        return f"worker died from signal {signal.Signals(sig).name}"
    return f"worker crashed (exit code {returncode})"
