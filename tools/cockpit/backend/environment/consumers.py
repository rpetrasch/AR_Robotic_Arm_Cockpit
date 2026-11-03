"""/ws/status: header status for signed-in users (docs/specs/spec_cockpit.md §7.2, §15).

On connect the consumer sends the last snapshot (or computes one); afterwards run_monitor
pushes every new snapshot to the ``status`` group.
"""

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache

from . import status


class StatusConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            # Accept first: a socket closed before the handshake reaches the browser as a
            # failed connection (HTTP 403) without the code; the client needs the code.
            await self.accept()
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(status.GROUP, self.channel_name)
        await self.accept()
        snap = await cache.aget(status.CACHE_KEY)
        if snap is None:
            snap = await status.snapshot()
        await self.send_json({"type": "status", "data": snap})

    async def disconnect(self, code):
        await self.channel_layer.group_discard(status.GROUP, self.channel_name)

    async def status_update(self, event):
        await self.send_json({"type": "status", "data": event["data"]})

    async def alert_new(self, event):
        """A new alert: the header shows a toast for error / critical (§10.5)."""
        await self.send_json({"type": "alert", "alert": event["alert"]})
