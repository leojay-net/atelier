"""The JSON API (Django Ninja), mounted at /api/v1/.

Every endpoint is scoped to the organization of the API key. Long work never happens in a
request: `POST /jobs` records a job and enqueues it; clients read its state, its events (polled
or streamed) and its outputs.
"""

from __future__ import annotations

import contextlib
import copy
import importlib
import mimetypes
import re
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpRequest, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from ninja import Field, File, Form, NinjaAPI, Schema, Status
from ninja.files import UploadedFile

from atelier.agent.voice import ACCENTS, DEFAULT_VOICE, MY_VOICE, PASSAGE, PEOPLE, VOICES
from atelier.api.core import accounts, edits, services
from atelier.api.core.auth import ApiKeyAuth
from atelier.api.core.edits import EditIn
from atelier.api.core.models import Asset, Character, Job, Org, SavedRecipe
from atelier.api.core.queue import enqueue
from atelier.api.core.schemas import (
    BrandKitIn,
    CharacterEdit,
    CharacterIn,
    ChatIn,
    JobIn,
    LoginIn,
    RecipeIn,
    SaveRecipeIn,
    SignupIn,
    SpecIn,
)
from atelier.api.core.services import ApiError
from atelier.engine.engine import Engine, format_plan
from atelier.media import AssetNotFound, HandleError, MediaType
from atelier.ops.catalogue import describe_name, full_catalogue
from atelier.spec.errors import SpecInvalid
from atelier.spec.model import CanonicalSpec
from atelier.spec.types import parse_color

api = NinjaAPI(title="Atelier", version="1.0", urls_namespace="atelier", auth=ApiKeyAuth())


@api.exception_handler(ApiError)
def _api_error(request: HttpRequest, exc: ApiError) -> HttpResponse:
    body: dict[str, Any] = {"error": exc.code, "message": exc.message}
    if exc.errors:
        body["errors"] = exc.errors
    return api.create_response(request, body, status=exc.status)


def _org(request: HttpRequest) -> Org:
    org: Org = request.org  # type: ignore[attr-defined]
    return org


# -- who am I, what can be done ----------------------------------------------------------------


@api.get("/me")
def me(request: HttpRequest) -> dict[str, Any]:
    org = _org(request)
    key = request.api_key  # type: ignore[attr-defined]
    return {
        "org": {"slug": org.slug, "name": org.name},
        "key": {"name": key.name, "prefix": key.prefix},
        "user": {"email": key.user.email} if key.user_id else None,
    }


# -- accounts (the web UI's sign-up / sign-in; see accounts.py) ---------------------------------


@api.post("/auth/signup", auth=None, response={201: dict[str, Any]})
def signup(request: HttpRequest, body: SignupIn) -> Status[dict[str, Any]]:
    return Status(201, accounts.signup(body.email, body.password, body.password_confirm))


@api.post("/auth/login", auth=None)
def login(request: HttpRequest, body: LoginIn) -> dict[str, Any]:
    return accounts.login(body.email, body.password)


@api.post("/auth/logout", response={204: None})
def logout(request: HttpRequest) -> Status[None]:
    accounts.logout(request.api_key)  # type: ignore[attr-defined]
    return Status(204, None)


@api.get("/ops")
def ops(request: HttpRequest) -> dict[str, Any]:
    """The catalogue of ops, presets and recipes, in the compact form the agent reads."""
    return {"catalogue": full_catalogue(services.engine_for(_org(request)).registry)}


@api.get("/ops/{name}")
def op_detail(request: HttpRequest, name: str) -> dict[str, Any]:
    text = describe_name(services.engine_for(_org(request)).registry, name)
    if text is None:
        raise ApiError(404, "not_found", f"no op, preset or recipe named {name!r}")
    return {"name": name, "description": text}


def _spec_errors(exc: SpecInvalid) -> list[str]:
    return [e.line() for e in exc.errors]


@api.post("/validate")
def validate(request: HttpRequest, body: SpecIn) -> dict[str, Any]:
    """Check a spec and say what running it would do, without running anything."""
    engine = services.engine_for(_org(request))
    try:
        plan = engine.plan(body.spec)
    except SpecInvalid as e:
        return {"valid": False, "errors": _spec_errors(e)}
    return {
        "valid": True,
        "steps": len(plan.spec.steps),
        "runs": len(plan.steps),
        "cached": plan.cached,
        "plan": format_plan(plan),
    }


# -- assets -------------------------------------------------------------------------------------


def _resolve(engine: Engine, handle: str) -> Any:
    try:
        return engine.store.resolve(handle)
    except (HandleError, AssetNotFound):
        raise ApiError(404, "unknown_asset", f"no asset {handle!r} in this organization") from None


