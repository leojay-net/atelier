"""Email + password accounts: sign-up, sign-in, sign-out, and that each account is private."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from django.utils import timezone
from test_api import Api

from atelier.api.core import accounts
from atelier.api.core.models import ApiKey, Member
from conftest import FIXTURES

pytestmark = pytest.mark.django_db(transaction=True)

PASSWORD = "violet-harbor-42"


@pytest.fixture(autouse=True)
def isolated(settings: Any, tmp_path: Path) -> None:
    settings.ATELIER_STORE_ROOT = tmp_path / "orgs"
    settings.ATELIER_QUEUE = "inline"
    cache.clear()


def signup(email: str, password: str = PASSWORD, confirm: str | None = None) -> Any:
    return Api().post(
        "/auth/signup",
        {"email": email, "password": password, "password_confirm": confirm or password},
    )


def login(email: str, password: str = PASSWORD) -> Any:
    return Api().post("/auth/login", {"email": email, "password": password})


def session(email: str) -> Api:
    r = signup(email)
    assert r.status_code == 201, r.json()
    return Api(r.json()["token"])


def test_signup_gives_a_working_session_in_a_new_private_workspace() -> None:
    r = signup("Ada@Example.com ")
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["user"]["email"] == "ada@example.com" and body["expires_in"] == 30 * 86400
    me = Api(body["token"]).get("/me").json()
    assert me["user"] == {"email": "ada@example.com"} and me["org"]["slug"] == body["org"]["slug"]
    assert Member.objects.get(user__email="ada@example.com").role == "owner"


@pytest.mark.parametrize(
    ("email", "password", "confirm", "code"),
    [
        ("not-an-email", PASSWORD, PASSWORD, "invalid_email"),
        ("a@b.co", PASSWORD, PASSWORD + "x", "password_mismatch"),
        ("a@b.co", "short1", "short1", "weak_password"),
        ("a@b.co", "12345678901", "12345678901", "weak_password"),
        ("a@b.co", "password123", "password123", "weak_password"),
    ],
)
def test_signup_rejects_bad_input(email: str, password: str, confirm: str, code: str) -> None:
    r = signup(email, password, confirm)
    assert r.status_code == 400 and r.json()["error"] == code, r.json()


def test_one_account_per_email_ignoring_case() -> None:
    assert signup("ada@example.com").status_code == 201
    r = signup("ADA@example.com")
    assert r.status_code == 409 and r.json()["error"] == "email_taken"


def test_login_issues_a_new_session_and_rejects_a_wrong_password() -> None:
    signup("ada@example.com")
    assert login("ada@example.com", "wrong-password").json()["error"] == "invalid_credentials"
    assert login("nobody@example.com").status_code == 401  # same answer for an unknown email
    r = login("ADA@example.com")
    assert r.status_code == 200 and Api(r.json()["token"]).get("/me").status_code == 200


def test_repeated_wrong_passwords_lock_the_email_for_a_while() -> None:
    signup("ada@example.com")
    for _ in range(accounts.MAX_FAILED_LOGINS):
        assert login("ada@example.com", "wrong-password").status_code == 401
    r = login("ada@example.com")  # even the right password, until the pause ends
    assert r.status_code == 429 and r.json()["error"] == "too_many_attempts"


def test_logout_revokes_only_that_session() -> None:
    first = session("ada@example.com")
    second = Api(login("ada@example.com").json()["token"])
    assert first.post("/auth/logout").status_code == 204
    assert first.get("/me").status_code == 401
    assert second.get("/me").status_code == 200


def test_logout_leaves_an_ordinary_api_key_alone() -> None:
    ada = session("ada@example.com")
    org = Member.objects.get(user__email="ada@example.com").org
    _, token = ApiKey.issue(org, "ci")
    ci = Api(token)
    assert ci.post("/auth/logout").status_code == 204
    assert ci.get("/me").status_code == 200 and ada.get("/me").status_code == 200


def test_a_session_expires_after_its_lifetime() -> None:
    ada = session("ada@example.com")
    ApiKey.objects.filter(name=accounts.SESSION_KEY_NAME).update(
        created_at=timezone.now() - timedelta(days=accounts.SESSION_DAYS, minutes=1)
    )
    assert ada.get("/me").status_code == 401


def test_users_cannot_see_or_use_each_others_files_and_jobs() -> None:
    """The reason accounts exist: one person's uploads are invisible to everyone else."""
    ada, bob = session("ada@example.com"), session("bob@example.com")
    photo = (FIXTURES / "photo.png").read_bytes()
    handle = ada.upload("holiday.png", photo).json()["handle"]
    job = ada.post(
        "/jobs", {"spec": {"in": handle, "ops": [["export", {"preset": "web_jpeg"}]]}}
    ).json()

    assert bob.get("/assets").json()["count"] == 0
    assert bob.get(f"/assets/{handle}").status_code == 404
    assert bob.get(f"/assets/{handle}/content").status_code == 404
    assert bob.get("/jobs").json()["count"] == 0
    assert bob.get(f"/jobs/{job['id']}").status_code == 404
    r = bob.post("/jobs", {"spec": {"in": handle, "ops": [["export", {"preset": "web_jpeg"}]]}})
    assert r.status_code in (404, 422), r.json()  # cannot even reference it

    # The same bytes uploaded by Bob are Bob's own copy, still invisible to Ada's listing count
    assert bob.upload("mine.png", photo).status_code == 201
    assert ada.get("/assets").json()["count"] == 2  # her upload and her job's output
    assert bob.get("/assets").json()["count"] == 1
