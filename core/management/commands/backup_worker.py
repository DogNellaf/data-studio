import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.services import claim_next_job, fail_stale_jobs, run_backup


class Command(BaseCommand):
    help = "Take queued backups. Runs until stopped, or once with --once."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process everything that is queued, then exit (for cron or tests).",
        )

    def handle(self, *args, once=False, **options):
        self._stopping = False
        if not once:
            # Текущую копию доснимаем, новых не берём: docker stop не обрывает дамп.
            signal.signal(signal.SIGTERM, self._stop)
            signal.signal(signal.SIGINT, self._stop)
            self.stdout.write("Backup worker started")

        processed = 0
        while not self._stopping:
            close_old_connections()
            stale = fail_stale_jobs()
            if stale:
                self.stdout.write(self.style.WARNING(f"Marked {stale} interrupted job(s) as failed"))

            job = claim_next_job()
            if job is None:
                if once:
                    break
                time.sleep(settings.BACKUP_WORKER_POLL_INTERVAL)
                continue

            self.stdout.write(f"Backup #{job.pk}: {job.type.code} of {job.db}@{job.host}")
            run_backup(job)
            processed += 1
            self.stdout.write(f"Backup #{job.pk}: {job.status}")

        if once:
            self.stdout.write(f"Processed {processed} job(s)")

    def _stop(self, signum, frame):
        self.stdout.write("Stopping after the current job")
        self._stopping = True