@api.post("/assets", response={201: dict[str, Any]})
def upload(request: HttpRequest, file: File[UploadedFile]) -> Status[dict[str, Any]]:
    org = _org(request)
    limit = services.upload_limit(org)
    if file.size is not None and file.size > limit:
        raise ApiError(413, "too_large", f"the file is larger than {limit // 2**20} MB")
    engine = services.engine_for(org)
    with tempfile.TemporaryDirectory(dir=engine.store.tmp) as tmp:
        target = Path(tmp) / "upload"
        written = 0
        with target.open("wb") as out:
            for chunk in file.chunks():
                written += len(chunk)
                if written > limit:
                    raise ApiError(413, "too_large", f"the file is larger than {limit // 2**20} MB")
                out.write(chunk)
        if written == 0:
            raise ApiError(400, "empty_file", "the file is empty")
        info = engine.store.put_file(target)
    if info.media in (MediaType.VIDEO, MediaType.AUDIO):
        meta = engine.probe(info)
        if "error" in meta:  # truncated, half-downloaded or not really media: say so now
            raise ApiError(
                415,
                "unreadable_media",
                f"this {info.media.value} file cannot be read ({meta['error']}); "
                "it may be incomplete or damaged",
            )
    if not services.known_media(info.media.value):
        raise ApiError(
            415,
            "unsupported_type",
            "this kind of file is not supported (images, video, audio, PDF, Word, Excel, "
            "PowerPoint and plain text are)",
        )
    info, name = services.normalize_upload(engine, info, file.name or "")
    asset = services.register_asset(org, info, name)
    return Status(201, services.asset_dict(asset))


