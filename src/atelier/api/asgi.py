"""ASGI entry point: `uvicorn atelier.api.asgi:application`."""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "atelier.api.settings")
application = get_asgi_application()
