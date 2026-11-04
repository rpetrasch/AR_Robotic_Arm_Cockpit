"""The ROS side of the cockpit agent: what it observes, telemetry, and the software stop.

The node runs in a MultiThreadedExecutor on a background thread (main.py). The HTTP handlers
call its public methods from the server thread; those only read snapshots under a lock or
wait on futures that the executor completes. The telemetry sampler (telemetry.Sampler) is a
third thread reading the latest messages under the same lock.

Topics that belong to later SPEC phases (/safety/status, /perception/objects) are subscribed
when they appear in the graph, with the type the graph reports, so the agent does not depend
on ar3_interfaces.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import yaml
from action_msgs.srv import CancelGoal
from control_msgs.msg import JointTrajectoryControllerState
from controller_manager_msgs.srv import ListControllers
from moveit_msgs.msg import DisplayTrajectory
from rcl_interfaces.msg import Log, ParameterDescriptor
from rcl_interfaces.srv import GetParameters, ListParameters
from rclpy.action import get_action_server_names_and_types_by_node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.parameter import parameter_value_to_python
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from rosidl_runtime_py.utilities import get_message
from sensor_msgs.msg import CameraInfo, Image, JointState
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory

from . import health, telemetry

POLICY_TOPIC = "/cockpit/policy"
# Action types the software stop cancels, MoveIt first so it does not re-send a trajectory.
STOP_ACTION_TYPES = (
    "moveit_msgs/action/MoveGroup",
    "moveit_msgs/action/ExecuteTrajectory",
    "control_msgs/action/FollowJointTrajectory",
    "control_msgs/action/GripperCommand",
    "control_msgs/action/ParallelGripperCommand",
)
# A trajectory with the controller's joint names and no points makes a joint_trajectory_controller
# hold its current position (without the names the controller rejects it).
HOLD_TOPIC_TYPE = "trajectory_msgs/msg/JointTrajectory"
REFRESH_S = 1.0
CALL_TIMEOUT_S = 2.0
TELEMETRY_DEFAULTS = {
    "rate_topics": ["/joint_states", "/clock"],
    "controller_state": "/joint_trajectory_controller/controller_state",
    "planned_path": "/display_planned_path",
    "safety": "/safety/status",
    "objects": "/perception/objects",
    "thumbnails": {"max_fps": 5, "width": 320, "idle_s": 10},
}
SAFETY_FRESH_S = 1.0
PARAM_BATCH = 50


def _desc(text: str) -> ParameterDescriptor:
    return ParameterDescriptor(description=text)


def _latched() -> QoSProfile:
    return QoSProfile(
        depth=1,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        reliability=ReliabilityPolicy.RELIABLE,
    )


class AgentNode(Node):
    def __init__(self):
        super().__init__("ar3_cockpit_agent")
        self.declare_parameter(
            "mode", "mock", _desc("mock | sim | real | remote (what this stack runs)")
        )
        self.declare_parameter(
            "config_dir", "", _desc("Site config directory (default: $AR3_CONFIG_DIR)")
        )
        self.declare_parameter(
            "controller_manager", "/controller_manager", _desc("controller_manager node")
        )
        self.declare_parameter("move_group", "move_group", _desc("move_group node name"))
        self.declare_parameter(
            "gz_world", "ar3_workcell", _desc("Gazebo world name (sim): where runs spawn models")
        )
        self.mode = self.get_parameter("mode").value
        if self.mode not in health.MODES:
            raise ValueError(f"mode must be one of {health.MODES}, got {self.mode!r}")
        # Secrets and switches come from the environment, never from ROS parameters
        # (`ros2 param get` would show them to everyone in the graph).
        self.remote_motion = health.env_flag(os.environ.get("COCKPIT_REMOTE_MOTION"))
        self.host = os.environ.get("COCKPIT_HOST_LABEL") or socket.gethostname()
        self.started = time.monotonic()

        self._state_lock = threading.Lock()
        self._clock_meter = health.RateMeter()
        self._sim_time = (0, time.monotonic())  # (last sim ns, when it last advanced)
        self._camera_rates: dict[str, health.RateMeter] = {}
        self._expected_cameras = self._read_cameras()
        self._controllers: list[dict] | None = None
        self._graph: dict = {"move_group": False, "move_action": False}
        self._joints: JointState | None = None
        self._joints_at = 0.0  # monotonic receive time
        self._joint_history = telemetry.History()  # for the sampler: the state at each tick
        self._controller_history = telemetry.History()
        self._names: list[str] = []  # joint order of the telemetry stream (first message)
        self._controller: tuple | None = None
        self._controller_at = 0.0
        self._safety: dict | None = None
        self._safety_at = 0.0
        self._clock_window: list[tuple[float, int]] = []  # (monotonic, sim ns) for the RTF
        self._owner_policy: dict | None = None
        self.arm = None  # REAL arm state: from ar3_safety in SPEC Phase 1

        sensors = ReentrantCallbackGroup()
        self.create_subscription(Clock, "/clock", self._on_clock, 10, callback_group=sensors)
        self.create_subscription(
            JointState, "/joint_states", self._on_joints, 10, callback_group=sensors
        )
        if self.mode == "sim":
            for name in self._expected_cameras:
                self._camera_rates[name] = health.RateMeter()
                self.create_subscription(
                    CameraInfo,
                    f"/{name}/camera_info",
                    lambda _msg, n=name: self._tick_camera(n),
                    qos_profile_sensor_data,
                    callback_group=sensors,
                )
        if self.mode == "remote":
            self.create_subscription(String, POLICY_TOPIC, self._on_policy, _latched())
        else:
            self._policy_pub = self.create_publisher(String, POLICY_TOPIC, _latched())
            self._publish_policy()

        self._calls = ReentrantCallbackGroup()
        cm = self.get_parameter("controller_manager").value
        self._list_controllers = self.create_client(
            ListControllers, f"{cm}/list_controllers", callback_group=self._calls
        )
        self._cancel_clients: dict[str, object] = {}
        self._hold_pubs: dict[str, object] = {}
        self._setup_telemetry(sensors)
        self._srdf = ""
        self.create_subscription(
            String, "/robot_description_semantic", self._on_srdf, _latched(), callback_group=sensors
        )
        self.gz_world = self.get_parameter("gz_world").value
        from .runs import Runs

        self.runs = Runs(self)
        self.create_timer(REFRESH_S, self._refresh, callback_group=MutuallyExclusiveCallbackGroup())

    # --- observations ----------------------------------------------------------------------

    def _read_cameras(self) -> dict[str, float]:
        config_dir = self.get_parameter("config_dir").value or os.environ.get("AR3_CONFIG_DIR")
        path = Path(config_dir or "") / "cameras.yaml"
        if not config_dir or not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        return {name: float(cam.get("rate", 0)) for name, cam in data.items() if cam.get("rate")}

    def _on_clock(self, msg: Clock) -> None:
        now = time.monotonic()
        ns = msg.clock.sec * 1_000_000_000 + msg.clock.nanosec
        with self._state_lock:
            self._clock_meter.tick(now)
            if ns > self._sim_time[0]:
                self._sim_time = (ns, now)
            self._clock_window.append((now, ns))
            while self._clock_window and now - self._clock_window[0][0] > 2.0:
                self._clock_window.pop(0)

    def _tick_camera(self, name: str) -> None:
        with self._state_lock:
            self._camera_rates[name].tick(time.monotonic())

    def _on_joints(self, msg: JointState) -> None:
        with self._state_lock:
            self._joints = msg
            self._joints_at = time.monotonic()
            self._joint_history.add(self._joints_at, msg)
            if not self._names:
                self._names = list(msg.name)

    def _on_srdf(self, msg: String) -> None:
        with self._state_lock:
            self._srdf = msg.data

    def srdf(self) -> str:
        with self._state_lock:
            return self._srdf

    def table_pose(self) -> tuple[list[float], list[float]]:
        """``table`` in ``world`` from config/robot.yaml (where Gazebo models go)."""
        config_dir = self.get_parameter("config_dir").value or os.environ.get("AR3_CONFIG_DIR")
        robot = yaml.safe_load((Path(config_dir or "") / "robot.yaml").read_text())
        return robot["table"]["xyz"], robot["table"].get("rpy", [0.0, 0.0, 0.0])

    def _on_policy(self, msg: String) -> None:
        try:
            policy = json.loads(msg.data)
        except ValueError:
            return
        with self._state_lock:
            self._owner_policy = policy

    def _publish_policy(self) -> None:
        payload = {
            "host": self.host,
            "mode": self.mode,
            "remote_motion": self.remote_motion,
            "arm": self.arm,
        }
        self._policy_pub.publish(String(data=json.dumps(payload)))

    def _refresh(self) -> None:
        """Every second: controllers and the move_group action (graph queries are cheap)."""
        listed = None
        if self._list_controllers.service_is_ready():
            future = self._list_controllers.call_async(ListControllers.Request())
            if _wait(future, CALL_TIMEOUT_S) and future.result() is not None:
                listed = [
                    {"name": c.name, "state": c.state, "type": c.type}
                    for c in future.result().controller
                ]
        name = self.get_parameter("move_group").value
        present = name in self.get_node_names()
        actions = []
        if present:
            try:
                actions = get_action_server_names_and_types_by_node(self, name, "/")
            except Exception:  # the node left between the two calls
                actions = []
        with self._state_lock:
            self._controllers = listed
            self._graph = {
                "move_group": present,
                "move_action": any(a == "/move_action" for a, _t in actions),
            }
        self._refresh_dynamic()

    # --- read by the HTTP handlers ---------------------------------------------------------

    def health(self) -> dict:
        now = time.monotonic()
        with self._state_lock:
            clock_hz = self._clock_meter.rate(now)
            advancing = now - self._sim_time[1] < 1.0
            cams = {n: m.rate(now) for n, m in self._camera_rates.items()}
            listed = self._controllers
            graph = dict(self._graph)
        components = {
            "gazebo": health.gazebo(self.mode, clock_hz, advancing),
            "controllers": health.controllers(listed),
            "move_group": health.move_group(graph["move_group"], graph["move_action"]),
            "cameras": health.cameras(self.mode, cams, self._expected_cameras),
            "safety": health.safety(self.mode),
        }
        return {
            "ok": True,
            "mode": self.mode,
            "host": self.host,
            "uptime_s": round(now - self.started, 1),
            "rtf": self.rtf(),
            **health.summary(self.mode, components),
        }

    def motion(self) -> dict:
        with self._state_lock:
            owner = self._owner_policy
        return health.motion_policy(self.mode, owner, self.arm)

    def joints(self) -> dict | None:
        with self._state_lock:
            msg = self._joints
        if msg is None:
            return None
        return {"name": list(msg.name), "position": [float(p) for p in msg.position]}

    def controllers(self) -> list[dict] | None:
        with self._state_lock:
            return self._controllers

    def graph(self) -> dict:
        nodes = sorted(f"{ns.rstrip('/')}/{n}" for n, ns in self.get_node_names_and_namespaces())
        topics = [{"name": n, "types": t} for n, t in self.get_topic_names_and_types()]
        services = [{"name": n, "types": t} for n, t in self.get_service_names_and_types()]
        return {"nodes": nodes, "topics": topics, "services": services, "actions": self._actions()}

    def _actions(self) -> list[dict]:
        """Action servers from the graph: an action shows up as <name>/_action/feedback."""
        out = []
        for name, types in self.get_topic_names_and_types():
            if name.endswith("/_action/feedback"):
                action = name[: -len("/_action/feedback")]
                kinds = [t.replace("_FeedbackMessage", "") for t in types]
                out.append({"name": action, "types": kinds})
        return out

    # --- telemetry (§10.3, §12a) -----------------------------------------------------------

    def _setup_telemetry(self, sensors) -> None:
        self.telemetry_config = self._read_telemetry()
        cfg = self.telemetry_config
        self.rosout = telemetry.Ring()
        self.events = telemetry.Ring(keep=200)
        self.sampler = telemetry.Sampler(self._sample)
        self._sensors = sensors
        self._rate_meters: dict[str, health.RateMeter] = {}
        self._dynamic: dict[str, object] = {}  # topic -> subscription (resolved at runtime)
        self._thumbs: dict[str, dict] = {}  # camera -> {sub, msg, jpeg, encoded, wanted}
        self._param_clients: dict[str, tuple] = {}
        self.create_subscription(
            JointTrajectoryControllerState,
            cfg["controller_state"],
            self._on_controller,
            10,
            callback_group=sensors,
        )
        self.create_subscription(
            DisplayTrajectory, cfg["planned_path"], self._on_planned, 10, callback_group=sensors
        )
        # /rosout publishers are transient-local (depth 1000): with the same durability the
        # agent also gets what nodes logged before it started (move_group's startup lines).
        rosout_qos = QoSProfile(
            depth=1000,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(
            Log, "/rosout", self._on_rosout, rosout_qos, callback_group=sensors
        )

    def _read_telemetry(self) -> dict:
        config_dir = self.get_parameter("config_dir").value or os.environ.get("AR3_CONFIG_DIR")
        path = Path(config_dir or "") / "cockpit" / "telemetry.yaml"
        data = {}
        if config_dir and path.exists():
            data = yaml.safe_load(path.read_text()) or {}
        return {
            **TELEMETRY_DEFAULTS,
            **data,
            "thumbnails": {**TELEMETRY_DEFAULTS["thumbnails"], **data.get("thumbnails", {})},
        }

    def _on_controller(self, msg: JointTrajectoryControllerState) -> None:
        with self._state_lock:
            self._controller = (
                list(msg.joint_names),
                list(msg.reference.positions),
                list(msg.reference.velocities),
            )
            self._controller_at = time.monotonic()
            self._controller_history.add(self._controller_at, self._controller)

    def _on_planned(self, msg: DisplayTrajectory) -> None:
        self.events.push({"kind": "trajectory", "t": time.time(), **telemetry.trajectory(msg)})

    def _on_rosout(self, msg: Log) -> None:
        self.rosout.push(telemetry.rosout_line(msg))

    def _on_safety(self, msg) -> None:
        state = telemetry.safety(msg)
        with self._state_lock:
            changed = self._safety is None or self._safety["estop"] != state["estop"]
            self._safety, self._safety_at = state, time.monotonic()
        if changed:
            self.events.push({"kind": "estop", "t": time.time(), **state})

    def _on_objects(self, msg) -> None:
        self.events.push(
            {"kind": "detections", "t": time.time(), "objects": telemetry.objects(msg)}
        )

    def _sample(self, due: float) -> dict | None:
        """The sampler's snapshot for the tick due at ``due`` (monotonic): sample-and-hold of
        the joint and controller states that were the latest at that time."""
        now = time.monotonic()
        with self._state_lock:
            last = self._joint_history.latest()
            if last is None or now - last[0] > telemetry.FRESH_S:
                return None  # /joint_states stopped: a real gap
            # the state at the tick's time; when the callbacks ran late too, the oldest kept
            joints = self._joint_history.at(due) or self._joint_history.oldest()
            ctrl_last = self._controller_history.latest()
            ctrl_item = None
            if ctrl_last is not None and now - ctrl_last[0] < telemetry.FRESH_S:
                ctrl_item = self._controller_history.at(due) or self._controller_history.oldest()
            names = self._names
            safety = self._safety if now - self._safety_at < SAFETY_FRESH_S else None
        msg = joints[1]
        ctrl = ctrl_item[1] if ctrl_item else None
        sample = telemetry.pack(
            names, (list(msg.name), msg.position, msg.velocity, msg.effort), ctrl
        )
        if safety is not None:
            sample["s"] = safety
        return sample

    def joint_names(self) -> list[str]:
        with self._state_lock:
            return list(self._names)

    def sim_time_s(self) -> float | None:
        """The latest /clock in seconds; None without /clock (the real arm)."""
        with self._state_lock:
            return self._clock_window[-1][1] / 1e9 if self._clock_window else None

    def rtf(self) -> float | None:
        """Sim real-time factor over the last 2 s of /clock; None without /clock."""
        with self._state_lock:
            window = list(self._clock_window)
        if len(window) < 2 or window[-1][0] - window[0][0] < 0.5:
            return None
        (m0, s0), (m1, s1) = window[0], window[-1]
        return round((s1 - s0) / 1e9 / (m1 - m0), 3)

    def _refresh_dynamic(self) -> None:
        """Every second: subscribe to configured topics that appeared (rates, safety, objects)
        and drop camera subscriptions nobody looked at for a while."""
        cfg = self.telemetry_config
        types = dict(self.get_topic_names_and_types())
        wanted = {t: ("rate", None) for t in cfg["rate_topics"]}
        wanted[cfg["safety"]] = ("safety", self._on_safety)
        wanted[cfg["objects"]] = ("objects", self._on_objects)
        for topic, (kind, handler) in wanted.items():
            key = f"{kind}:{topic}"
            if key in self._dynamic or topic not in types:
                continue
            try:
                msg_type = get_message(types[topic][0])
            except (AttributeError, ModuleNotFoundError, ValueError):
                continue  # the message package is not installed in this image
            if kind == "rate":
                meter = self._rate_meters.setdefault(topic, health.RateMeter())
                sub = self.create_subscription(
                    msg_type,
                    topic,
                    lambda _m, mt=meter: self._tick(mt),
                    qos_profile_sensor_data,
                    callback_group=self._sensors,
                    raw=True,
                )
            else:
                sub = self.create_subscription(
                    msg_type, topic, handler, 10, callback_group=self._sensors
                )
            self._dynamic[key] = sub
        now = time.monotonic()
        idle = float(cfg["thumbnails"]["idle_s"])
        for thumb in list(self._thumbs.values()):
            if thumb["sub"] is not None and now - thumb["wanted"] > idle:
                self.destroy_subscription(thumb["sub"])
                with self._state_lock:
                    thumb["sub"], thumb["msg"] = None, None

    def _tick(self, meter: health.RateMeter) -> None:
        with self._state_lock:
            meter.tick(time.monotonic())

    def topic_rates(self) -> dict[str, dict]:
        """Configured topics: measured rate (2 s window) and age of the last message."""
        now = time.monotonic()
        out = {}
        with self._state_lock:
            for topic in self.telemetry_config["rate_topics"]:
                meter = self._rate_meters.get(topic)
                stamps = meter._stamps if meter else ()
                out[topic] = {
                    "hz": round(meter.rate(now), 2) if meter else 0.0,
                    "age_s": round(now - stamps[-1], 3) if stamps else None,
                }
        return out

    def measure_rate(self, topic: str, seconds: float = 2.0) -> dict:
        """`ros2 topic hz` for one topic, on demand (ROS Graph page)."""
        types = dict(self.get_topic_names_and_types())
        if topic not in types:
            raise KeyError(topic)
        msg_type = get_message(types[topic][0])
        meter = health.RateMeter(window_s=seconds + 1)
        sub = self.create_subscription(
            msg_type,
            topic,
            lambda _m: self._tick(meter),
            qos_profile_sensor_data,
            callback_group=self._sensors,
            raw=True,
        )
        try:
            time.sleep(seconds)
            with self._state_lock:
                hz = meter.rate(time.monotonic())
                count = len(meter._stamps)
        finally:
            self.destroy_subscription(sub)
        return {"topic": topic, "hz": round(hz, 2), "messages": count, "window_s": seconds}

    def graph_detail(self) -> dict:
        """The graph with publishers and subscribers per topic (ROS Graph page, §10.4)."""
        base = self.graph()
        for t in base["topics"]:
            t["publishers"] = sorted(
                {
                    _node_name(i.node_namespace, i.node_name)
                    for i in self.get_publishers_info_by_topic(t["name"])
                }
            )
            t["subscribers"] = sorted(
                {
                    _node_name(i.node_namespace, i.node_name)
                    for i in self.get_subscriptions_info_by_topic(t["name"])
                }
            )
        return base

    def node_params(self, node: str) -> list[dict]:
        """Parameters of a node, read-only (list_parameters + get_parameters)."""
        node = "/" + node.strip("/")
        if node not in self.graph()["nodes"]:
            raise KeyError(node)
        with self._state_lock:
            clients = self._param_clients.get(node)
            if clients is None:
                clients = (
                    self.create_client(
                        ListParameters, f"{node}/list_parameters", callback_group=self._calls
                    ),
                    self.create_client(
                        GetParameters, f"{node}/get_parameters", callback_group=self._calls
                    ),
                )
                self._param_clients[node] = clients
        lister, getter = clients
        if not lister.wait_for_service(timeout_sec=1.0):
            # e.g. tf2's transform_listener_impl_* nodes: they switch the services off
            raise TimeoutError(f"{node} has no parameters to show (its parameter services are off)")
        future = lister.call_async(ListParameters.Request())
        if not _wait(future, CALL_TIMEOUT_S) or future.result() is None:
            raise TimeoutError(f"{node}: list_parameters did not answer")
        names = sorted(future.result().result.names)

        def get(batch: list[str]) -> list | None:
            fut = getter.call_async(GetParameters.Request(names=batch))
            if not _wait(fut, CALL_TIMEOUT_S) or fut.result() is None:
                raise TimeoutError(f"{node}: get_parameters did not answer")
            values = fut.result().values
            return values if len(values) == len(batch) else None

        out = []
        for i in range(0, len(names), PARAM_BATCH):
            batch = names[i : i + PARAM_BATCH]
            values = get(batch)
            # rclcpp answers nothing for the whole request when one name cannot be read
            # (move_group lists some it does not serve); then ask one at a time.
            pairs = (
                zip(batch, values, strict=True)
                if values is not None
                else ((n, (get([n]) or [None])[0]) for n in batch)
            )
            for name, value in pairs:
                if value is None:
                    out.append({"name": name, "value": None, "unavailable": True})
                    continue
                v = parameter_value_to_python(value)
                v = [_finite(x) for x in v] if isinstance(v, tuple | list) else _finite(v)
                out.append({"name": name, "value": v})
        return out

    def thumbnail(self, camera: str) -> bytes | None:
        """The latest image of ``camera`` as a small JPEG, at most max_fps new ones a second.
        The image topic is subscribed on the first request and dropped when nobody asks."""
        cfg = self.telemetry_config["thumbnails"]
        if camera not in self._expected_cameras:
            raise KeyError(camera)
        now = time.monotonic()
        with self._state_lock:
            thumb = self._thumbs.setdefault(
                camera, {"sub": None, "msg": None, "jpeg": None, "encoded": 0.0, "wanted": now}
            )
            thumb["wanted"] = now
            need_sub = thumb["sub"] is None
        if need_sub:
            sub = self.create_subscription(
                Image,
                f"/{camera}/image_raw",
                lambda m, c=camera: self._on_image(c, m),
                qos_profile_sensor_data,
                callback_group=self._sensors,
            )
            with self._state_lock:
                thumb["sub"] = sub
        with self._state_lock:
            msg, cached, encoded = thumb["msg"], thumb["jpeg"], thumb["encoded"]
        if msg is not None and now - encoded >= 1.0 / float(cfg["max_fps"]):
            cached = telemetry.jpeg(msg, int(cfg["width"]))
            with self._state_lock:
                thumb["jpeg"], thumb["encoded"], thumb["msg"] = cached, now, None
        return cached

    def _on_image(self, camera: str, msg: Image) -> None:
        with self._state_lock:
            thumb = self._thumbs.get(camera)
            if thumb is not None:
                thumb["msg"] = msg

    def cameras(self) -> list[str]:
        return sorted(self._expected_cameras)

    # --- software stop (§7.2, principle 4) --------------------------------------------------

    def stop(self) -> dict:
        """Cancel every goal of the motion actions, then hold the trajectory controllers.

        Not an e-stop: it asks the controllers to stop; the hardware e-stop cuts power.
        """
        cancelled, errors = [], []
        actions = sorted(
            (a for a in self._actions() if any(t in STOP_ACTION_TYPES for t in a["types"])),
            key=lambda a: min(
                STOP_ACTION_TYPES.index(t) for t in a["types"] if t in STOP_ACTION_TYPES
            ),
        )
        for action in actions:
            client = self._cancel_client(action["name"])
            if not client.wait_for_service(timeout_sec=0.5):
                errors.append(f"{action['name']}: cancel service not available")
                continue
            # A zero goal id and zero stamp cancel all goals of the server.
            future = client.call_async(CancelGoal.Request())
            if not _wait(future, CALL_TIMEOUT_S) or future.result() is None:
                errors.append(f"{action['name']}: no answer")
                continue
            goals = len(future.result().goals_canceling)
            cancelled.append({"action": action["name"], "goals": goals})
        held = []
        for topic, types in self.get_topic_names_and_types():
            if topic.endswith("/joint_trajectory") and HOLD_TOPIC_TYPE in types:
                names = self._controller_joints(topic)
                if not names:
                    # An empty message without joint names is rejected by the controller.
                    errors.append(f"{topic}: controller joints unknown, hold not sent")
                    continue
                self._hold_pub(topic).publish(JointTrajectory(joint_names=names))
                held.append(topic)
        self.get_logger().warning(
            f"software stop: cancelled {sum(c['goals'] for c in cancelled)} goal(s), "
            f"held {held}"
        )
        return {"cancelled": cancelled, "held": held, "errors": errors}

    def _cancel_client(self, action: str):
        with self._state_lock:
            client = self._cancel_clients.get(action)
            if client is None:
                client = self.create_client(
                    CancelGoal, f"{action}/_action/cancel_goal", callback_group=self._calls
                )
                self._cancel_clients[action] = client
        return client

    def _controller_joints(self, topic: str) -> list[str]:
        """Joint names of the controller that owns ``topic`` (from its controller_state)."""
        owner = topic.rsplit("/", 1)[0]
        with self._state_lock:
            ctrl = self._controller
        if ctrl is None or self.telemetry_config["controller_state"].rsplit("/", 1)[0] != owner:
            return []
        return list(ctrl[0])

    def _hold_pub(self, topic: str):
        with self._state_lock:
            pub = self._hold_pubs.get(topic)
            if pub is None:
                pub = self.create_publisher(JointTrajectory, topic, 10)
                self._hold_pubs[topic] = pub
        return pub


def _finite(v):
    """NaN / inf (e.g. MoveIt's unset joint limits) as text: JSON has no such numbers."""
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return str(v)
    return v


def _node_name(namespace: str, name: str) -> str:
    return f"{namespace.rstrip('/')}/{name}"


def _wait(future, timeout: float) -> bool:
    """Wait for a future that the executor thread completes."""
    done = threading.Event()
    future.add_done_callback(lambda _f: done.set())
    return done.wait(timeout)
