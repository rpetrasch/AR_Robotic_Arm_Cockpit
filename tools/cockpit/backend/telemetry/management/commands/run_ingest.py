"""Worker: telemetry from the agent into TimescaleDB, and the 10 Hz live stream
(docs/specs/spec_cockpit.md §4, §12a.3; telemetry.ingest).

    python manage.py run_ingest             # forever (started by cockpit.sh)

Every 2 s it reads the running stack from Docker, opens or closes its session and starts or
stops the agent streams. At start it applies the retention policies (TELEMETRY_RETENTION).
"""

import asyncio
import logging
import time

from django.core.management.base import BaseCommand

from core.runner import get_runner
from environment import state as stack_state
from telemetry import policies
from telemetry.ingest import Ingest

log = logging.getLogger("cockpit.ingest")
INTERVAL_S = 2.0


class Command(BaseCommand):
    help = "Store the agent's telemetry in TimescaleDB and relay the live stream."

    def handle(self, *args, **options):
        from monitoring.logs import install_handler

        install_handler("ingest")
        kept = policies.apply()
        log.info(
            "run_ingest: retention %s",
            ", ".join(f"{t} {d} d" for t, d in kept.items() if d) or "none",
        )
        asyncio.run(self.loop())

    async def loop(self):
        ingest = Ingest()
        try:
            while True:
                started = time.monotonic()
                try:
                    st = await stack_state.read(get_runner())
                    await ingest.step(st)
                except Exception:  # one bad poll must not stop the worker
                    log.exception("ingest poll failed")
                await asyncio.sleep(max(0.1, INTERVAL_S - (time.monotonic() - started)))
        finally:
            await ingest.stop()
