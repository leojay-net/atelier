from __future__ import annotations

from pathlib import Path

from django.apps import AppConfig
from django.conf import settings


class CoreConfig(AppConfig):
    name = "atelier.api.core"
    label = "core"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """A file-based SQLite database needs its directory: create it on first use."""
        db = settings.DATABASES["default"]
        name = str(db.get("NAME", ""))
        if (
            "sqlite" in str(db.get("ENGINE", ""))
            and name
            and name != ":memory:"
            and "memory" not in name
        ):
            Path(name).parent.mkdir(parents=True, exist_ok=True)
