"""Telemetry without ROS: the sampling grid, decimation, packing, rings, JPEG, and the
WebSockets with a fake node."""

import io
import itertools
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ar3_cockpit_agent import telemetry
from ar3_cockpit_agent.app import create_app

TOKEN = "unit-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NAMES = ["joint_1", "joint_2"]


def test_rates_are_the_divisors_of_the_base_rate():
    assert telemetry.ticks_per_sample(50) == 2
    assert telemetry.ticks_per_sample(10) == 10
    assert 100 in telemetry.RATES and 3 not in telemetry.RATES
    with pytest.raises(ValueError):
        telemetry.ticks_per_sample(30)


def test_pack_orders_by_the_stream_names_and_marks_missing_values():
    joints = (["joint_2", "joint_1"], [0.2, 0.1], [2.0, 1.0], [])
    sample = telemetry.pack(NAMES, joints, (["joint_1"], [0.15], [1.5]))
    assert sample["p"] == [0.1, 0.2]
    assert sample["v"] == [1.0, 2.0]
    assert sample["e"] == [None, None]  # no effort in the message
    assert sample["cp"] == [0.15, None]
    assert "cp" not in telemetry.pack(NAMES, joints, None)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_clients_keep_every_kth_tick_of_the_same_grid():
    sampler = telemetry.Sampler(lambda _due: {"p": [0.0]})
    fast, slow = telemetry.Client(50), telemetry.Client(10)
    sampler.add(fast)
    sampler.add(slow)
    tick0 = 170_000_000_000  # any grid point; 100 ticks = 1 s
    for i in range(100):
        sampler.step(tick0 + i, (tick0 + i) / telemetry.BASE_HZ)
    got_fast, got_slow = fast.take(), slow.take()
    assert len(got_fast) == 50 and len(got_slow) == 10
    # the 10 Hz samples are a subset of the 50 Hz ones (same grid points)
    assert {s["t"] for s in got_slow} <= {s["t"] for s in got_fast}
    steps = [round(b["t"] - a["t"], 3) for a, b in itertools.pairwise(got_fast)]
    assert set(steps) == {0.02}


def test_no_fresh_state_means_no_sample():
    sampler = telemetry.Sampler(lambda _due: None)
    client = telemetry.Client(100)
    sampler.add(client)
    sampler.step(0, 0.0)
    assert client.take() == []


def test_a_client_that_does_not_read_loses_its_oldest_samples():
    client = telemetry.Client(100)
    for i in range(telemetry.QUEUE_MAX + 5):
        client.offer(i, {"t": i})
    taken = client.take()
    assert client.dropped == 5
    assert taken[0]["t"] == 5


def test_history_gives_the_message_of_the_ticks_own_time():
    h = telemetry.History(keep_s=1.0)
    for t in (0.0, 0.01, 0.02, 0.03):
        h.add(10.0 + t, f"m{t}")
    assert h.at(10.025) == (10.02, "m0.02")  # a late tick still gets its own state
    assert h.at(9.0) is None
    assert h.latest() == (10.03, "m0.03") and h.oldest() == (10.0, "m0.0")
    h.add(11.5, "late")
    assert len(h) == 1  # older than keep_s is dropped


def test_a_late_sampler_catches_up_with_its_own_grid_times():
    seen = []

    def snap(due):
        seen.append(due)
        return {"p": [due]}

    sampler = telemetry.Sampler(snap)
    client = telemetry.Client(100)
    sampler.add(client)
    period = 1 / telemetry.BASE_HZ
    for i in range(3):  # the thread wakes 0.3 s late: three ticks due, all caught up
        sampler.step(i, i * period, due=i * period)
    assert [round(d, 2) for d in seen] == [0.0, 0.01, 0.02]
    assert [s["p"][0] for s in client.take()] == seen


def test_ring_returns_what_is_newer():
    ring = telemetry.Ring(keep=3)
    for i in range(5):
        ring.push({"i": i})
    seq, items = ring.after(3)
    assert seq == 5 and [x["i"] for x in items] == [3, 4]
    assert ring.after(0)[1] == [{"i": 2}, {"i": 3}, {"i": 4}]  # only the last 3 kept


def test_rosout_line():
    msg = NS(
        stamp=NS(sec=10, nanosec=500_000_000),
        level=40,
        name="move_group",
        msg="planning failed",
        file="x.cpp",
        line=7,
    )
    line = telemetry.rosout_line(msg)
    assert line == {
        "t": 10.5,
        "level": "ERROR",
        "node": "move_group",
        "text": "planning failed",
        "file": "x.cpp",
        "line": 7,
    }


def test_trajectory_and_objects_and_safety():
    point = NS(positions=[0.1, 0.2], time_from_start=NS(sec=1, nanosec=250_000_000))
    msg = NS(trajectory=[NS(joint_trajectory=NS(joint_names=NAMES, points=[point]))])
    assert telemetry.trajectory(msg) == {"names": NAMES, "points": [{"t": 1.25, "p": [0.1, 0.2]}]}
    pose = NS(header=NS(frame_id="table"), pose=NS(position=NS(x=0.1, y=0.2, z=0.03)))
    obj = NS(
        id="b1", class_name="block", confidence=0.91, color="red", pose=pose, source_camera="c"
    )
    assert telemetry.objects(NS(objects=[obj]))[0]["xyz"] == [0.1, 0.2, 0.03]
    assert telemetry.safety(NS(estop=True, homed=False, in_workspace=True, faults=["x"]))["estop"]


