"""Running jobs. A worker claims a queued job, runs it in the organization's engine (each step
in its own sandboxed subprocess) and stores the outcome and the progress events.

A job ends in one of: succeeded, checks_failed (finished, but a check still fails), needs_input
(the agent asked a question), failed (an error, with its message) or cancelled (asked for
between two steps; a step already running is not interrupted).
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from atelier import links
from atelier.agent.client import MissingCredentials
from atelier.agent.config import DEFAULT_MODEL, AgentConfig
from atelier.agent.loop import AgentResult
from atelier.agent.loop import Job as AgentJob
from atelier.agent.loop import run_job as run_agent
from atelier.agent.plan_cache import PlanCache
from atelier.agent.telemetry import Telemetry
from atelier.agent.voice import MY_VOICE, VOICES, VoiceService
from atelier.api.core import services
from atelier.api.core.models import Job
from atelier.engine.engine import Engine
from atelier.engine.runner import JobResult, RunCancelled, StepFailed
from atelier.spec.errors import SpecInvalid

log = logging.getLogger(__name__)
ClientFactory = Callable[[], Any]


def _client_factory() -> ClientFactory:
    module, _, name = settings.ATELIER_MODEL_CLIENT.rpartition(".")
    factory: ClientFactory = getattr(importlib.import_module(module), name)
    return factory


def _factory(dotted: str) -> Callable[[], Any]:
    module, _, name = dotted.rpartition(".")
    factory: Callable[[], Any] = getattr(importlib.import_module(module), name)
    return factory


def _voice_factory() -> Callable[[], VoiceService | None]:
    module, _, name = settings.ATELIER_VOICE_CLIENT.rpartition(".")
    factory: Callable[[], VoiceService | None] = getattr(importlib.import_module(module), name)
    return factory


def _observer(job: Job) -> Callable[[str, dict[str, Any]], None]:
    def observe(kind: str, data: dict[str, Any]) -> None:
        if kind == "step_start" and Job.objects.filter(pk=job.pk, cancel_requested=True).exists():
            raise RunCancelled
        services.emit(job, kind, data)

    return observe


def _finish(job: Job, status: str, **fields: Any) -> Job:
    job.status = status
    job.finished_at = timezone.now()
    for name, value in fields.items():
        setattr(job, name, value)
    job.save()
    services.emit(job, "finished", {"status": status})
    return job


def _register_outputs(job: Job, engine: Engine, result: JobResult) -> None:
    for rep in result.outputs.values():
        services.register_asset(job.org, engine.store.info(rep.hash), origin=job)


def brand_kit_text(job: Job) -> str:
    """The organization's brand kit as text for the request (never for the system prompt)."""
    kit = job.org.brand_kit or {}
    if not kit:
        return ""
    lines = ["Brand kit (use it when the request is about our brand):"]
    for key in ("name", "tagline"):
        if kit.get(key):
            lines.append(f"- {key}: {kit[key]}")
    if kit.get("colors"):
        lines.append("- colors: " + ", ".join(f"{k} {v}" for k, v in kit["colors"].items()))
    if kit.get("fonts"):
        lines.append("- fonts: " + ", ".join(f"{k} {v}" for k, v in kit["fonts"].items()))
    for key in ("logo", "logo_dark"):
        if kit.get(key):
            lines.append(f"- {key}: {kit[key]} (an image asset you may use)")
    if kit.get("rules"):
        lines.append("Rules, limitations, and restrictions (strictly follow these):")
        for rule in kit["rules"]:
            lines.append(f"- {rule}")
    return "\n".join(lines)


def _run_spec(job: Job, engine: Engine) -> Job:
    result = engine.run(job.spec)
    _register_outputs(job, engine, result)
    status = Job.SUCCEEDED if result.passed else Job.CHECKS_FAILED
    return _finish(
        job,
        status,
        result=services.summarize_result(result),
        compute_seconds=result.seconds,
    )


def _agent_outcome(job: Job, engine: Engine, res: AgentResult) -> Job:
    body: dict[str, Any] = (
        services.summarize_result(res.engine_result) if res.engine_result is not None else {}
    )
    body.update(summary=res.summary, warnings=list(res.warnings), question=res.question)
    if res.title:
        body["title"] = res.title
    if res.credits:
        body["credits"] = list(res.credits)
    fields: dict[str, Any] = {"result": body, "spec": res.spec}
    if res.record is not None:
        fields["units"] = res.record.units()
        fields["compute_seconds"] = res.engine_result.seconds if res.engine_result else 0.0
    if res.engine_result is not None:
        _register_outputs(job, engine, res.engine_result)
    if res.status == "ok":
        return _finish(job, Job.SUCCEEDED, **fields)
    if res.status == "checks_failed":
        return _finish(job, Job.CHECKS_FAILED, **fields)
    if res.status == "needs_input":
        return _finish(job, Job.NEEDS_INPUT, **fields)
    reason = {
        "refused": "the request was declined",
        "budget": "this was too big to finish in one go. Try asking for fewer slides or "
        "pictures, or do it in steps (first the pictures, then the video)",
        "no_output": "nothing was produced",
    }.get(res.status, "the agent could not finish")
    return _finish(job, Job.FAILED, error=f"{reason}: {res.detail}".strip(": "), **fields)