@api.get("/assets")
def list_assets(request: HttpRequest, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    qs = Asset.objects.filter(org=_org(request)).order_by("-created_at")
    limit = max(1, min(limit, 200))
    return {
        "count": qs.count(),
        "items": [services.asset_dict(a) for a in qs[offset : offset + limit]],
    }


@api.get("/library")
def library(
    request: HttpRequest,
    section: str = "created",
    media: str = "all",
    limit: int = 60,
    offset: int = 0,
) -> dict[str, Any]:
    """The web UI's Library: created / uploaded / extras, filterable by media."""
    if section not in ("created", "uploaded", "extras"):
        raise ApiError(422, "bad_request", "section must be created, uploaded or extras")
    limit = max(1, min(limit, 200))
    return services.library(_org(request), section, media, limit, max(0, offset))


@api.get("/voices")
def voices(request: HttpRequest) -> dict[str, Any]:
    """The voiceover voices a user can choose: an accent, each with a woman's and a man's."""
    return {
        "default": DEFAULT_VOICE,
        "mine": bool((_org(request).my_voice or {}).get("handle")),
        "accents": [
            {"id": a, "label": label, "voices": [{"id": f"{a}_{g}", "label": g} for g in PEOPLE]}
            for a, (label, _) in ACCENTS.items()
        ],
    }


@api.patch("/conversations/{job_id}")
def rename_conversation(request: HttpRequest, job_id: str, body: ChatIn) -> dict[str, Any]:
    """Name a chat (any job in it). The name wins over the AI's titles from then on."""
    end = services.latest_in_thread(_job(request, job_id))
    end.chat_title = " ".join(body.title.split())[:120]
    end.save(update_fields=["chat_title"])
    return {"id": str(end.id), "title": services.thread_title(end)}


@api.delete("/conversations/{job_id}")
def delete_conversation(request: HttpRequest, job_id: str, files: bool = False) -> dict[str, Any]:
    """Remove a chat from the list (every message in it). With `files`, also delete what it
    made (results, and the voiceovers, music and pictures made or found for it); otherwise
    they stay in the Library. The user's own uploads are always kept."""
    org = _org(request)
    chain = services.thread_jobs(services.latest_in_thread(_job(request, job_id)))
    removed = services.delete_chat_files(org, chain) if files else 0
    Job.objects.filter(pk__in=[j.pk for j in chain]).update(chat_deleted=True)
    return {"deleted": len(chain), "files_deleted": removed}


# -- the user's own voice ---------------------------------------------------------------------

MY_VOICE_SECONDS = (4.0, 30.0)  # shortest and longest recording kept


@api.get("/my-voice")
def get_my_voice(request: HttpRequest) -> dict[str, Any]:
    mine = _org(request).my_voice or {}
    return {
        "recorded": bool(mine.get("handle")),
        "created_at": mine.get("created_at"),
        "seconds": mine.get("seconds"),
        "passage": PASSAGE,
    }


@api.get("/my-voice/audio")
def my_voice_audio(request: HttpRequest) -> FileResponse:
    org = _org(request)
    mine = org.my_voice or {}
    if not mine.get("handle"):
        raise ApiError(404, "not_found", "no recorded voice")
    engine = services.engine_for(org)
    path = engine.store.path(engine.store.resolve(mine["handle"]).hash)
    return FileResponse(path.open("rb"), content_type="audio/mpeg")


@api.post("/my-voice")
def put_my_voice(
    request: HttpRequest,
    file: File[UploadedFile],
    consent: Form[bool] = False,
    read_passage: Form[bool] = True,
) -> dict[str, Any]:
    """Save a recording of the user's own voice (theirs, with their consent) as the reference
    for `my_voice`. It is cleaned up in the sandbox: sound only, at most 30 s, as MP3."""
    org = _org(request)
    if not consent:
        raise ApiError(422, "consent_needed", "confirm that this is your own voice")
    engine = services.engine_for(org)
    with tempfile.TemporaryDirectory(dir=engine.store.tmp) as tmp:
        target = Path(tmp) / "voice"
        written = 0
        with target.open("wb") as out:
            for chunk in file.chunks():
                written += len(chunk)
                if written > 25 << 20:
                    raise ApiError(413, "too_large", "keep the recording under 30 seconds")
                out.write(chunk)
        if written == 0:
            raise ApiError(400, "empty_file", "the recording is empty")
        info = engine.store.put_file(target)
    if info.media not in (MediaType.AUDIO, MediaType.VIDEO):
        raise ApiError(415, "unsupported_type", "that is not a sound recording")
    meta = engine.probe(info)
    seconds = float(meta.get("duration") or 0.0)
    if "error" in meta or not meta.get("audio"):
        raise ApiError(415, "unreadable_media", "that recording has no sound we can read")
    if seconds < MY_VOICE_SECONDS[0]:
        raise ApiError(422, "too_short", "record at least 5 seconds (read the whole passage)")
    end = min(seconds, MY_VOICE_SECONDS[1])
    steps: list[dict[str, Any]] = []
    if info.media is MediaType.VIDEO:
        steps.append({"id": "a", "op": "audio.extract", "args": {}})
    steps += [
        {"id": "t", "op": "audio.trim", "args": {"start": 0, "end": round(end - 0.01, 2)}},
        {"id": "out", "op": "export", "args": {"preset": "podcast_mp3"}},
    ]
    prev = "v"
    for step in steps:
        step["in"], prev = prev, step["id"]
    try:
        result = engine.run(
            {"version": 1, "inputs": {"v": info.handle}, "steps": steps, "outputs": ["out"]}
        )
    except Exception:
        raise ApiError(422, "unreadable_media", "that recording could not be read") from None
    org.my_voice = {
        "handle": result.outputs["out"].handle,
        "text": PASSAGE if read_passage else "",
        "seconds": round(end, 1),
        "created_at": timezone.now().isoformat(),
    }
    org.save(update_fields=["my_voice"])
    return get_my_voice(request)


@api.delete("/my-voice")
def delete_my_voice(request: HttpRequest) -> dict[str, Any]:
    org = _org(request)
    mine = org.my_voice or {}
    if mine.get("handle"):
        engine = services.engine_for(org)
        with contextlib.suppress(Exception):  # already gone is fine
            engine.store.path(engine.store.resolve(mine["handle"]).hash).unlink(missing_ok=True)
    org.my_voice = {}
    org.save(update_fields=["my_voice"])
    return {"recorded": False}


@api.get("/conversations")
def conversations(request: HttpRequest, limit: int = 50) -> dict[str, Any]:
    """Recent chats, newest first, each by its latest job (open it at /studio/<id>)."""
    return {"items": services.conversations(_org(request), max(1, min(limit, 100)))}


def _asset(request: HttpRequest, handle: str) -> Asset:
    digest = handle.removeprefix("asset:")
    asset = (
        Asset.objects.filter(org=_org(request), hash__startswith=digest).first()
        if len(digest) >= 8
        else None
    )
    if asset is None:
        raise ApiError(404, "unknown_asset", f"no asset {handle!r} in this organization")
    return asset


@api.get("/assets/{handle}")
def asset_detail(request: HttpRequest, handle: str) -> dict[str, Any]:
    asset = _asset(request, handle)
    engine = services.engine_for(_org(request))
    info = engine.store.resolve(asset.handle)
    return {
        **services.asset_dict(asset),
        "description": engine.inspect(info, full=True),
        "meta": engine.probe(info),
    }


_INLINE = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "video/mp4",
    "audio/mpeg",
    "application/pdf",
}


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _ranged(path: Path, kind: str, header: str) -> HttpResponse | None:
    """A 206 for `Range: bytes=a-b` (one range), streamed from the file; None to send it all.
    Browsers need this to seek in a video or audio file (the editor's playhead, the player)."""
    match = _RANGE.match(header.strip())
    if not match or match.groups() == ("", ""):
        return None
    size = path.stat().st_size
    first, last = match.groups()
    if first == "":  # the last N bytes
        start, end = max(0, size - int(last)), size - 1
    else:
        start, end = int(first), min(size - 1, int(last)) if last else size - 1
    if start >= size or start > end:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        return response

    def chunks() -> Any:
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                data = f.read(min(left, 1 << 20))
                if not data:
                    break
                left -= len(data)
                yield data

    response = StreamingHttpResponse(chunks(), status=206, content_type=kind)
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Content-Length"] = str(end - start + 1)
    return response


