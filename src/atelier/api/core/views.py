"""The JSON API (Django Ninja), mounted at /api/v1/.

Every endpoint is scoped to the organization of the API key. Long work never happens in a
request: `POST /jobs` records a job and enqueues it; clients read its state, its events (polled
or streamed) and its outputs.
"""

from __future__ import annotations

import copy
import mimetypes
import tempfile
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpRequest, HttpResponse
from django.utils import timezone
from ninja import File, NinjaAPI, Status
from ninja.files import UploadedFile

from atelier.agent.voice import ACCENTS, DEFAULT_VOICE, PEOPLE, VOICES
from atelier.api.core import accounts, services
from atelier.api.core.auth import ApiKeyAuth
from atelier.api.core.models import Asset, Job, Org, SavedRecipe
from atelier.api.core.queue import enqueue
from atelier.api.core.schemas import (
    BrandKitIn,
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
def delete_conversation(request: HttpRequest, job_id: str) -> dict[str, Any]:
    """Remove a chat from the list (every message in it). What it made stays in the Library."""
    chain = services.thread_jobs(services.latest_in_thread(_job(request, job_id)))
    Job.objects.filter(pk__in=[j.pk for j in chain]).update(chat_deleted=True)
    return {"deleted": len(chain)}


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


@api.get("/assets/{handle}/content")
def asset_content(request: HttpRequest, handle: str) -> FileResponse:
    asset = _asset(request, handle)
    engine = services.engine_for(_org(request))
    path = engine.store.path(asset.hash)
    kind = mimetypes.guess_type(f"x{asset.ext}")[0] or "application/octet-stream"
    inline = kind in _INLINE
    response = FileResponse(path.open("rb"), content_type=kind)
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
    if voice and voice not in VOICES:
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
