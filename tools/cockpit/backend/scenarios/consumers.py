"""/ws/runs/<run_id>: a run live (docs/specs/spec_cockpit.md §12.3 run panel, §15).

On connect: the run as stored and its events so far (events.jsonl); then every event the
run's job relays from the agent (group ``run.<id>``)."""

from __future__ import annotations

import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from telemetry.models import Run

from . import execute


class RunConsumer(AsyncJsonWebsocketConsumer):
    group = ""

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            # Accept first: a socket closed before the handshake reaches the browser as a
            # failed connection (HTTP 403) without the code; the client needs the code.
            await self.accept()
            await self.close(code=4401)
            return
        run_id = self.scope["url_route"]["kwargs"]["run_id"]
        run = await Run.objects.filter(pk=run_id).afirst()
        if run is None:
            await self.accept()
            await self.close(code=4404)
            return
        self.group = execute.group(run_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        events = await sync_to_async(_events)(run_id)
        await self.send_json({"type": "run", "state": execute.run_dict(run), "events": events})

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def run_event(self, event):
        await self.send_json({"type": "run", "state": event.get("state"),
                              "events": event.get("events", [])})  # fmt: skip


def _events(run_id) -> list[dict]:
    p = execute.runs_dir() / str(run_id) / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()[-2000:] if line.strip()]