@api.get("/assets/{handle}/content")
def asset_content(request: HttpRequest, handle: str) -> HttpResponse:
    asset = _asset(request, handle)
    engine = services.engine_for(_org(request))
    path = engine.store.path(asset.hash)
    kind = mimetypes.guess_type(f"x{asset.ext}")[0] or "application/octet-stream"
    inline = kind in _INLINE
    response: HttpResponse = _ranged(path, kind, request.headers.get("Range", "")) or FileResponse(
        path.open("rb"), content_type=kind
    )  # type: ignore[assignment]
    response["Accept-Ranges"] = "bytes"
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{asset.filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return response


# -- jobs ---------------------------------------------------------------------------------------


def _plain_spec(spec: CanonicalSpec) -> dict[str, Any]:
    """A canonical spec as an input the validator accepts again (no op versions or origin)."""
    data = spec.to_json()
    data.pop("origin", None)
    for step in data["steps"]:
        step.pop("op_version", None)
    return data


def _bind(template: dict[str, Any], inputs: dict[str, str]) -> dict[str, Any]:
    spec = copy.deepcopy(template)
    have = spec.get("inputs", {})
    unknown = sorted(set(inputs) - set(have))
    if unknown:
        raise ApiError(
            422,
            "unknown_input",
            f"the recipe has no input {', '.join(unknown)}; it has: {', '.join(sorted(have))}",
        )
    spec["inputs"] = {**have, **inputs}
    return spec


def _validated(engine: Engine, spec: Any) -> CanonicalSpec:
    try:
        return engine.validate(spec)
    except SpecInvalid as e:
        raise ApiError(422, "invalid_spec", "the spec is not valid", _spec_errors(e)) from None


@api.post("/jobs", response={202: dict[str, Any]})
def create_job(request: HttpRequest, body: JobIn) -> Status[dict[str, Any]]:
    org = _org(request)
    given = [x for x in (body.spec, body.prompt, body.recipe_id) if x is not None]
    if len(given) != 1:
        raise ApiError(422, "bad_request", "give exactly one of spec, prompt or recipe_id")
    engine = services.engine_for(org)
    recipe: SavedRecipe | None = None
    spec = body.spec
    if body.recipe_id is not None:
        recipe = SavedRecipe.objects.filter(pk=body.recipe_id, org=org, archived=False).first()
        if recipe is None:
            raise ApiError(404, "not_found", f"no recipe {body.recipe_id} in this organization")
        spec = _bind(recipe.template, body.inputs)
    if spec is not None:
        _validated(engine, spec)
        kind, prompt, handles = "spec", "", []
    else:
        assert body.prompt is not None
        if not body.prompt.strip():
            raise ApiError(422, "bad_request", "the prompt is empty")
        kind, prompt = "prompt", body.prompt.strip()
        handles = [_resolve(engine, h).handle for h in body.assets]
    parent: Job | None = None
    if body.parent is not None:
        if kind != "prompt":
            raise ApiError(422, "bad_request", "only a prompt job can follow up on another job")
        parent = _job(request, body.parent)
        if not parent.finished:
            raise ApiError(409, "parent_running", "wait for that job to finish, then refine it")
    voice = (body.voice or "").strip()
    if voice == MY_VOICE and not (org.my_voice or {}).get("handle"):
        raise ApiError(422, "bad_request", "record your voice in Settings first")
    if voice and voice not in VOICES and voice != MY_VOICE:
        raise ApiError(422, "bad_request", f"unknown voice {voice!r}")
    if not voice and parent is not None:
        voice = parent.voice  # a chat keeps its voice
    services.check_rate(request.api_key)  # type: ignore[attr-defined]
    services.check_quota(org)
    job = Job.objects.create(
        voice=voice,
        parent=parent,
        org=org,
        created_by=request.api_key,  # type: ignore[attr-defined]
        kind=kind,
        spec=spec,
        prompt=prompt,
        assets=handles,
        recipe=recipe,
    )
    enqueue(job)
    job.refresh_from_db()
    return Status(202, services.job_dict(job))


@api.get("/jobs/{job_id}/thread")
def job_thread(request: HttpRequest, job_id: str) -> dict[str, Any]:
    """The conversation a job belongs to, oldest first: it and the jobs it follows up on."""
    chain = [_job(request, job_id)]
    while chain[-1].parent_id and len(chain) < 50:
        parent = Job.objects.filter(pk=chain[-1].parent_id, org=_org(request)).first()
        if parent is None:
            break
        chain.append(parent)
    return {"items": [services.job_dict(j) for j in reversed(chain)]}


def _job(request: HttpRequest, job_id: str) -> Job:
    try:
        job = Job.objects.select_related("recipe").filter(pk=job_id, org=_org(request)).first()
    except (ValueError, TypeError, ValidationError):
        job = None
    if job is None:
        raise ApiError(404, "not_found", f"no job {job_id!r} in this organization")
    return job


@api.get("/jobs")
def list_jobs(
    request: HttpRequest, status: str | None = None, limit: int = 25, offset: int = 0
) -> dict[str, Any]:
    qs = Job.objects.filter(org=_org(request)).select_related("recipe").order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    limit = max(1, min(limit, 100))
    return {
        "count": qs.count(),
        "items": [services.job_dict(j) for j in qs[offset : offset + limit]],
    }


@api.get("/jobs/{job_id}")
def job_detail(request: HttpRequest, job_id: str) -> dict[str, Any]:
    return services.job_dict(_job(request, job_id), events=True)


@api.get("/jobs/{job_id}/events.json")
def job_events(request: HttpRequest, job_id: str, after: int = 0) -> dict[str, Any]:
    """Progress events after `after` (poll with the last `seq` you saw)."""
    job = _job(request, job_id)
    events = [services.event_dict(e) for e in job.events.filter(seq__gt=after)]
    return {"status": job.status, "finished": job.finished, "events": events}


@api.post("/jobs/{job_id}/cancel")
def cancel_job(request: HttpRequest, job_id: str) -> dict[str, Any]:
    """Stop a job: at once if it is still waiting, else between two of its steps."""
    job = _job(request, job_id)
    if job.finished:
        raise ApiError(409, "already_finished", f"the job already ended ({job.status})")
    if Job.objects.filter(pk=job.pk, status=Job.QUEUED).update(
        status=Job.CANCELLED, finished_at=timezone.now(), cancel_requested=True, error="cancelled"
    ):
        job.refresh_from_db()
        services.emit(job, "finished", {"status": Job.CANCELLED})
    else:
        Job.objects.filter(pk=job.pk).update(cancel_requested=True)
        job.refresh_from_db()
    return services.job_dict(job)


# -- saved recipes ------------------------------------------------------------------------------


def _recipe_dict(r: SavedRecipe) -> dict[str, Any]:
    return {
        "id": r.pk,
        "name": r.name,
        "description": r.description,
        "version": r.version,
        "inputs": r.template.get("inputs", {}),
        "created_at": r.created_at,
    }


def _save_recipe(
    request: HttpRequest, name: str, description: str, spec: dict[str, Any]
) -> SavedRecipe:
    org = _org(request)
    _validated(services.engine_for(org), spec)
    existing = SavedRecipe.objects.filter(org=org, name=name).first()
    if existing is not None:
        existing.template, existing.description = spec, description
        existing.version, existing.archived = existing.version + 1, False
        existing.save()
        return existing
    return SavedRecipe.objects.create(
        org=org,
        name=name,
        description=description,
        template=spec,
        created_by=request.api_key,  # type: ignore[attr-defined]
    )


@api.get("/recipes")
def list_recipes(request: HttpRequest) -> dict[str, Any]:
    items = SavedRecipe.objects.filter(org=_org(request), archived=False).order_by("name")
    return {"items": [_recipe_dict(r) for r in items]}


@api.post("/recipes", response={201: dict[str, Any]})
def create_recipe(request: HttpRequest, body: RecipeIn) -> Status[dict[str, Any]]:
    """Save a full-form spec to run again; its inputs are the recipe's parameters."""
    if "inputs" not in body.spec:
        raise ApiError(422, "bad_request", "a saved recipe needs a full-form spec with `inputs`")
    return Status(201, _recipe_dict(_save_recipe(request, body.name, body.description, body.spec)))


@api.post("/jobs/{job_id}/save-recipe", response={201: dict[str, Any]})
def save_job_as_recipe(
    request: HttpRequest, job_id: str, body: SaveRecipeIn
) -> Status[dict[str, Any]]:
    """Save the spec a finished job ran as a recipe (\"save as recipe\" in the UI)."""
    job = _job(request, job_id)
    if job.spec is None:
        raise ApiError(409, "no_spec", "this job has no spec to save (it produced none)")
    canon = _validated(services.engine_for(_org(request)), job.spec)
    return Status(
        201, _recipe_dict(_save_recipe(request, body.name, body.description, _plain_spec(canon)))
    )


@api.delete("/recipes/{recipe_id}")
def delete_recipe(request: HttpRequest, recipe_id: int) -> dict[str, Any]:
    n = SavedRecipe.objects.filter(pk=recipe_id, org=_org(request)).update(archived=True)
    if not n:
        raise ApiError(404, "not_found", f"no recipe {recipe_id} in this organization")
    return {"archived": recipe_id}


# -- brand kit and usage ------------------------------------------------------------------------


@api.get("/brand-kit")
def get_brand_kit(request: HttpRequest) -> dict[str, Any]:
    return dict(_org(request).brand_kit or {})


@api.put("/brand-kit")
def put_brand_kit(request: HttpRequest, body: BrandKitIn) -> dict[str, Any]:
    """The organization's logo, colors and fonts. The agent gets them in each request's text
    (never in the system prompt), and the logo assets ride along."""
    from atelier import fonts

    org = _org(request)
    engine = services.engine_for(org)
    kit: dict[str, Any] = {}
    if body.name:
        kit["name"] = body.name
    if body.tagline:
        kit["tagline"] = body.tagline
    for role, value in body.colors.items():
        try:
            parse_color(value)
        except ValueError as e:
            raise ApiError(422, "bad_color", f"colors.{role}: {e}") from None
    if body.colors:
        kit["colors"] = body.colors
    for role, family in body.fonts.items():
        if family not in fonts.families():
            raise ApiError(
                422,
                "bad_font",
                f"fonts.{role}: {family!r} is not bundled; use {', '.join(fonts.families())}",
            )
    if body.fonts:
        kit["fonts"] = body.fonts
    if body.rules:
        kit["rules"] = [str(r).strip() for r in body.rules if str(r).strip()]
    for key in ("logo", "logo_dark"):
        handle = getattr(body, key)
        if handle:
            info = _resolve(engine, handle)
            if info.media is not MediaType.IMAGE:
                raise ApiError(
                    422, "bad_logo", f"{key} must be an image, but {handle} is {info.media.value}"
                )
            kit[key] = info.handle
    org.brand_kit = kit
    org.save(update_fields=["brand_kit"])
    return kit


@api.get("/usage")
def get_usage(request: HttpRequest) -> dict[str, Any]:
    return services.usage(_org(request))


# -- the animation cast (characters and places) -------------------------------------------------


def _service(dotted: str) -> Any:
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)()


