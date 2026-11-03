"""Worker: runs queued jobs (docs/specs/spec_cockpit.md §4, §8.3). A mutating job runs alone
(create_job refuses a second one); non-mutating jobs (a telemetry export, a rosbag recording)
run next to it, so a 10-minute recording does not block the queue. At start it also brings the
assistant's knowledge index up to date (in a thread, without a job).

    python manage.py run_jobs            # forever (started by cockpit.sh)
    python manage.py run_jobs --once     # run what is queued now, then exit (tests, CLI)

It polls the database every 0.5 s; a job that was running when the worker stopped is marked
failed on the next start (its process is gone).
"""

import asyncio
import logging
import threading

from django.core.management.base import BaseCommand
from django.utils import timezone

from monitoring.logs import install_handler
from setup import executor
from setup.models import Job

log = logging.getLogger("cockpit.jobs")
POLL_S = 0.5


def _refresh_index() -> None:
    """The assistant's index is re-indexed at start when a source file changed (§14.2)."""
    from django.db import connection

    from assistant import index

    try:
        if index.stale():
            index.refresh()
    except Exception:
        log.exception("assistant index refresh failed")
    finally:
        connection.close()


class Command(BaseCommand):
    help = "Run queued setup jobs (mutating ones one at a time)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="run the queue, then exit")

    def handle(self, *args, once: bool, **options):
        install_handler("jobs")
        orphans = Job.objects.filter(status=Job.RUNNING).update(
            status=Job.FAILED, finished=timezone.now(), exit_code=-1
        )
        if orphans:
            log.warning(
                "marked %d job(s) failed: they were running when the worker stopped", orphans
            )
        if not once:
            threading.Thread(target=_refresh_index, name="assistant-index", daemon=True).start()
        asyncio.run(self.loop(once))

    async def loop(self, once: bool):
        log.info("run_jobs: waiting for jobs")
        running: set[asyncio.Task] = set()
        while True:
            job = await executor.claim_next()
            if job is not None:
                log.info("job %s: %s", job.id, job.command)
                task = asyncio.ensure_future(self.run_one(job))
                running.add(task)
                task.add_done_callback(running.discard)
                continue
            if once and not running:
                return
            await asyncio.sleep(POLL_S)

    async def run_one(self, job: Job) -> None:
        job = await executor.execute(job)
        log.info("job %s: %s (exit %s)", job.id, job.status, job.exit_code)