def test_jpeg_from_rgb_and_bgr():
    from PIL import Image

    width, height = 640, 2
    data = bytes([255, 0, 0] * width * height)  # red in rgb8
    rgb = NS(encoding="rgb8", width=width, height=height, step=3 * width, data=data)
    out = Image.open(io.BytesIO(telemetry.jpeg(rgb, width=320)))
    assert out.size == (320, 1)
    assert out.getpixel((10, 0))[0] > 200
    bgr = NS(encoding="bgr8", width=width, height=height, step=3 * width, data=data)
    assert Image.open(io.BytesIO(telemetry.jpeg(bgr))).getpixel((10, 0))[2] > 200
    with pytest.raises(ValueError):
        telemetry.jpeg(NS(encoding="16UC1", width=1, height=1, step=2, data=b"\0\0"))


# --- the WebSockets and the new endpoints, with a fake node ------------------------------


class FakeAgent:
    mode = "sim"

    def __init__(self):
        self.sampler = telemetry.Sampler(lambda _due: {"p": [0.1, 0.2], "v": [0, 0], "e": [0, 0]})
        self.events = telemetry.Ring()
        self.rosout = telemetry.Ring()

    def joint_names(self):
        return NAMES

    def topic_rates(self):
        return {"/joint_states": {"hz": 100.0, "age_s": 0.01}}

    def rtf(self):
        return 0.98

    def node_params(self, name):
        if name.strip("/") != "move_group":
            raise KeyError(name)
        return [{"name": "use_sim_time", "value": True}]

    def measure_rate(self, topic, seconds):
        if topic != "/clock":
            raise KeyError(topic)
        return {"topic": topic, "hz": 1000.0, "messages": 2000, "window_s": seconds}

    def cameras(self):
        return ["cam_overhead"]

    def thumbnail(self, camera):
        if camera != "cam_overhead":
            raise KeyError(camera)
        return b"\xff\xd8jpeg"


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture
def client(agent):
    return TestClient(create_app(agent, TOKEN))


def test_websockets_need_the_token(client):
    for path in ("/ws/telemetry", "/ws/rosout"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(path) as ws:
                ws.receive_json()
        assert exc.value.code == 1008


def test_telemetry_stream_sends_samples_events_and_rates(client, agent):
    agent.events.push({"kind": "trajectory", "names": NAMES, "points": []})  # before: not sent
    with client.websocket_connect("/ws/telemetry?rate=50", headers=AUTH) as ws:
        deadline = time.monotonic() + 2
        while not agent.sampler._clients and time.monotonic() < deadline:  # added after accept
            time.sleep(0.01)
        time.sleep(0.02)
        for i in range(10):
            agent.sampler.step(i, i / 100)
        agent.events.push({"kind": "estop", "estop": True})
        msg = ws.receive_json()
        assert msg["type"] == "telemetry" and msg["names"] == NAMES
        assert len(msg["samples"]) == 5  # every 2nd of 10 ticks
        assert msg["rates"]["/joint_states"]["hz"] == 100.0 and msg["rtf"] == 0.98
        events = msg["events"] or ws.receive_json()["events"]
        assert [e["kind"] for e in events] == ["estop"]


def test_telemetry_rate_must_divide_the_base_rate(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/telemetry?rate=30", headers=AUTH) as ws:
            ws.receive_json()
    assert exc.value.code == 1003


def test_rosout_stream_from_a_sequence_number(client, agent):
    agent.rosout.push({"level": "INFO", "text": "old"})
    agent.rosout.push({"level": "ERROR", "text": "new"})
    with client.websocket_connect("/ws/rosout?after=1", headers=AUTH) as ws:
        msg = ws.receive_json()
    assert msg["seq"] == 2 and [line["text"] for line in msg["lines"]] == ["new"]


def test_params_rate_and_thumbnails(client):
    assert client.get("/node/params?name=/move_group", headers=AUTH).json()["params"][0] == {
        "name": "use_sim_time",
        "value": True,
    }
    assert client.get("/node/params?name=/nope", headers=AUTH).status_code == 404
    assert client.get("/topic/rate?name=/clock&seconds=1", headers=AUTH).json()["hz"] == 1000.0
    assert client.get("/topic/rate?name=/nope", headers=AUTH).status_code == 404
    assert client.get("/topics/rates", headers=AUTH).json()["/joint_states"]["hz"] == 100.0
    thumb = client.get("/cameras/cam_overhead/thumb.jpg", headers=AUTH)
    assert thumb.headers["content-type"] == "image/jpeg" and thumb.content.startswith(b"\xff\xd8")
    assert client.get("/cameras/nope/thumb.jpg", headers=AUTH).status_code == 404