def _design(org: Org, kind: str, look: str) -> str:
    """Draw a cast member's design picture in the animation style; returns its handle."""
    from atelier.agent.animation import STYLE
    from atelier.agent.cast import design_prompt
    from atelier.agent.pictures import PictureError

    pictures = _service(settings.ATELIER_PICTURES_CLIENT)
    if pictures is None:
        raise ApiError(503, "unavailable", "drawing characters is not available right now")
    shape = "landscape" if kind == "place" else "square"
    try:
        data = pictures.generate(design_prompt(kind, look), shape, style=STYLE)
    except PictureError as e:
        raise ApiError(502, "picture_failed", f"the design could not be drawn: {e}") from None
    info = services.engine_for(org).store.put_bytes(data)
    if info.media is not MediaType.IMAGE:
        raise ApiError(502, "picture_failed", "no picture came back; try again")
    return info.handle


def _character(request: HttpRequest, character_id: int) -> Character:
    found = Character.objects.filter(pk=character_id, org=_org(request)).first()
    if found is None:
        raise ApiError(404, "not_found", "no such character")
    return found


@api.get("/characters")
def list_characters(request: HttpRequest) -> dict[str, Any]:
    rows = Character.objects.filter(org=_org(request)).order_by("kind", "created_at")
    return {"items": [services.character_dict(c) for c in rows]}