def followup_context(job: Job) -> tuple[str, list[str]]:
    """For a follow-up ("refine this"): what the earlier request asked and produced, as text
    for the model, and the earlier outputs and inputs, which become assets of this request."""
    parent = job.parent
    if parent is None:
        return "", []
    result = parent.result or {}
    outputs = [o["handle"] for o in (result.get("outputs") or {}).values() if o.get("handle")]
    lines = ["Follow-up to an earlier request in this conversation."]
    lines.append(f"Earlier request: {parent.prompt or '(a pipeline spec, no prompt)'}")
    if result.get("summary"):
        lines.append(f"What you made then: {result['summary']}")
    if result.get("question"):
        lines.append(f"You asked then: {result['question']} (this request answers it)")
    if parent.error:
        lines.append(f"It failed then: {parent.error}")
    if outputs:
        lines.append(
            "Earlier result (edit this one unless asked to start over): " + ", ".join(outputs)
        )
    if parent.spec:
        spec = json.dumps(parent.spec, separators=(",", ":"), ensure_ascii=False)
        lines.append(f"Earlier spec: {spec[:3000]}")
    return "\n".join(lines), [*outputs, *parent.assets]


def fetch_links(job: Job, engine: Engine) -> tuple[str, list[str]]:
    """Download the files linked in the message (links.py) as assets of this job. Returns a
    note for the model (what each link became, or why it could not be used) and the handles."""
    urls = links.find_links(job.prompt)[: links.MAX_LINKS]
    if not urls:
        return "", []
    notes, handles = [], []
    for url in urls:
        services.emit(job, "link", {"url": url[:200], "state": "downloading"})
        work = engine.store.work_dir("link-")
        try:
            name = links.fetch(
                url,
                work / "download",
                max_bytes=services.upload_limit(job.org),
                allow_private=settings.ATELIER_LINKS_ALLOW_PRIVATE,
            )
            info = engine.store.put_file(work / "download", move=True)
            if not services.known_media(info.media.value):
                raise links.LinkError("that file is not a picture, video, sound or document")
            try:
                info, name = services.normalize_upload(engine, info, name)
            except services.ApiError as e:
                raise links.LinkError(e.message) from None
            if info.media.value in ("video", "audio") and "error" in engine.probe(info):
                raise links.LinkError("the downloaded file is damaged or incomplete")
            services.register_asset(job.org, info, filename=name, origin=job)
            handles.append(info.handle)
            notes.append(f"- {url} was downloaded: {engine.inspect(info)}")
            services.emit(job, "link", {"url": url[:200], "state": "done", "name": name})
        except links.LinkError as e:
            notes.append(f"- {url} could not be used: {e}. Tell the user in plain words.")
            services.emit(job, "link", {"url": url[:200], "state": "failed", "reason": str(e)})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return "Links in the message:\n" + "\n".join(notes), handles


def _voice_for(job: Job, engine: Engine) -> VoiceService | None:
    """The voice service, able to speak in the user's own voice when they recorded one."""
    service = _voice_factory()()
    mine = job.org.my_voice or {}
    if service is not None and mine.get("handle") and hasattr(service, "with_reference"):
        try:
            mp3 = engine.store.path(engine.store.resolve(mine["handle"]).hash).read_bytes()
        except Exception:  # the recording is gone; the other voices still work
            return service
        service = service.with_reference(mp3, str(mine.get("text") or ""))
    return service


