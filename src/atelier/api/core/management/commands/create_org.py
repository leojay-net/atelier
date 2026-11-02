"""manage.py create_org <slug> "<name>" [--units N] [--compute-seconds N] [--key-name NAME]"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from atelier.api.core.models import ApiKey, Org


class Command(BaseCommand):
    help = "Create an organization (and, unless --no-key, its first API key, shown once)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("slug")
        parser.add_argument("name")
        parser.add_argument("--units", type=int, help="monthly model-unit limit")
        parser.add_argument("--compute-seconds", type=float, help="monthly compute-seconds limit")
        parser.add_argument("--key-name", default="default")
        parser.add_argument("--no-key", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if Org.objects.filter(slug=options["slug"]).exists():
            raise CommandError(f"an organization {options['slug']!r} already exists")
        org = Org.objects.create(
            slug=options["slug"],
            name=options["name"],
            monthly_units_limit=options["units"],
            monthly_compute_seconds_limit=options["compute_seconds"],
        )
        self.stdout.write(f"created organization {org.slug}")
        if not options["no_key"]:
            _, token = ApiKey.issue(org, options["key_name"])
            self.stdout.write(f"API key (shown once): {token}")
