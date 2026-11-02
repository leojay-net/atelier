"""manage.py run_worker [--once] [--poll SECONDS]: run queued jobs until stopped.

Start as many workers as the machine can use; each claims jobs with an atomic update, so they
never run the same job twice. Every step of a job is still its own sandboxed subprocess.
"""

from __future__ import annotations

import signal
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from atelier.api.core import worker


class Command(BaseCommand):
    help = "Run queued jobs (the job worker)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true", help="exit when the queue is empty")
        parser.add_argument("--poll", type=float, default=0.5, help="seconds between checks")

    def handle(self, *args: Any, **options: Any) -> None:
        def stop(signum: int, frame: FrameType | None) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        self.stdout.write("worker started")
        try:
            done = worker.run_forever(poll=options["poll"], once=options["once"])
        except KeyboardInterrupt:
            self.stdout.write("worker stopped")
            return
        self.stdout.write(f"ran {done} job(s)")