def _run_prompt(job: Job, engine: Engine) -> Job:
    context, earlier = followup_context(job)
    link_note, linked = fetch_links(job, engine)
    prompt = f"{context}\n\nNow: {job.prompt}" if context else job.prompt
    if link_note:
        prompt = f"{prompt}\n\n{link_note}"
    mine = job.org.my_voice or {}
    if job.voice in VOICES or (job.voice == MY_VOICE and mine):
        label = "the user's own recorded voice" if job.voice == MY_VOICE else VOICES[job.voice]
        prompt += (
            f"\n\nVoice for any voiceover: {label} (voice {job.voice}); "
            "the user chose it, so use it unless they ask for another."
        )
    elif mine:
        prompt += "\n\nThe user has recorded their own voice: voice my_voice if they ask for it."
    kit = brand_kit_text(job)
    handles = list(dict.fromkeys([*job.assets, *linked, *earlier]))[:20]  # new files first
    if kit:
        prompt = f"{prompt}\n\n{kit}"
        handles += [
            h
            for k in ("logo", "logo_dark")
            if (h := (job.org.brand_kit or {}).get(k)) and h not in handles
        ]
    telemetry = Telemetry(engine.home / "telemetry")
    res = run_agent(
        AgentJob(prompt=prompt, assets=tuple(handles)),
        engine=engine,
        adapter=_client_factory()(),
        config=AgentConfig(model=settings.ATELIER_MODEL_NAME or DEFAULT_MODEL),
        telemetry=telemetry,
        plan_cache=PlanCache(engine.home / "plans"),
        voice=_voice_for(job, engine),
        pictures=_factory(settings.ATELIER_PICTURES_CLIENT)(),
        music=_factory(settings.ATELIER_MUSIC_CLIENT)(),
    )
    for handle, name in res.generated:  # voiceovers, found and made pictures: the user's too
        services.register_asset(job.org, engine.store.resolve(handle), filename=name, origin=job)
    return _agent_outcome(job, engine, res)


def execute(job_id: uuid.UUID) -> Job:
    """Claim and run one job. Safe to call from several workers at once: only one wins."""
    now = timezone.now()
    claimed = Job.objects.filter(pk=job_id, status=Job.QUEUED).update(
        status=Job.RUNNING, started_at=now, heartbeat_at=now
    )
    job = Job.objects.select_related("org", "recipe", "parent").get(pk=job_id)
    if not claimed:
        return job  # someone else has it, or it was cancelled while it waited
    services.emit(job, "started", {"kind": job.kind})
    engine = dataclasses.replace(services.engine_for(job.org), observer=_observer(job))
    try:
        return _run_spec(job, engine) if job.kind == "spec" else _run_prompt(job, engine)
    except RunCancelled:
        return _finish(job, Job.CANCELLED, error="cancelled")
    except SpecInvalid as e:
        return _finish(
            job, Job.FAILED, error="invalid spec", result={"errors": [x.line() for x in e.errors]}
        )
    except StepFailed as e:
        return _finish(job, Job.FAILED, error=str(e))
    except MissingCredentials:
        return _finish(job, Job.FAILED, error="the model service is not configured on this server")
    except Exception as e:  # a bug must fail the job, not take the worker down
        log.exception("job %s crashed", job.pk)
        return _finish(job, Job.FAILED, error=f"internal error: {_describe(e)}")


def _describe(e: BaseException) -> str:
    """Name the exception and its root cause, e.g. a model client's APIConnectionError caused by
    ConnectionRefusedError, so a failed job is diagnosable from the API. Only an OS error's fixed
    `strerror` text is included; other messages may carry internals and stay in the log."""
    root: BaseException | None = e
    seen = set()
    while root is not None and id(root) not in seen:
        seen.add(id(root))
        nxt = root.__cause__ or root.__context__
        if nxt is None:
            break
        root = nxt
    if root is None or root is e:
        return type(e).__name__
    why = f" ({root.strerror})" if isinstance(root, OSError) and root.strerror else ""
    return f"{type(e).__name__} caused by {type(root).__name__}{why}"


def reap_stale() -> int:
    """Fail running jobs whose worker went silent (crashed or was killed)."""
    cutoff = timezone.now() - timedelta(seconds=settings.ATELIER_STALE_JOB_SECONDS)
    lost = list(Job.objects.filter(status=Job.RUNNING, heartbeat_at__lt=cutoff))
    for job in lost:
        _finish(job, Job.FAILED, error="the worker stopped responding; run the job again")
    return len(lost)


def run_forever(*, poll: float = 0.5, once: bool = False, max_jobs: int | None = None) -> int:
    """Claim and run queued jobs, oldest first, until stopped (or, with `once`, until none wait)."""
    done = 0
    last_reap = 0.0
    while max_jobs is None or done < max_jobs:
        if time.monotonic() - last_reap > 60:
            reap_stale()
            last_reap = time.monotonic()
        nxt = (
            Job.objects.filter(status=Job.QUEUED)
            .order_by("created_at")
            .values_list("pk", flat=True)
            .first()
        )
        if nxt is None:
            if once:
                break
            time.sleep(poll)
            continue
        execute(nxt)
        done += 1
    return done
