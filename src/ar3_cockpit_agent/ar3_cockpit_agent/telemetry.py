"""Telemetry of the cockpit agent (docs/specs/spec_cockpit.md §10.3, §12a.3): what the WebSockets
send.

Pure Python, unit-tested without ROS. The node keeps the latest messages; a sampler thread
takes a snapshot on a fixed grid of ``BASE_HZ`` and hands it to every connected client, which
keeps every k-th tick for its rate (``RATES``: the divisors of BASE_HZ). The grid makes the
stored rate exact: 50 Hz gives one sample every 20 ms of wall time, whatever the rate of
/joint_states, and a missing sample is a real gap (no /joint_states for ``FRESH_S``, or the
agent lagged by more than ``MAX_LAG_S``).

A sample is sample-and-hold of /joint_states (position, velocity, effort) and the controller
state (commanded position and velocity): the message that was the latest at the tick's grid
time. The node keeps the last second of both with their receive times (``History``), so a
tick the thread runs late (GIL, a busy executor) still gets the state of its own time instead
of being dropped; only a pause longer than ``MAX_LAG_S`` leaves a gap. Joint order is fixed per
stream and sent in every message (``names``), so a message can be read on its own.
"""

from __future__ import annotations

import io
import threading
import time
from collections import deque
from collections.abc import Callable

BASE_HZ = 100
RATES = tuple(r for r in range(1, BASE_HZ + 1) if BASE_HZ % r == 0)
FRESH_S = 0.5  # no message for this long (now): the stream stopped, ticks are gaps
MAX_LAG_S = 1.0  # later ticks are skipped (counted); earlier late ones are caught up
HISTORY_S = 1.5  # how much of the message history ``History`` keeps
BATCH_S = 0.1  # samples per WebSocket message: everything of the last 100 ms
QUEUE_MAX = 2000  # per client; a client that does not read loses its oldest samples
ROSOUT_KEEP = 2000
# rcl_interfaces/msg/Log levels
LEVELS = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "FATAL"}


def ticks_per_sample(rate: int) -> int:
    if rate not in RATES:
        raise ValueError(f"rate must be one of {RATES}")
    return BASE_HZ // rate


def _floats(values) -> list[float | None]:
    return [None if v != v else round(float(v), 6) for v in values]  # NaN -> None


def pack(names: list[str], joints, controller) -> dict:
    """One sample. ``joints``: (names, position, velocity, effort) of the latest JointState;
    ``controller``: (names, cmd position, cmd velocity) or None."""
    jn, pos, vel, eff = joints
    index = {n: i for i, n in enumerate(jn)}

    def pick(values, n):
        i = index.get(n)
        return values[i] if i is not None and i < len(values) else float("nan")

    sample = {
        "p": _floats(pick(pos, n) for n in names),
        "v": _floats(pick(vel, n) for n in names),
        "e": _floats(pick(eff, n) for n in names),
    }
    if controller is not None:
        cn, cpos, cvel = controller
        cindex = {n: i for i, n in enumerate(cn)}

        def cpick(values, n):
            i = cindex.get(n)
            return values[i] if i is not None and i < len(values) else float("nan")

        sample["cp"] = _floats(cpick(cpos, n) for n in names)
        sample["cv"] = _floats(cpick(cvel, n) for n in names)
    return sample


class History:
    """Messages with their monotonic receive time, the last ``HISTORY_S`` seconds."""

    def __init__(self, keep_s: float = HISTORY_S):
        self.keep_s = keep_s
        self._items: deque[tuple[float, object]] = deque()

    def add(self, at: float, msg) -> None:
        self._items.append((at, msg))
        while self._items and at - self._items[0][0] > self.keep_s:
            self._items.popleft()

    def at(self, t: float) -> tuple[float, object] | None:
        """The latest message received at or before ``t`` (receive time, message)."""
        found = None
        for item in reversed(self._items):
            if item[0] <= t:
                found = item
                break
        return found

    def latest(self) -> tuple[float, object] | None:
        return self._items[-1] if self._items else None

    def oldest(self) -> tuple[float, object] | None:
        return self._items[0] if self._items else None

    def __len__(self) -> int:
        return len(self._items)


class Client:
    """One WebSocket client of the sample stream: its rate and its queue."""

    def __init__(self, rate: int):
        self.every = ticks_per_sample(rate)
        self.rate = rate
        self.queue: deque[dict] = deque(maxlen=QUEUE_MAX)
        self.dropped = 0
        self._lock = threading.Lock()

    def offer(self, tick: int, sample: dict) -> None:
        if tick % self.every:
            return
        with self._lock:
            if len(self.queue) == self.queue.maxlen:
                self.dropped += 1
            self.queue.append(sample)

    def take(self) -> list[dict]:
        with self._lock:
            out = list(self.queue)
            self.queue.clear()
            return out


