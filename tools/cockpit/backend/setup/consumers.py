"""/ws/jobs/<id> (the live log of one job) and /ws/checks (checklist changes)
(docs/specs/spec_cockpit.md §8.3, §15)."""

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from . import executor, service
from .models import Job


class _Authenticated(AsyncJsonWebsocketConsumer):
    group = ""

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            # Accept first: a socket closed before the handshake reaches the browser as a
            # failed connection (HTTP 403) without the code; the client needs the code.
            await self.accept()
            await self.close(code=4401)
            return
        self.group = self.group_name()
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.on_open()

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    def group_name(self) -> str:
        raise NotImplementedError

    async def on_open(self):
        pass


class JobConsumer(_Authenticated):
    """Sends the job and its log so far, then live lines. Lines carry a ``seq``: the client
    drops duplicates between the backlog and the first live batch."""

    def group_name(self) -> str:
        return executor.job_group(self.scope["url_route"]["kwargs"]["job_id"])

    async def on_open(self):
        job_id = self.scope["url_route"]["kwargs"]["job_id"]
        job = await Job.objects.filter(pk=job_id).afirst()
        if job is None:
            await self.close(code=4404)  # already accepted (on_open runs after accept)
            return
        await self.send_json({"type": "job", "job": service.job_dict(job)})
        lines = await sync_to_async(service.read_log)(job)
        for i in range(0, len(lines), 500):
            await self.send_json({"type": "lines", "lines": lines[i : i + 500]})

    async def job_update(self, event):
        await self.send_json({"type": "job", "job": event["job"]})

    async def job_lines(self, event):
        await self.send_json({"type": "lines", "lines": event["lines"]})


class ChecksConsumer(_Authenticated):
    def group_name(self) -> str:
        return executor.CHECKS_GROUP

    async def checks_update(self, event):
        await self.send_json({"type": "checks", "ids": event["ids"]})
