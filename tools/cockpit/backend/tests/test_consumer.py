import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from environment import status
from environment.consumers import StatusConsumer

pytestmark = pytest.mark.django_db(transaction=True)


async def test_anonymous_socket_is_closed():
    comm = WebsocketCommunicator(StatusConsumer.as_asgi(), "/ws/status")
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected  # accepted, then closed with the code the browser can see
    assert await comm.receive_output() == {"type": "websocket.close", "code": 4401}


async def test_signed_in_socket_gets_snapshot_and_pushes(settings, user):
    settings.FAKE = True
    comm = WebsocketCommunicator(StatusConsumer.as_asgi(), "/ws/status")
    comm.scope["user"] = await sync_to_async(lambda: user)()
    connected, _ = await comm.connect()
    assert connected
    first = await comm.receive_json_from(timeout=5)
    assert first["type"] == "status" and first["data"]["host"]["os"] == "macos"
    await get_channel_layer().group_send(
        status.GROUP, {"type": "status.update", "data": {"overall": "OK"}}
    )
    assert (await comm.receive_json_from(timeout=5))["data"] == {"overall": "OK"}
    await comm.disconnect()


async def test_full_asgi_stack_rejects_foreign_origins():
    from config.asgi import application

    comm = WebsocketCommunicator(
        application, "/ws/status", headers=[(b"origin", b"http://evil.example")]
    )
    connected, _ = await comm.connect()
    assert not connected
