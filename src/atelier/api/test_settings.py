"""Settings for tests: an in-memory database, inline jobs, a throwaway secret."""

from __future__ import annotations

import os

os.environ.setdefault("ATELIER_DEBUG", "1")

from atelier.api.settings import *  # noqa: F403

ALLOWED_HOSTS = ["testserver", "localhost"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ATELIER_QUEUE = "inline"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast, for tests only
