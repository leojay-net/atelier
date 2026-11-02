"""manage.py create_api_key <org-slug> <name> | --revoke <prefix>"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from atelier.api.core.models import ApiKey, Org


class Command(BaseCommand):
    help = "Issue an API key for an organization (shown once), or revoke one by its prefix."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("org", nargs="?")
        parser.add_argument("name", nargs="?", default="key")
        parser.add_argument("--revoke", metavar="PREFIX", help="revoke the key with this prefix")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["revoke"]:
            n = ApiKey.objects.filter(prefix=options["revoke"], revoked_at__isnull=True).update(
                revoked_at=timezone.now()
            )
            if not n:
                raise CommandError(f"no active key with prefix {options['revoke']!r}")
            self.stdout.write(f"revoked {options['revoke']}")
            return
        if not options["org"]:
            raise CommandError("give an organization slug, or --revoke PREFIX")
        org = Org.objects.filter(slug=options["org"]).first()
        if org is None:
            raise CommandError(f"no organization {options['org']!r}")
        _, token = ApiKey.issue(org, options["name"])
        self.stdout.write(f"API key (shown once): {token}")