@api.post("/characters", response={201: dict[str, Any]})
def create_character(request: HttpRequest, body: CharacterIn) -> Status[dict[str, Any]]:
    """Design a character or place from a description and add it to the cast."""
    from atelier.agent.cast import CastMember

    org = _org(request)
    name = " ".join(body.name.split())
    if Character.objects.filter(org=org, name__iexact=name).exists():
        raise ApiError(409, "exists", f"you already have {name!r}; edit it instead")
    look = " ".join(body.look.split())
    voice = " ".join(body.voice.split()) if body.kind == "character" else ""
    handle = _design(org, body.kind, look)
    member = CastMember(name, body.kind, look, voice, handle)
    filename = "character-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + ".png"
    made = services.save_character(org, member, filename, origin=None)
    return Status(201, services.character_dict(made))


@api.patch("/characters/{character_id}")
def edit_character(request: HttpRequest, character_id: int, body: CharacterEdit) -> dict[str, Any]:
    c = _character(request, character_id)
    if body.name is not None:
        name = " ".join(body.name.split())
        clash = Character.objects.filter(org=c.org, name__iexact=name).exclude(pk=c.pk)
        if clash.exists():
            raise ApiError(409, "exists", f"you already have {name!r}")
        c.name = name
    if body.voice is not None and c.kind == "character":
        c.voice = " ".join(body.voice.split())
    look_changed = body.look is not None and " ".join(body.look.split()) != c.look
    if body.look is not None:
        c.look = " ".join(body.look.split())
    if look_changed or body.redesign:
        c.image = _design(c.org, c.kind, c.look)
        services.register_asset(
            c.org, services.engine_for(c.org).store.resolve(c.image),
            "character-" + re.sub(r"[^a-z0-9]+", "-", c.name.lower()).strip("-") + ".png",
        )  # fmt: skip
    c.updated_at = timezone.now()
    c.save()
    return services.character_dict(c)


@api.delete("/characters/{character_id}")
def delete_character(request: HttpRequest, character_id: int) -> dict[str, Any]:
    """Remove it from the cast (its design picture stays in the Library)."""
    c = _character(request, character_id)
    c.delete()
    return {"deleted": character_id}


@api.get("/characters/{character_id}/voice")
def hear_character(request: HttpRequest, character_id: int) -> HttpResponse:
    """A short line in the character's voice, to hear it before animating."""
    from atelier.agent.voice import VoiceError

    c = _character(request, character_id)
    if c.kind != "character":
        raise ApiError(422, "bad_request", "a place has no voice")
    voice = _service(settings.ATELIER_VOICE_CLIENT)
    if voice is None or not hasattr(voice, "speak_as"):
        raise ApiError(503, "unavailable", "voices are not available right now")
    try:
        mp3 = voice.speak_as(f"Hi! I'm {c.name}. Nice to meet you!", c.voice or "a friendly voice")
    except VoiceError as e:
        raise ApiError(502, "voice_failed", str(e)) from None
    return HttpResponse(mp3, content_type="audio/mpeg")


# -- hand edits (the editor) --------------------------------------------------------------------