class Sampler:
    """Calls ``snapshot(due)`` BASE_HZ times a second on a fixed wall-clock grid and offers the
    result to every client. ``due`` is the tick's grid time on the monotonic clock; the
    snapshot returns None when there is nothing fresh at that time."""

    def __init__(
        self, snapshot: Callable[[float], dict | None], clock=time.monotonic, wall=time.time
    ):
        self._snapshot = snapshot
        self._clock, self._wall = clock, wall
        self._clients: set[Client] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.skipped = 0  # ticks skipped because the thread was late (counted, not hidden)
        self._thread: threading.Thread | None = None

    def add(self, client: Client) -> None:
        with self._lock:
            self._clients.add(client)

    def remove(self, client: Client) -> None:
        with self._lock:
            self._clients.discard(client)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def step(self, tick: int, stamp: float, due: float | None = None) -> None:
        """One tick: ``stamp`` is its wall time on the grid, ``due`` its monotonic time."""
        with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        sample = self._snapshot(self._clock() if due is None else due)
        if sample is None:
            return
        sample = {"t": round(stamp, 3), **sample}
        for c in clients:
            c.offer(tick, sample)

    def _run(self) -> None:
        period = 1.0 / BASE_HZ
        mono0, wall0 = self._clock(), self._wall()
        # Ticks are numbered from the epoch so that every client's k-th tick is the same
        # grid point (wall time a multiple of k / BASE_HZ), also across reconnects.
        tick0 = round(wall0 * BASE_HZ)
        i = 0
        while not self._stop.is_set():
            due = mono0 + i * period
            now = self._clock()
            if now < due:
                time.sleep(due - now)
                continue
            if now - due > MAX_LAG_S:
                skip = int((now - due) / period)
                self.skipped += skip
                i += skip
                continue
            tick = tick0 + i
            self.step(tick, tick / BASE_HZ, due)
            i += 1


class Ring:
    """The last ``keep`` items with a running sequence number, for /rosout and events."""

    def __init__(self, keep: int = ROSOUT_KEEP):
        self._items: deque[tuple[int, dict]] = deque(maxlen=keep)
        self._seq = 0
        self._lock = threading.Lock()

    def push(self, item: dict) -> int:
        with self._lock:
            self._seq += 1
            self._items.append((self._seq, item))
            return self._seq

    def after(self, seq: int) -> tuple[int, list[dict]]:
        """Items newer than ``seq`` and the newest sequence number."""
        with self._lock:
            return self._seq, [item for s, item in self._items if s > seq]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq


def rosout_line(msg) -> dict:
    """An rcl_interfaces/msg/Log as a log line for the cockpit (§10.2)."""
    return {
        "t": msg.stamp.sec + msg.stamp.nanosec / 1e9,
        "level": LEVELS.get(int(msg.level), str(msg.level)),
        "node": msg.name,
        "text": msg.msg,
        "file": msg.file,
        "line": int(msg.line),
    }


def trajectory(msg) -> dict:
    """moveit_msgs/DisplayTrajectory → joint names and waypoints (first trajectory)."""
    out = {"names": [], "points": []}
    for traj in msg.trajectory:
        jt = traj.joint_trajectory
        out["names"] = list(jt.joint_names)
        for p in jt.points:
            t = p.time_from_start.sec + p.time_from_start.nanosec / 1e9
            out["points"].append({"t": round(t, 4), "p": _floats(p.positions)})
        break
    return out


def objects(msg) -> list[dict]:
    """ar3_interfaces/DetectedObjectArray (SPEC §4), read by field name: the type is resolved
    at runtime, so the agent does not depend on a package that does not exist yet."""
    out = []
    for o in getattr(msg, "objects", []):
        pose = o.pose.pose
        out.append(
            {
                "id": o.id,
                "class": o.class_name,
                "confidence": round(float(o.confidence), 3),
                "color": getattr(o, "color", ""),
                "frame": o.pose.header.frame_id,
                "xyz": [
                    round(pose.position.x, 4),
                    round(pose.position.y, 4),
                    round(pose.position.z, 4),
                ],
                "camera": getattr(o, "source_camera", ""),
            }
        )
    return out


def safety(msg) -> dict:
    """ar3_interfaces/SafetyStatus (SPEC §4), read by field name."""
    return {
        "estop": bool(getattr(msg, "estop", False)),
        "homed": bool(getattr(msg, "homed", False)),
        "in_workspace": bool(getattr(msg, "in_workspace", True)),
        "faults": list(getattr(msg, "faults", [])),
    }


def jpeg(msg, width: int = 320, quality: int = 70) -> bytes:
    """sensor_msgs/Image (rgb8, bgr8, mono8, rgba8, bgra8) → a JPEG ``width`` pixels wide."""
    import numpy as np
    from PIL import Image

    channels = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}
    enc = msg.encoding
    if enc not in channels:
        raise ValueError(f"unsupported encoding {enc}")
    c = channels[enc]
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    rows = raw.reshape(msg.height, msg.step)[:, : msg.width * c]
    arr = rows.reshape(msg.height, msg.width, c) if c > 1 else rows
    if enc in ("bgr8", "bgra8"):
        arr = arr[:, :, [2, 1, 0]]
    elif enc == "rgba8":
        arr = arr[:, :, :3]
    img = Image.fromarray(arr if c > 1 else arr, "RGB" if c > 1 else "L")
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()
