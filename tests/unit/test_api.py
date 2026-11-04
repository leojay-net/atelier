"""The application layer: keys, assets, jobs, events, quotas, recipes, brand kit and workers.

The database is in-memory SQLite, jobs run inline (no worker process needed), the store is a
temporary directory, and the "model" is scripted, so nothing here touches the network.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

import api_support
from atelier.api.core import services, worker
from atelier.api.core.models import ApiKey, Asset, Job, Org
from conftest import FIXTURES
from harness.fake_model import ScriptedModel, final, run_pipeline

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def isolated(settings: Any, tmp_path: Path) -> None:
    settings.ATELIER_STORE_ROOT = tmp_path / "orgs"
    settings.ATELIER_QUEUE = "inline"
    settings.ATELIER_MODEL_CLIENT = "api_support.model_client"
    api_support.model = None


class Api:
    """A client that sends one API key and speaks JSON."""

    def __init__(self, token: str | None = None) -> None:
        self.client = Client()
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}

    def get(self, path: str, **params: Any) -> Any:
        return self.client.get(f"/api/v1{path}", params, **self.headers)

    def post(self, path: str, body: Any = None) -> Any:
        return self.client.post(
            f"/api/v1{path}",
            json.dumps(body or {}),
            content_type="application/json",
            **self.headers,
        )

    def put(self, path: str, body: Any) -> Any:
        return self.client.put(
            f"/api/v1{path}", json.dumps(body), content_type="application/json", **self.headers
        )

    def delete(self, path: str) -> Any:
        return self.client.delete(f"/api/v1{path}", **self.headers)

    def upload(self, name: str, data: bytes) -> Any:
        return self.client.post(
            "/api/v1/assets", {"file": SimpleUploadedFile(name, data)}, **self.headers
        )


def make_org(slug: str, **fields: Any) -> tuple[Org, Api]:
    org = Org.objects.create(slug=slug, name=slug.title(), **fields)
    _, token = ApiKey.issue(org, "test")
    return org, Api(token)


@pytest.fixture
def acme() -> tuple[Org, Api]:
    return make_org("acme")


@pytest.fixture
def api(acme: tuple[Org, Api]) -> Api:
    return acme[1]


@pytest.fixture
def photo_bytes() -> bytes:
    return (FIXTURES / "photo.png").read_bytes()


def photo_handle(api: Api, photo_bytes: bytes) -> str:
    r = api.upload("photo.png", photo_bytes)
    assert r.status_code == 201, r.content
    handle: str = r.json()["handle"]
    return handle


def crop_spec(handle: str) -> dict[str, Any]:
    return {
        "in": handle,
        "ops": [["image.resize", {"width": 320}], ["export", {"preset": "web_jpeg"}]],
    }


# -- keys -------------------------------------------------------------------------------------


def test_requests_need_a_valid_key() -> None:
    assert Api().get("/me").status_code == 401
    assert Api("atk_nope_nope").get("/me").status_code == 401
    org, api = make_org("acme")
    assert api.get("/me").json() == {
        "org": {"slug": "acme", "name": "Acme"},
        "key": {"name": "test", "prefix": ApiKey.objects.get().prefix},
    }
    ApiKey.objects.update(revoked_at=timezone.now())
    assert api.get("/me").status_code == 401  # revoked
    assert org.pk  # (the org stays)


def test_a_key_is_stored_only_as_a_hash() -> None:
    org, _ = make_org("acme")
    key, token = ApiKey.issue(org, "another")
    assert token.startswith("atk_") and token not in key.key_hash
    assert key.key_hash == ApiKey.digest(token) and len(key.key_hash) == 64
    assert not any(token in str(v) for v in ApiKey.objects.values())


# -- the catalogue and validation ---------------------------------------------------------------


def test_the_catalogue_and_op_details_are_served(api: Api) -> None:
    assert "image.crop" in api.get("/ops").json()["catalogue"]
    assert "params:" in api.get("/ops/image.crop").json()["description"]
    r = api.get("/ops/no.such")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_validate_reports_the_plan_or_every_error(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    ok = api.post("/validate", {"spec": crop_spec(handle)}).json()
    assert ok["valid"] and ok["steps"] == 2 and "cached" in ok and ok["runs"] == 2
    bad = api.post("/validate", {"spec": {"in": handle, "ops": [["image.nope", {}]]}}).json()
    assert bad["valid"] is False and "image.nope" in bad["errors"][0]


# -- assets -----------------------------------------------------------------------------------


def test_upload_registers_an_asset_and_dedups_within_the_org(api: Api, photo_bytes: bytes) -> None:
    r = api.upload("Holiday snap!.png", photo_bytes)
    body = r.json()
    assert r.status_code == 201 and body["handle"].startswith("asset:") and body["media"] == "image"
    assert body["filename"] == "Holiday snap_.png"  # a name that cannot break a header
    again = api.upload("other.png", photo_bytes).json()
    assert again["handle"] == body["handle"] and Asset.objects.count() == 1


def test_uploads_are_checked(acme: tuple[Org, Api], photo_bytes: bytes) -> None:
    org, api = acme
    r = api.upload("blob.bin", b"\x00\x01\x02\x03" * 40)
    assert r.status_code == 415 and r.json()["error"] == "unsupported_type"
    org.max_upload_bytes = 100
    org.save()
    r = api.upload("photo.png", photo_bytes)
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    assert api.client.post("/api/v1/assets", {}, **api.headers).status_code == 422  # no file


def test_empty_and_unreadable_uploads_are_refused(api: Api) -> None:
    r = api.upload("empty.mp4", b"")
    assert r.status_code == 400 and r.json()["error"] == "empty_file"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_truncated_video_is_refused_at_upload(api: Api, tmp_path: Path) -> None:
    # A camera/ffmpeg-style MP4 keeps its index (moov) at the end, so an interrupted
    # download has no index at all and cannot be read.
    tail_moov = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(FIXTURES / "av" / "clip_landscape.mp4"),
         "-c", "copy", "-movflags", "-faststart", str(tail_moov)],
        check=True,
    )  # fmt: skip
    whole = tail_moov.read_bytes()
    r = api.upload("truncated.mp4", whole[: len(whole) // 3])
    assert r.status_code == 415 and r.json()["error"] == "unreadable_media", r.json()
    assert api.upload("clip.mp4", whole).status_code == 201


def test_asset_detail_content_and_listing(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    detail = api.get(f"/assets/{handle}").json()
    assert detail["media"] == "image" and "image" in detail["description"] and detail["meta"]
    content = api.get(f"/assets/{handle}/content")
    assert content.status_code == 200 and b"".join(content.streaming_content) == photo_bytes
    assert content["Content-Type"] == "image/png" and content["X-Content-Type-Options"] == "nosniff"
    assert content["Content-Disposition"].startswith("inline;")
    assert "sandbox" in content["Content-Security-Policy"]
    listing = api.get("/assets").json()
    assert listing["count"] == 1 and listing["items"][0]["handle"] == handle
    assert api.get("/assets/asset:00000000").status_code == 404


def test_an_office_or_pdf_download_is_an_attachment(api: Api) -> None:
    letter = api.upload("letter.docx", (FIXTURES / "docs" / "letter.docx").read_bytes()).json()
    r = api.get(f"/assets/{letter['handle']}/content")
    assert r["Content-Disposition"].startswith("attachment;")


def test_one_organization_never_sees_anothers_assets(photo_bytes: bytes) -> None:
    _, a = make_org("acme")
    _, b = make_org("globex")
    handle = photo_handle(a, photo_bytes)
    assert b.get(f"/assets/{handle}").status_code == 404
    assert b.get(f"/assets/{handle}/content").status_code == 404
    assert b.get("/assets").json()["count"] == 0
    r = b.post("/jobs", {"spec": crop_spec(handle)})  # the engine cannot resolve it there either
    assert r.status_code == 422 and r.json()["error"] == "invalid_spec"
    assert photo_handle(b, photo_bytes) == handle  # same bytes, its own copy in its own store


# -- spec jobs --------------------------------------------------------------------------------


def test_a_spec_job_runs_and_records_outputs_and_events(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    r = api.post("/jobs", {"spec": crop_spec(handle)})
    assert r.status_code == 202
    job = r.json()
    assert job["kind"] == "spec" and job["status"] == "succeeded" and job["units"] == 0.0
    (out,) = job["result"]["outputs"].values()
    assert (
        out["media"] == "image"
        and out["handle"].startswith("asset:")
        and "jpeg" in out["summary"].lower()
    )
    assert job["result"]["passed"] is True and job["compute_seconds"] > 0
    checks = next(iter(job["result"]["checks"].values()))
    assert checks and all(c["status"] == "pass" for c in checks)
    # the output is an asset of this org and can be downloaded
    download = api.get(f"/assets/{out['handle']}/content")
    assert download.status_code == 200 and download["Content-Type"] == "image/jpeg"
    detail = api.get(f"/jobs/{job['id']}").json()
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds[0] == "started" and "plan" in kinds and kinds[-1] == "finished"
    assert kinds.count("step_start") == kinds.count("step_done") >= 1
    assert [e["seq"] for e in detail["events"]] == list(range(1, len(kinds) + 1))


def test_events_can_be_polled_from_where_the_client_left_off(api: Api, photo_bytes: bytes) -> None:
    job = api.post("/jobs", {"spec": crop_spec(photo_handle(api, photo_bytes))}).json()
    everything = api.get(f"/jobs/{job['id']}/events.json").json()
    assert everything["finished"] and everything["status"] == "succeeded"
    last = everything["events"][1]["seq"]
    rest = api.get(f"/jobs/{job['id']}/events.json", after=last).json()["events"]
    assert [e["seq"] for e in rest] == [e["seq"] for e in everything["events"] if e["seq"] > last]


def test_an_invalid_spec_is_refused_up_front_with_every_error(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    r = api.post("/jobs", {"spec": {"in": handle, "ops": [["image.crop", {"aspect": "wide"}]]}})
    body = r.json()
    assert r.status_code == 422 and body["error"] == "invalid_spec" and body["errors"]
    assert Job.objects.count() == 0  # nothing was queued


def test_a_step_that_fails_at_run_time_fails_the_job_with_the_reason(api: Api) -> None:
    pdf = api.upload("report.pdf", (FIXTURES / "docs" / "report.pdf").read_bytes()).json()["handle"]
    r = api.post("/jobs", {"spec": {"in": pdf, "ops": [["pdf.pages", {"pages": "1,9"}]]}}).json()
    assert r["status"] == "failed" and "page 9 is past the end" in r["error"]
    assert r["finished_at"] and r["result"] is None


def test_checks_that_still_fail_are_reported_not_hidden(api: Api) -> None:
    letter = api.upload("letter.docx", (FIXTURES / "docs" / "letter.docx").read_bytes()).json()[
        "handle"
    ]
    job = api.post(
        "/jobs", {"spec": {"in": letter, "ops": [["export", {"preset": "docx_clean"}]]}}
    ).json()
    assert job["status"] == "checks_failed" and job["result"]["passed"] is False
    failing = [c for cs in job["result"]["checks"].values() for c in cs if c["status"] == "fail"]
    assert failing and failing[0]["name"] == "no_placeholders" and failing[0]["hint"]


def test_a_request_needs_exactly_one_thing_to_do(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    for body in ({}, {"spec": crop_spec(handle), "prompt": "make it small"}):
        r = api.post("/jobs", body)
        assert r.status_code == 422 and r.json()["error"] == "bad_request"
    assert api.post("/jobs", {"prompt": "   "}).status_code == 422
    assert api.post("/jobs", {"prompt": "x", "assets": ["asset:00000000"]}).status_code == 404


def test_jobs_are_listed_newest_first_and_are_org_scoped(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    first = api.post("/jobs", {"spec": crop_spec(handle)}).json()["id"]
    second = api.post("/jobs", {"spec": crop_spec(handle)}).json()["id"]
    items = api.get("/jobs").json()["items"]
    assert [j["id"] for j in items] == [second, first]
    assert api.get("/jobs", status="failed").json()["count"] == 0
    _, other = make_org("globex")
    assert other.get("/jobs").json()["count"] == 0
    assert other.get(f"/jobs/{first}").status_code == 404
    assert api.get("/jobs/not-a-uuid").status_code == 404


# -- cancelling and workers -------------------------------------------------------------------


def test_a_queued_job_can_be_cancelled_and_a_finished_one_cannot(
    api: Api, photo_bytes: bytes, settings: Any
) -> None:
    settings.ATELIER_QUEUE = "database"  # jobs wait for a worker
    handle = photo_handle(api, photo_bytes)
    job = api.post("/jobs", {"spec": crop_spec(handle)}).json()
    assert job["status"] == "queued"
    cancelled = api.post(f"/jobs/{job['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and cancelled["finished_at"]
    assert worker.run_forever(once=True) == 0  # the worker finds nothing to run
    again = api.post(f"/jobs/{job['id']}/cancel")
    assert again.status_code == 409 and again.json()["error"] == "already_finished"


def test_a_worker_runs_waiting_jobs_oldest_first_and_stops_when_idle(
    api: Api, photo_bytes: bytes, settings: Any
) -> None:
    settings.ATELIER_QUEUE = "database"
    handle = photo_handle(api, photo_bytes)
    ids = [api.post("/jobs", {"spec": crop_spec(handle)}).json()["id"] for _ in range(2)]
    assert worker.run_forever(once=True) == 2
    done = list(Job.objects.order_by("finished_at").values_list("pk", flat=True))
    assert [str(x) for x in done] == ids
    assert set(Job.objects.values_list("status", flat=True)) == {"succeeded"}


def test_cancelling_a_job_that_has_started_stops_it_between_steps(
    acme: tuple[Org, Api], photo_bytes: bytes
) -> None:
    org, api = acme
    handle = photo_handle(api, photo_bytes)
    job = Job.objects.create(org=org, kind="spec", spec=crop_spec(handle), cancel_requested=True)
    result = worker.execute(job.pk)
    assert result.status == "cancelled"
    assert list(result.events.values_list("kind", flat=True))[-1] == "finished"


def test_two_workers_never_run_the_same_job(acme: tuple[Org, Api], photo_bytes: bytes) -> None:
    org, api = acme
    handle = photo_handle(api, photo_bytes)
    job = Job.objects.create(org=org, kind="spec", spec=crop_spec(handle))
    first = worker.execute(job.pk)
    second = worker.execute(job.pk)  # the row is no longer queued: it is returned untouched
    assert first.status == "succeeded" and second.status == "succeeded"
    assert job.events.filter(kind="started").count() == 1


def test_a_job_whose_worker_went_silent_is_failed_by_the_reaper(
    acme: tuple[Org, Api], settings: Any
) -> None:
    org, _ = acme
    settings.ATELIER_STALE_JOB_SECONDS = 60
    old = (
        timezone.now() - timezone.timedelta(minutes=10) if hasattr(timezone, "timedelta") else None
    )
    from datetime import timedelta

    old = timezone.now() - timedelta(minutes=10)
    stale = Job.objects.create(org=org, kind="spec", status="running", heartbeat_at=old)
    fresh = Job.objects.create(org=org, kind="spec", status="running", heartbeat_at=timezone.now())
    assert worker.reap_stale() == 1
    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.status == "failed" and "stopped responding" in stale.error
    assert fresh.status == "running"


def test_a_crash_inside_a_job_fails_it_and_not_the_worker(
    acme: tuple[Org, Api], monkeypatch: pytest.MonkeyPatch
) -> None:
    org, _ = acme

    def boom(job: Job, engine: Any) -> Job:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(worker, "_run_spec", boom)
    job = Job.objects.create(org=org, kind="spec", spec={})
    result = worker.execute(job.pk)
    assert result.status == "failed" and result.error == "internal error: RuntimeError"
    assert "secret" not in result.error  # details stay in the log, not in the API


def test_a_crash_names_its_root_cause_without_leaking_messages(
    acme: tuple[Org, Api], monkeypatch: pytest.MonkeyPatch
) -> None:
    org, _ = acme

    def refused(job: Job, engine: Any) -> Job:
        try:
            try:
                raise ConnectionRefusedError(111, "Connection refused")
            except OSError as err:
                raise ValueError("secret transport detail") from err
        except ValueError as err:
            raise RuntimeError("secret client detail") from err

    monkeypatch.setattr(worker, "_run_spec", refused)
    job = Job.objects.create(org=org, kind="spec", spec={})
    result = worker.execute(job.pk)
    assert result.status == "failed"
    assert result.error == (
        "internal error: RuntimeError caused by ConnectionRefusedError (Connection refused)"
    )
    assert "secret" not in result.error


# -- quotas and usage -------------------------------------------------------------------------


def test_usage_counts_this_months_jobs(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    api.post("/jobs", {"spec": crop_spec(handle)})
    used = api.get("/usage").json()
    assert used["jobs"] == 1 and used["compute_seconds"] > 0 and used["units"] == 0
    assert used["units_limit"] is None


def test_a_spent_allowance_refuses_new_jobs_with_a_way_out(photo_bytes: bytes) -> None:
    org, api = make_org("acme", monthly_compute_seconds_limit=0.001, monthly_units_limit=5)
    handle = photo_handle(api, photo_bytes)
    assert api.post("/jobs", {"spec": crop_spec(handle)}).status_code == 202  # nothing spent yet
    r = api.post("/jobs", {"spec": crop_spec(handle)})
    body = r.json()
    assert (
        r.status_code == 429 and body["error"] == "quota_exceeded" and "resets" in body["message"]
    )
    Job.objects.filter(org=org).update(units=9, compute_seconds=0)
    org.monthly_compute_seconds_limit = None
    org.save()
    assert "model units" in api.post("/jobs", {"prompt": "hi"}).json()["message"]


# -- prompt jobs (the agent, scripted) ---------------------------------------------------------


def test_a_prompt_job_runs_the_agent_and_counts_its_units(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    api_support.model = ScriptedModel(
        run_pipeline(crop_spec(handle), usage=(1000, 200, 0, 0)),
        final("Resized it.", usage=(500, 100, 0, 0)),
    )
    job = api.post("/jobs", {"prompt": "Make this small", "assets": [handle]}).json()
    assert job["status"] == "succeeded" and job["kind"] == "prompt"
    assert job["result"]["summary"] == "Resized it." and job["result"]["outputs"]
    assert job["units"] == 1000 + 5 * 200 + 500 + 5 * 100  # billed-token equivalents
    assert job["spec"] is not None
    kinds = [e["kind"] for e in api.get(f"/jobs/{job['id']}").json()["events"]]
    assert "turn" in kinds and "tools" in kinds and "step_done" in kinds


def test_the_agent_can_ask_a_question_instead_of_guessing(api: Api, photo_bytes: bytes) -> None:
    handle = photo_handle(api, photo_bytes)
    api_support.model = ScriptedModel(final("", question="Which size do you want?"))
    job = api.post("/jobs", {"prompt": "Resize it", "assets": [handle]}).json()
    assert job["status"] == "needs_input" and job["result"]["question"] == "Which size do you want?"


def test_a_missing_model_service_fails_the_job_politely(api: Api) -> None:
    class NoKey:
        def create(self, request: dict[str, Any]) -> Any:
            from atelier.agent.client import MissingCredentials

            raise MissingCredentials("no key")

    api_support.model = NoKey()
    job = api.post("/jobs", {"prompt": "hello there"}).json()
    assert job["status"] == "failed" and "not configured" in job["error"]


def test_a_refused_request_fails_with_the_reason(api: Api) -> None:
    from harness.fake_model import refusal

    api_support.model = ScriptedModel(refusal("cyber", "declined"))
    job = api.post("/jobs", {"prompt": "do something odd"}).json()
    assert job["status"] == "failed" and "declined" in job["error"]


# -- brand kit ---------------------------------------------------------------------------------


def test_the_brand_kit_is_validated_stored_and_sent_with_prompts(
    api: Api, photo_bytes: bytes
) -> None:
    logo = api.upload("logo.png", (FIXTURES / "logo.png").read_bytes()).json()["handle"]
    kit = {
        "name": "Acme Studio",
        "tagline": "Made well",
        "colors": {"primary": "#ff5500", "ink": "#101010"},
        "fonts": {"heading": "Inter"},
        "logo": logo,
    }
    assert api.put("/brand-kit", kit).json()["logo"] == logo
    assert api.get("/brand-kit").json()["colors"]["primary"] == "#ff5500"
    api_support.model = ScriptedModel(final("Nothing to do.", question=""))
    api.post("/jobs", {"prompt": "Make me a banner"})
    text = api_support.model.requests[0]["messages"][0]["content"]
    assert "Brand kit" in text and "Acme Studio" in text and "primary #ff5500" in text
    assert f"logo: {logo}" in text and logo in text
    # never in the system prompt, which is frozen and shared
    assert "Acme Studio" not in json.dumps(api_support.model.requests[0]["system"])


def test_brand_kit_rules_limitations_and_restrictions(api: Api) -> None:
    kit = {
        "rules": [
            "Video duration must not exceed 30 seconds",
            "Never use unlicensed audio",
        ]
    }
    r = api.put("/brand-kit", kit)
    assert r.status_code == 200
    assert r.json()["rules"] == kit["rules"]
    assert api.get("/brand-kit").json()["rules"] == kit["rules"]
    api_support.model = ScriptedModel(final("Nothing to do.", question=""))
    api.post("/jobs", {"prompt": "Create an ad"})
    text = api_support.model.requests[0]["messages"][0]["content"]
    assert "Rules, limitations, and restrictions (strictly follow these):" in text
    assert "- Video duration must not exceed 30 seconds" in text
    assert "- Never use unlicensed audio" in text


def test_bad_brand_kits_are_refused_with_the_field(api: Api, photo_bytes: bytes) -> None:
    assert (
        api.put("/brand-kit", {"colors": {"primary": "not-a-color"}}).json()["error"] == "bad_color"
    )
    r = api.put("/brand-kit", {"fonts": {"heading": "Comic Sans"}})
    assert (
        r.status_code == 422 and r.json()["error"] == "bad_font" and "Inter" in r.json()["message"]
    )
    pdf = api.upload("r.pdf", (FIXTURES / "docs" / "report.pdf").read_bytes()).json()["handle"]
    assert api.put("/brand-kit", {"logo": pdf}).json()["error"] == "bad_logo"
    assert api.put("/brand-kit", {"logo": "asset:00000000"}).status_code == 404


# -- saved recipes -----------------------------------------------------------------------------


def test_a_finished_job_can_be_saved_as_a_recipe_and_run_on_a_new_asset(
    api: Api, photo_bytes: bytes
) -> None:
    handle = photo_handle(api, photo_bytes)
    job = api.post("/jobs", {"spec": crop_spec(handle)}).json()
    saved = api.post(
        f"/jobs/{job['id']}/save-recipe", {"name": "small-web", "description": "320 wide"}
    )
    body = saved.json()
    assert saved.status_code == 201 and body["name"] == "small-web" and body["version"] == 1
    assert (
        list(body["inputs"]) == ["in"] or body["inputs"]
    )  # the saved spec's inputs are the parameters
    (name,) = body["inputs"]
    other = api.upload("small.png", (FIXTURES / "small.png").read_bytes()).json()["handle"]
    run = api.post("/jobs", {"recipe_id": body["id"], "inputs": {name: other}}).json()
    assert run["status"] == "succeeded" and run["recipe"] == "small-web"
    (out,) = run["result"]["outputs"].values()
    assert (
        out["handle"] != next(iter(job["result"]["outputs"].values()))["handle"]
    )  # a different photo
    bad = api.post("/jobs", {"recipe_id": body["id"], "inputs": {"nope": other}})
    assert bad.status_code == 422 and bad.json()["error"] == "unknown_input"
    assert name in bad.json()["message"]
    assert api.get("/recipes").json()["items"][0]["name"] == "small-web"


def test_saving_under_an_existing_name_makes_a_new_version_and_delete_hides_it(
    api: Api, photo_bytes: bytes
) -> None:
    handle = photo_handle(api, photo_bytes)
    spec = {"version": 1, "inputs": {"photo": handle}, "steps": [
        {"id": "r", "op": "image.resize", "in": "photo", "args": {"width": 200}},
        {"id": "out", "op": "export", "in": "r", "args": {"preset": "web_jpeg"}}],
        "outputs": ["out"]}  # fmt: skip
    first = api.post("/recipes", {"name": "thumb", "spec": spec}).json()
    again = api.post("/recipes", {"name": "thumb", "spec": spec, "description": "v2"}).json()
    assert first["id"] == again["id"] and again["version"] == 2 and again["description"] == "v2"
    assert api.delete(f"/recipes/{first['id']}").json() == {"archived": first["id"]}
    assert api.get("/recipes").json()["items"] == []
    assert api.post("/jobs", {"recipe_id": first["id"]}).status_code == 404
    assert api.post("/recipes", {"name": "bad name", "spec": spec}).status_code == 422
    assert (
        api.post("/recipes", {"name": "linear", "spec": {"in": handle, "ops": []}}).status_code
        == 422
    )


def test_recipes_belong_to_their_organization(photo_bytes: bytes) -> None:
    _, a = make_org("acme")
    _, b = make_org("globex")
    handle = photo_handle(a, photo_bytes)
    spec = {"version": 1, "inputs": {"photo": handle}, "steps": [
        {"id": "out", "op": "export", "in": "photo", "args": {"preset": "web_jpeg"}}],
        "outputs": ["out"]}  # fmt: skip
    rid = a.post("/recipes", {"name": "x", "spec": spec}).json()["id"]
    assert b.get("/recipes").json()["items"] == []
    assert b.post("/jobs", {"recipe_id": rid}).status_code == 404
    assert b.delete(f"/recipes/{rid}").status_code == 404


# -- server-sent events ------------------------------------------------------------------------


def auth(api: Api) -> dict[str, str]:
    """The same key as a header dictionary (the async client takes headers, not WSGI keys)."""
    return {"Authorization": api.headers["HTTP_AUTHORIZATION"]}


async def read_stream(response: Any) -> str:
    chunks = [c async for c in response.streaming_content]
    return b"".join(chunks).decode()


@pytest.mark.asyncio
async def test_the_stream_replays_events_and_ends(
    acme: tuple[Org, Api], photo_bytes: bytes
) -> None:
    from asgiref.sync import sync_to_async
    from django.test import AsyncClient

    _, api = acme
    handle = await sync_to_async(photo_handle)(api, photo_bytes)
    job = (await sync_to_async(api.post)("/jobs", {"spec": crop_spec(handle)})).json()
    client = AsyncClient()
    r = await client.get(f"/api/v1/jobs/{job['id']}/events", headers=auth(api))
    assert r.status_code == 200 and r["Content-Type"] == "text/event-stream"
    body = await read_stream(r)
    assert (
        "event: started" in body
        and "event: finished" in body
        and body.rstrip().endswith("data: {}")
    )
    frames = [b for b in body.split("\n\n") if b.startswith("id:")]
    ids = [int(f.split("\n")[0].removeprefix("id: ")) for f in frames]
    assert ids == sorted(ids) and ids[0] == 1
    data = json.loads(frames[0].split("data: ", 1)[1])
    assert data["kind"] == "started" and data["seq"] == 1
    resumed = await client.get(
        f"/api/v1/jobs/{job['id']}/events",
        headers={**auth(api), "Last-Event-ID": str(ids[-2])},
    )
    tail = await read_stream(resumed)
    assert f"id: {ids[-1]}" in tail and f"id: {ids[0]}\n" not in tail


@pytest.mark.asyncio
async def test_the_stream_needs_a_key_and_the_right_org(photo_bytes: bytes) -> None:
    from asgiref.sync import sync_to_async
    from django.test import AsyncClient

    _, a = await sync_to_async(make_org)("acme")
    _, b = await sync_to_async(make_org)("globex")
    handle = await sync_to_async(photo_handle)(a, photo_bytes)
    job = (await sync_to_async(a.post)("/jobs", {"spec": crop_spec(handle)})).json()
    client = AsyncClient()
    assert (await client.get(f"/api/v1/jobs/{job['id']}/events")).status_code == 401
    assert (
        await client.get(f"/api/v1/jobs/{job['id']}/events", headers=auth(b))
    ).status_code == 404


# -- management commands -----------------------------------------------------------------------


def test_the_commands_create_orgs_keys_and_run_workers(
    capsys: pytest.CaptureFixture[str], settings: Any
) -> None:
    out = io.StringIO()
    call_command("create_org", "initech", "Initech", "--units", "1000", stdout=out)
    text = out.getvalue()
    token = text.split("API key (shown once): ")[1].strip()
    org = Org.objects.get(slug="initech")
    assert (
        org.monthly_units_limit == 1000 and Api(token).get("/me").json()["org"]["slug"] == "initech"
    )
    with pytest.raises(Exception, match="already exists"):
        call_command("create_org", "initech", "Again")
    out = io.StringIO()
    call_command("create_api_key", "initech", "ci", stdout=out)
    prefix = ApiKey.objects.get(name="ci").prefix
    call_command("create_api_key", "--revoke", prefix, stdout=io.StringIO())
    assert ApiKey.objects.get(name="ci").revoked_at is not None
    settings.ATELIER_QUEUE = "database"
    out = io.StringIO()
    call_command("run_worker", "--once", stdout=out)
    assert "ran 0 job(s)" in out.getvalue()


def test_usage_helpers_use_calendar_months() -> None:
    from datetime import UTC, datetime

    start = services.month_start(datetime(2026, 9, 26, 15, 30, tzinfo=UTC))
    assert (start.day, start.hour, start.minute) == (1, 0, 0) and start.month == 9


# -- CORS and the schema ------------------------------------------------------------------------


def test_a_web_ui_on_another_origin_is_allowed_only_when_listed(settings: Any) -> None:
    settings.CORS_ALLOWED_ORIGINS = ["https://ui.example"]
    client = Client()
    ask = {
        "HTTP_ORIGIN": "https://ui.example",
        "HTTP_ACCESS_CONTROL_REQUEST_METHOD": "POST",
        "HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "authorization,content-type",
    }
    ok = client.options("/api/v1/jobs", **ask)
    assert ok["Access-Control-Allow-Origin"] == "https://ui.example"
    assert "authorization" in ok["Access-Control-Allow-Headers"]
    other = client.options("/api/v1/jobs", **{**ask, "HTTP_ORIGIN": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in other


def test_the_openapi_schema_describes_every_endpoint_a_ui_needs() -> None:
    schema = Client().get("/api/v1/openapi.json").json()
    paths = set(schema["paths"])
    for path in ("/api/v1/jobs", "/api/v1/assets", "/api/v1/validate", "/api/v1/recipes",
                 "/api/v1/brand-kit", "/api/v1/usage", "/api/v1/jobs/{job_id}/cancel"):  # fmt: skip
        assert path in paths, path
    assert schema["info"]["title"] == "Atelier"


# -- a durable copy of every store ---------------------------------------------------------------


def test_with_a_backend_another_machine_serves_assets_and_results_it_never_held(
    settings: Any, tmp_path: Path, photo_bytes: bytes
) -> None:
    """The API and the workers can run on different machines: each organization's store is
    mirrored to the backend, and a node with an empty disk fetches what it needs."""
    import shutil

    from atelier.engine.mirror import DirBackend

    api_support.backend = DirBackend(tmp_path / "bucket")
    settings.ATELIER_OBJECT_BACKEND = "api_support.object_backend"
    _, api = make_org("acme")
    handle = photo_handle(api, photo_bytes)
    job = api.post("/jobs", {"spec": crop_spec(handle)}).json()
    assert job["status"] == "succeeded"
    (out,) = job["result"]["outputs"].values()
    shutil.rmtree(settings.ATELIER_STORE_ROOT)  # a fresh machine: nothing on local disk
    content = api.get(f"/assets/{out['handle']}/content")
    assert content.status_code == 200 and content["Content-Type"] == "image/jpeg"
    assert b"".join(content.streaming_content)[:2] == b"\xff\xd8"
    again = api.post("/jobs", {"spec": crop_spec(handle)}).json()  # the input is fetched too
    assert again["status"] == "succeeded"
    assert api.get(f"/assets/{handle}").json()["media"] == "image"


def test_a_key_that_starts_jobs_too_fast_is_slowed_down(
    api: Api, photo_bytes: bytes, settings: Any
) -> None:
    settings.ATELIER_JOBS_PER_MINUTE = 2
    handle = photo_handle(api, photo_bytes)
    for _ in range(2):
        assert api.post("/jobs", {"spec": crop_spec(handle)}).status_code == 202
    r = api.post("/jobs", {"spec": crop_spec(handle)})
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    _, other = make_org("globex")  # another key has its own allowance
    other_handle = photo_handle(other, photo_bytes)
    assert other.post("/jobs", {"spec": crop_spec(other_handle)}).status_code == 202
    settings.ATELIER_JOBS_PER_MINUTE = 0
    assert api.post("/jobs", {"spec": crop_spec(handle)}).status_code == 202  # 0 turns it off


def test_the_migrations_match_the_models_and_the_project_checks_clean() -> None:
    """A model change without a migration would break every deployment: fail here instead."""
    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO())
    call_command("check", stdout=io.StringIO())


def test_a_file_database_gets_its_directory_on_first_use(settings: Any, tmp_path: Path) -> None:
    """Found by running the real server: the default database path did not exist yet."""
    from django.apps import apps

    target = tmp_path / "fresh" / "api" / "db.sqlite3"
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(target)}}
    apps.get_app_config("core").ready()
    assert target.parent.is_dir() and not target.exists()  # the folder, not a stray file