@api.post("/jobs/{job_id}/edit", response={202: dict[str, Any]})
def edit_by_hand(request: HttpRequest, job_id: str, body: EditIn) -> Status[dict[str, Any]]:
    """Render the editor's changes to a video as the chat's next version. No model is used:
    a typed voiceover is spoken and AI music composed first, then the edit runs as a spec."""
    org = _org(request)
    parent = _job(request, job_id)
    if not parent.finished:
        raise ApiError(409, "parent_running", "wait for that version to finish, then edit it")
    engine = services.engine_for(org)
    source = _resolve(engine, body.source)
    if source.media is not MediaType.VIDEO:
        raise ApiError(422, "bad_request", "only videos can be edited here")
    duration = float(engine.probe(source).get("duration") or 0.0)
    try:
        parts = edits.kept_parts(body, duration)
    except ValueError as e:
        raise ApiError(422, "bad_request", str(e)) from None
    length = edits.make_clock(body, parts).length
    append_handles: list[str] = []
    for item in body.append:
        extra = _resolve(engine, item.handle)
        if extra.media is not MediaType.VIDEO:
            raise ApiError(422, "bad_request", "only videos can be added after the clip")
        append_handles.append(extra.handle)
        whole = float(engine.probe(extra).get("duration") or 0.0)
        length += max(0.0, min(item.end or whole, whole) - item.start)
    services.check_rate(request.api_key)  # type: ignore[attr-defined]
    services.check_quota(org)
    made: list[tuple[str, str]] = []

    voice_handle = None
    if body.voiceover and body.voiceover.handle:
        voice_handle = _resolve(engine, body.voiceover.handle).handle
    elif body.voiceover and body.voiceover.text.strip() and not body.draft:
        info = _spoken(org, engine, body.voiceover.text, body.voiceover.voice)
        voice_handle = info.handle
        made.append((info.handle, "voiceover.mp3"))

    music_handle = None
    if body.music and body.music.handle:
        music_handle = _resolve(engine, body.music.handle).handle
    elif body.music and body.music.compose.strip() and not body.draft:
        info = _composed(engine, body.music.compose, length)
        music_handle = info.handle
        made.append((info.handle, "music.mp3"))

    green_handle = _resolve(engine, body.green_screen.handle).handle if body.green_screen else None
    overlay_infos = [_resolve(engine, o.handle) for o in body.overlays]
    overlay_handles = [i.handle for i in overlay_infos]
    overlay_media = [i.media.value for i in overlay_infos]
    has_audio = bool(engine.probe(source).get("audio"))
    skipped_captions = body.captions is not None and not has_audio
    if skipped_captions:  # captions are written from the video's own sound; a silent video has none
        body = body.model_copy(update={"captions": None})
    spec = edits.build_spec(
        body, duration, voice_handle, music_handle, has_audio, overlay_handles, append_handles, overlay_media, green_handle
    )
    _validated(engine, spec)
    if body.draft:
        services.prune_drafts(org)
    job = Job.objects.create(
        parent=None if body.draft else parent,
        org=org,
        created_by=request.api_key,  # type: ignore[attr-defined]
        kind="spec",
        spec=spec,
        prompt=edits.DRAFT_PROMPT
        if body.draft
        else edits.describe(body, parts, duration)
        + (" (no captions: this video has no sound to write them from)" if skipped_captions else ""),
        assets=[source.handle],
        voice=parent.voice,
    )
    for handle, name in made:
        services.register_asset(org, engine.store.resolve(handle), filename=name, origin=job)
    enqueue(job)
    job.refresh_from_db()
    return Status(202, services.job_dict(job))


@api.get("/assets/{handle}/beats")
def beats(request: HttpRequest, handle: str) -> dict[str, Any]:
    """The tempo of a piece of music (or a video's sound) and when its beats fall."""
    from atelier.av import beats as tempo

    org = _org(request)
    engine = services.engine_for(org)
    info = _resolve(engine, handle)
    if info.media not in (MediaType.AUDIO, MediaType.VIDEO):
        raise ApiError(422, "bad_request", "only sound and video have a tempo")
    return tempo.analyse(tempo.decode(engine.store.path(info.hash)))


@api.post("/assets/{handle}/cutout", response={202: dict[str, Any]})
def cutout(request: HttpRequest, handle: str) -> Status[dict[str, Any]]:
    """Cut the subject of a picture out of its background (a transparent PNG), e.g. for a
    sticker. A background job of its own: it is not a version of any chat."""
    org = _org(request)
    engine = services.engine_for(org)
    source = _resolve(engine, handle)
    if source.media is not MediaType.IMAGE:
        raise ApiError(422, "bad_request", "only pictures can be cut out")
    services.check_rate(request.api_key)  # type: ignore[attr-defined]
    services.check_quota(org)
    spec = {
        "version": 1,
        "inputs": {"i": source.handle},
        "steps": [
            {"id": "c", "op": "image.remove_background", "in": ["i"], "args": {}},
            {"id": "out", "op": "export", "in": ["c"], "args": {"preset": "transparent_png"}},
        ],
        "outputs": ["out"],
    }
    _validated(engine, spec)
    job = Job.objects.create(
        org=org,
        created_by=request.api_key,  # type: ignore[attr-defined]
        kind="spec",
        spec=spec,
        prompt="Cut out the background",
        assets=[source.handle],
    )
    enqueue(job)
    job.refresh_from_db()
    return Status(202, services.job_dict(job))


