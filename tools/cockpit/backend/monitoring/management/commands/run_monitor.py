"""Worker: header status, health, alerts, metrics and logs (docs/specs/spec_cockpit.md §4, §10).

Every 2 s: the status snapshot (stack, component health, jobs, REC), the alert rules over it
(container_down within one poll of a container dying), push to /ws/status. Next to it:

- metrics at 1 Hz (host; containers and GPU every 5 s), cached for the Overview and the
  rules, written to the ``metrics`` hypertable while the session records;
- the log collector: ``docker logs -f`` of every running container and /rosout;
- hourly pruning: audit log (1 year), job logs (30 days / last 200), resolved alerts
  (90 days), log lines over LOG_KEEP_PER_SOURCE per source.

    python manage.py run_monitor            # forever (started by cockpit.sh)
    python manage.py run_monitor --once     # one snapshot, printed as JSON
"""

import asyncio
import json
import logging
import time

import yaml
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

from accounts import audit
from assistant.chat import prune_threads
from core import platform
from core.agent import AgentError, get_agent
from core.runner import get_runner
from environment import state as stack_state
from environment import status
from monitoring import alerts, logs, metrics
from monitoring.collector import Collector
from setup.service import prune_jobs
from telemetry import sessions
from telemetry.ingest import ingest_state

log = logging.getLogger("cockpit.monitor")
INTERVAL_S = 2.0
METRICS_S = 1.0
SLOW_METRICS_S = 5.0
PRUNE_EVERY_S = 3600.0
ALERTS_GROUP = "status"


class Command(BaseCommand):
    help = "Compute the header status every 2 s, evaluate the alerts, collect metrics and logs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="one snapshot, printed as JSON")

    def handle(self, *args, once: bool, **options):
        if once:
            self.stdout.write(json.dumps(asyncio.run(status.snapshot()), indent=2))
            return
        logs.install_handler("monitor")
        asyncio.run(Monitor().run())


def _limits_deg() -> dict:
    robot = yaml.safe_load((settings.REPO_DIR / "config/robot.yaml").read_text())
    return robot.get("joint_limits", {})


class Monitor:
    """``runner`` is taken fresh every poll: the fake runner answers from the state file as
    it was when the runner was made."""

    def __init__(self):
        self.collector = Collector(get_runner())
        self.metrics: dict = {"host": None, "containers": [], "gpus": []}
        self.session = None
        self.st = None
        self.limits = _limits_deg()

    async def run(self):
        log.info("run_monitor: status every %.0f s, metrics every %.0f s", INTERVAL_S, METRICS_S)
        tasks = [asyncio.ensure_future(self.metrics_loop())]
        try:
            await self.status_loop()
        finally:
            for t in tasks:
                t.cancel()
            await self.collector.stop()

    async def status_loop(self):
        layer = get_channel_layer()
        last_prune = 0.0
        while True:
            started = time.monotonic()
            try:
                runner = get_runner()
                self.collector.runner = runner
                self.st = st = await stack_state.read(runner)
                self.session = await sessions.sync(st, runner=runner)
                await self.collector.step(st, self.session.id if self.session else None)
                snap = await status.snapshot(runner, st=st)
                extra = await self.extra(snap, st)
                fired = alerts.firing(alerts.facts(snap, extra))
                result = await sync_to_async(alerts.apply)(
                    fired, self.session.id if self.session else None
                )
                if result["new"] or result["resolved"]:
                    status.with_alerts(snap, await sync_to_async(alerts.summary)())
                for alert in result["new"]:
                    log.warning("alert %s [%s]: %s", alert["rule"], alert["subject"],
                                alert["message"])  # fmt: skip
                    await layer.group_send(ALERTS_GROUP, {"type": "alert.new", "alert": alert})
                await cache.aset(status.CACHE_KEY, snap, timeout=30)
                await layer.group_send(status.GROUP, {"type": "status.update", "data": snap})
                if started - last_prune > PRUNE_EVERY_S:
                    await self.prune()
                    last_prune = started
            except Exception:  # one bad poll must not stop the monitor
                log.exception("status poll failed")
            await asyncio.sleep(max(0.1, INTERVAL_S - (time.monotonic() - started)))

    async def extra(self, snap: dict, st) -> dict:
        """Facts besides the snapshot: topic ages, joints, metrics, log matches, ingest."""
        topics = joints = None
        if snap["stop"]["available"]:  # the agent answers
            agent = get_agent()
            try:
                topics, joints = await asyncio.gather(agent.topic_rates(), agent.joints())
            except AgentError:
                pass
        dead_mode = st.dead_stacks[0][0].chip if st.dead_stacks and st.dead_stacks[0][0] else None
        if st.dead_stacks and dead_mode is None:
            dead_mode = "ROS · ?"
        return {
            "topics": topics,
            "joints": joints,
            "limits_deg": self.limits,
            "metrics": self.metrics,
            "log_matches": self.collector.take_matches(),
            "ingest": await ingest_state(),
            "dead_mode": dead_mode,
        }

    async def metrics_loop(self):
        last_slow = 0.0
        host_os = (await platform.current_host(get_runner())).os
        while True:
            started = time.monotonic()
            try:
                self.metrics["host"] = await asyncio.to_thread(metrics.host)
                if started - last_slow >= SLOW_METRICS_S and self.st is not None:
                    last_slow = started
                    names = [c.name for c in self.st.containers if c.state == "running"]
                    runner = get_runner()
                    self.metrics["containers"], self.metrics["gpus"] = await asyncio.gather(
                        metrics.containers(runner, names), metrics.gpus(runner, host_os)
                    )
                self.metrics["ts"] = time.time()
                await cache.aset(metrics.CACHE_KEY, self.metrics, timeout=30)
                session = self.session
                if session is not None and session.recording:
                    await sync_to_async(_write_metrics)(metrics.rows(self.metrics, session.id))
            except Exception:
                log.exception("metrics poll failed")
            await asyncio.sleep(max(0.05, METRICS_S - (time.monotonic() - started)))

    async def prune(self):
        pruned = await sync_to_async(audit.prune)()
        jobs = await sync_to_async(prune_jobs)()
        old_alerts = await sync_to_async(alerts.prune)()
        lines = await sync_to_async(logs.prune)()
        threads = await sync_to_async(prune_threads)()
        if pruned or jobs or old_alerts or lines or threads:
            log.info(
                "pruned %d audit entries, %d jobs, %d alerts, %d log lines, %d assistant threads",
                pruned, jobs, old_alerts, lines, threads,
            )  # fmt: skip


def _write_metrics(rows: list[tuple]) -> None:
    from django.db import connection

    if not rows:
        return
    with connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO metrics (time, session_id, name, labels, value) "
            "VALUES (%s, %s, %s, %s, %s)",
            rows,
        )
