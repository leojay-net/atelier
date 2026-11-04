"""Django settings, read from the environment.

ATELIER_HOME            where data lives (default ~/.atelier); the database and every org's
                        content-addressed store are under <home>/api
ATELIER_SECRET_KEY      required unless ATELIER_DEBUG=1
ATELIER_DEBUG           1 to enable debug and a throwaway secret key
ATELIER_ALLOWED_HOSTS   comma-separated host names (default localhost,127.0.0.1)
ATELIER_DB_*            ENGINE (default sqlite3), NAME, USER, PASSWORD, HOST, PORT (Postgres)
ATELIER_QUEUE           "database" (workers poll; default) or "inline" (run at once, for tests)
ATELIER_MAX_UPLOAD_MB   largest upload (default 200)
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


HOME = Path(os.environ.get("ATELIER_HOME", Path.home() / ".atelier"))
DATA_DIR = HOME / "api"
DEBUG = _flag("ATELIER_DEBUG")

SECRET_KEY = os.environ.get("ATELIER_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("set ATELIER_SECRET_KEY (or ATELIER_DEBUG=1 for development)")
    SECRET_KEY = "insecure-development-key-do-not-use"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ATELIER_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "corsheaders",
    "atelier.api.core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "atelier.api.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
ASGI_APPLICATION = "atelier.api.asgi.application"

_engine = os.environ.get("ATELIER_DB_ENGINE", "django.db.backends.sqlite3")
if "sqlite" in _engine:
    DATABASES = {
        "default": {
            "ENGINE": _engine,
            "NAME": os.environ.get("ATELIER_DB_NAME", DATA_DIR / "db.sqlite3"),
            # The API and several workers write at once (job claims, progress events): WAL lets
            # readers run beside a writer, IMMEDIATE takes the write lock up front instead of
            # failing mid-transaction, and the timeout waits for it instead of "database is locked".
            "OPTIONS": {
                "timeout": 20,
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": _engine,
            "NAME": os.environ.get("ATELIER_DB_NAME", "atelier"),
            "USER": os.environ.get("ATELIER_DB_USER", ""),
            "PASSWORD": os.environ.get("ATELIER_DB_PASSWORD", ""),
            "HOST": os.environ.get("ATELIER_DB_HOST", ""),
            "PORT": os.environ.get("ATELIER_DB_PORT", ""),
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
LANGUAGE_CODE = "en-us"

# A web UI served from another origin needs CORS: list its origins, comma-separated.
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ATELIER_CORS_ORIGINS", "").split(",") if o.strip()
]
CORS_ALLOW_HEADERS = ["authorization", "content-type", "last-event-id", "accept"]
CORS_EXPOSE_HEADERS = ["content-disposition"]
CORS_URLS_REGEX = r"^/api/.*$"

# Atelier
ATELIER_STORE_ROOT = DATA_DIR / "orgs"  # one content-addressed store per organization
ATELIER_QUEUE = os.environ.get("ATELIER_QUEUE", "database")
ATELIER_JOBS_PER_MINUTE = int(
    os.environ.get("ATELIER_JOBS_PER_MINUTE", "60")
)  # per API key; 0 = no limit
ATELIER_MAX_UPLOAD_BYTES = int(os.environ.get("ATELIER_MAX_UPLOAD_MB", "200")) * 1024 * 1024
# Optional durable copy of every organization's store (S3 or compatible). Empty: local disk only.
ATELIER_S3_BUCKET = os.environ.get("ATELIER_S3_BUCKET", "")
ATELIER_S3_PREFIX = os.environ.get("ATELIER_S3_PREFIX", "atelier")
ATELIER_S3_ENDPOINT = os.environ.get("ATELIER_S3_ENDPOINT", "")  # e.g. a MinIO URL
ATELIER_OBJECT_BACKEND = (
    "atelier.api.core.clients.object_backend"  # a factory: () -> backend | None
)
# A factory: () -> ModelAdapter. Read from the environment (docker-compose sets it); the default is
# Vultr Serverless Inference. Also: atelier.api.core.clients.{anthropic,openai,gemini,ollama}_client
ATELIER_MODEL_CLIENT = os.environ.get(
    "ATELIER_MODEL_CLIENT", "atelier.api.core.clients.vultr_client"
)
# The model name for ATELIER_MODEL_CLIENT's provider. Empty keeps AgentConfig's own default
# (Claude's DEFAULT_MODEL) -- set this whenever the client above isn't anthropic_client, e.g.
# "llama3.1:8b-instruct-q4_K_M" for ollama_client, "gpt-4o" for openai_client, "gemini-2.5-flash"
# for gemini_client.
ATELIER_MODEL_NAME = os.environ.get("ATELIER_MODEL_NAME", "")
# Tests only: let chat links reach a local test server (production refuses private addresses).
ATELIER_LINKS_ALLOW_PRIVATE = False
# A factory: () -> VoiceService | None, for the agent's make_voiceover tool (Vultr TTS).
ATELIER_VOICE_CLIENT = os.environ.get(
    "ATELIER_VOICE_CLIENT", "atelier.agent.voice.from_environment"
)
ATELIER_JOB_WALL_SECONDS = 600.0
ATELIER_STALE_JOB_SECONDS = 900.0  # a running job with no heartbeat for this long is failed
DATA_UPLOAD_MAX_MEMORY_SIZE = ATELIER_MAX_UPLOAD_BYTES
SECURE_CONTENT_TYPE_NOSNIFF = True