class SplitIn(Schema):
    source: str


@api.post("/jobs/{job_id}/split", response={202: dict[str, Any]})
def split_layers(request: HttpRequest, job_id: str, body: SplitIn) -> Status[dict[str, Any]]:
    """Make the next version of a video as separate layers: its picture and its sound apart."""
    org = _org(request)
    parent = _job(request, job_id)
    if not parent.finished:
        raise ApiError(409, "parent_running", "wait for that version to finish first")
    engine = services.engine_for(org)
    source = _resolve(engine, body.source)
    if source.media is not MediaType.VIDEO:
        raise ApiError(422, "bad_request", "only videos can be split into layers")
    if not engine.probe(source).get("audio"):
        raise ApiError(422, "bad_request", "this video has no sound to separate")
    services.check_rate(request.api_key)  # type: ignore[attr-defined]
    services.check_quota(org)
    spec = edits.split_spec(source.handle)
    _validated(engine, spec)
    job = Job.objects.create(
        parent=parent,
        org=org,
        created_by=request.api_key,  # type: ignore[attr-defined]
        kind="spec",
        spec=spec,
        prompt="Split into layers: the picture and the sound",
        assets=[source.handle],
        voice=parent.voice,
    )
    enqueue(job)
    job.refresh_from_db()
    return Status(202, services.job_dict(job))


def _spoken(org: Org, engine: Engine, text: str, voice_id: str) -> Any:
    """A voiceover of `text` in one of the voices (or the user's own); returns its info."""
    from atelier.agent.budget import Budget
    from atelier.agent.config import AgentConfig
    from atelier.agent.tools import Toolbox
    from atelier.agent.voice import VoiceError

    voice_id = voice_id or DEFAULT_VOICE
    if voice_id not in VOICES and voice_id != MY_VOICE:
        raise ApiError(422, "bad_request", f"unknown voice {voice_id!r}")
    service = _service(settings.ATELIER_VOICE_CLIENT)
    if service is None:
        raise ApiError(503, "unavailable", "voiceovers are not available right now")
    if voice_id == MY_VOICE:
        mine = org.my_voice or {}
        if not mine.get("handle") or not hasattr(service, "with_reference"):
            raise ApiError(422, "bad_request", "record your voice in Settings first")
        ref = engine.store.path(engine.store.resolve(mine["handle"]).hash).read_bytes()
        service = service.with_reference(ref, str(mine.get("text") or ""))
    box = Toolbox(engine, Budget(AgentConfig().limits), voice=service)
    try:
        info, _captions = box._speak(" ".join(text.split()), voice_id)
    except (VoiceError, OSError) as e:
        raise ApiError(502, "voice_failed", f"the voiceover could not be made: {e}") from None
    return info


def _composed(engine: Engine, style: str, seconds: float) -> Any:
    """Original music from a few words, about `seconds` long; returns its info."""
    from atelier.agent.music import MusicError

    composer = _service(settings.ATELIER_MUSIC_CLIENT)
    if composer is None:
        raise ApiError(503, "unavailable", "AI music is not available right now")
    try:
        mp3 = composer.compose(style.strip()[:500], max(10, int(seconds) + 2), 100, 7)
    except MusicError as e:
        raise ApiError(502, "music_failed", f"the music could not be made: {e}") from None
    return engine.store.put_bytes(mp3, media=MediaType.AUDIO, ext=".mp3")


class SoundIn(Schema):
    text: str = Field("", max_length=4000)
    voice: str = ""
    style: str = Field("", max_length=500)
    seconds: float = Field(30.0, ge=5, le=600)


@api.post("/jobs/{job_id}/edit/voiceover")
def make_edit_voiceover(request: HttpRequest, job_id: str, body: SoundIn) -> dict[str, Any]:
    """The editor's "Make voiceover": spoken now, so it can be heard in the preview; saving
    the edit then uses this one."""
    org, parent = _org(request), _job(request, job_id)
    if not body.text.strip():
        raise ApiError(422, "bad_request", "type what the voice should say")
    services.check_rate(request.api_key)  # type: ignore[attr-defined]
    engine = services.engine_for(org)
    info = _spoken(org, engine, body.text, body.voice)
    services.register_asset(org, info, filename="voiceover.mp3", origin=parent)
    return {"handle": info.handle, "seconds": engine.probe(info).get("duration")}


@api.post("/jobs/{job_id}/edit/music")
def make_edit_music(request: HttpRequest, job_id: str, body: SoundIn) -> dict[str, Any]:
    """The editor's "Make music": composed now, to hear in the preview."""
    org, parent = _org(request), _job(request, job_id)
    if not body.style.strip():
        raise ApiError(422, "bad_request", "say what kind of music, e.g. calm piano")
    services.check_rate(request.api_key)  # type: ignore[attr-defined]
    engine = services.engine_for(org)
    info = _composed(engine, body.style, body.seconds)
    services.register_asset(org, info, filename="music.mp3", origin=parent)
    return {"handle": info.handle, "seconds": engine.probe(info).get("duration")}
