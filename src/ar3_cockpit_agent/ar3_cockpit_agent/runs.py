"""Scenario runs in the ROS graph (docs/specs/spec_cockpit.md §12.4): the agent's side of a run.

A run gets the resolved scenario from the cockpit (randomized already) and, in its own thread:

1. checks the motion policy and the mode;
2. resets: removes every planning-scene object and Gazebo model named ``scn_*``, moves to the
   start pose;
3. applies the scene: collision objects through ``/apply_planning_scene`` (frame ``table``) and,
   in sim with Gazebo, models through gz-transport ``/world/<world>/create`` with generated SDF;
4. runs the ``sequence`` steps: each move is planned first (MoveGroup, ``plan_only``), then
   executed (ExecuteTrajectory) with a timeout of 3 × the planned duration (§12.4); ``wait``
   sleeps; Pause holds before the next step; Abort cancels the goal and holds (software stop);
5. reads the final Gazebo poses of the spawned objects (for ``objects_in_bins``, evaluated by
   the cockpit) and cleans up unless ``keep_scene``.

Every change is an event in the run's ring (``/ws/run/<id>``). The cockpit evaluates the
success criteria and stores the run. ``perception: ground_truth`` (DetectedObjectArray) and
the ``pick_place`` / ``sort`` task types need ar3_interfaces and RunTask (SPEC Phases 2–3);
the agent does not advertise them in /capabilities.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from geometry_msgs.msg import Pose, PoseStamped
from moveit_msgs.action import ExecuteTrajectory, MoveGroup
from moveit_msgs.msg import (
    CollisionObject,
    Constraints,
    JointConstraint,
    MoveItErrorCodes,
    OrientationConstraint,
    PlanningScene,
    PlanningSceneComponents,
    PositionConstraint,
)
from moveit_msgs.srv import ApplyPlanningScene, GetPlanningScene, GetPositionIK
from rclpy.action import ActionClient
from sensor_msgs.msg import JointState
from shape_msgs.msg import SolidPrimitive

from . import scenario, telemetry

TCP_LINK = "gripper_tcp"
ARM, GRIPPER = "arm", "gripper"
POSITION_TOLERANCE_M = 0.001
ORIENTATION_TOLERANCE_RAD = 0.01
CALL_TIMEOUT_S = 5.0
SPAWN_TIMEOUT_MS = 5000
CLI_TIMEOUT_S = 10.0
ERRORS = {v: k for k, v in vars(MoveItErrorCodes).items() if k.isupper() and isinstance(v, int)}
PRIMITIVE = {"box": SolidPrimitive.BOX, "cylinder": SolidPrimitive.CYLINDER,
             "sphere": SolidPrimitive.SPHERE}  # fmt: skip
TASK_TYPES = scenario.TASK_TYPES
Aborted, StepFailed = scenario.Aborted, scenario.StepFailed


def _wait(future, timeout: float) -> bool:
    done = threading.Event()
    future.add_done_callback(lambda _f: done.set())
    return done.wait(timeout)


def error_name(code: int) -> str:
    return ERRORS.get(code, str(code))


class Gazebo:
    """Spawn and remove models over gz-transport, read models and poses with the ``gz`` CLI
    (both work headless on both arches).

    No Python subscription: the Python binding keeps the GIL while ``request()`` waits, so a
    Python callback on gz-transport's thread (e.g. /pose/info) would block the reply until the
    timeout. ``gz model`` asks the world's state service in a process of its own instead.
    """

    def __init__(self, world: str):
        from gz.transport13 import Node

        self.world = world
        self.node = Node()

    def _cli(self, *args: str) -> str:
        # `gz model` has no world option: it asks the one running world (ours)
        res = subprocess.run(  # noqa: S603 (fixed argv)
            ["gz", "model", *args],
            capture_output=True, text=True, timeout=CLI_TIMEOUT_S,
        )  # fmt: skip
        return res.stdout

    def models(self) -> list[str]:
        return scenario.parse_model_list(self._cli("--list"))

    def pose(self, name: str):
        """((x, y, z), (qx, qy, qz, qw)) in the world frame, or None."""
        return scenario.parse_model_pose(self._cli("-m", name, "-p"))

    def spawn(self, name: str, sdf: str, xyz, quat) -> None:
        from gz.msgs10.boolean_pb2 import Boolean
        from gz.msgs10.entity_factory_pb2 import EntityFactory

        req = EntityFactory(sdf=sdf, name=name, allow_renaming=False)
        req.pose.position.x, req.pose.position.y, req.pose.position.z = xyz
        o = req.pose.orientation
        o.x, o.y, o.z, o.w = quat
        for attempt in (1, 2):
            ok, rep = self.node.request(
                f"/world/{self.world}/create", req, EntityFactory, Boolean, SPAWN_TIMEOUT_MS
            )
            if ok and rep.data:
                return
            if attempt == 1:  # most likely the name exists (a run that crashed): replace it
                self.remove(name)
                time.sleep(0.2)
        raise StepFailed(f"Gazebo did not spawn {name}")

    def remove(self, name: str) -> None:
        from gz.msgs10.boolean_pb2 import Boolean
        from gz.msgs10.entity_pb2 import Entity

        req = Entity(name=name, type=Entity.MODEL)
        self.node.request(f"/world/{self.world}/remove", req, Entity, Boolean, SPAWN_TIMEOUT_MS)


class Runs:
    """The node's run machinery: clients, the current run, IK checks, scene apply / clear."""

    def __init__(self, node):
        self.node = node
        cb = node._calls
        self.move = ActionClient(node, MoveGroup, "/move_action", callback_group=cb)
        self.execute = ActionClient(
            node, ExecuteTrajectory, "/execute_trajectory", callback_group=cb
        )
        self.apply = node.create_client(
            ApplyPlanningScene, "/apply_planning_scene", callback_group=cb
        )
        self.get_scene = node.create_client(
            GetPlanningScene, "/get_planning_scene", callback_group=cb
        )
        self.ik = node.create_client(GetPositionIK, "/compute_ik", callback_group=cb)
        self.current: Run | None = None
        self.runs: dict[str, Run] = {}
        self._gazebo: Gazebo | None = None
        self._lock = threading.Lock()

    # --- shared helpers -----------------------------------------------------------------------

    def gazebo(self) -> Gazebo | None:
        if self.node.mode != "sim":
            return None
        if self._gazebo is None:
            self._gazebo = Gazebo(self.node.gz_world)
        return self._gazebo

    def named(self) -> dict[str, dict[str, dict[str, float]]]:
        srdf = self.node.srdf()
        if not srdf:
            raise StepFailed("no /robot_description_semantic from move_group yet")
        return scenario.parse_group_states(srdf)

    def scene_objects(self) -> list[str]:
        if not self.get_scene.wait_for_service(timeout_sec=2.0):
            raise StepFailed("/get_planning_scene not available")
        req = GetPlanningScene.Request()
        req.components.components = PlanningSceneComponents.WORLD_OBJECT_NAMES
        fut = self.get_scene.call_async(req)
        if not _wait(fut, CALL_TIMEOUT_S) or fut.result() is None:
            raise StepFailed("/get_planning_scene did not answer")
        return [o.id for o in fut.result().scene.world.collision_objects]

    def apply_objects(self, add: list[CollisionObject], remove: list[str]) -> None:
        scene = PlanningScene(is_diff=True)
        for oid in remove:
            scene.world.collision_objects.append(
                CollisionObject(id=oid, operation=CollisionObject.REMOVE)
            )
        scene.world.collision_objects.extend(add)
        if not self.apply.wait_for_service(timeout_sec=2.0):
            raise StepFailed("/apply_planning_scene not available")
        fut = self.apply.call_async(ApplyPlanningScene.Request(scene=scene))
        if not _wait(fut, CALL_TIMEOUT_S) or fut.result() is None or not fut.result().success:
            raise StepFailed("/apply_planning_scene refused the scene")

    def collision_object(self, obj: dict) -> CollisionObject:
        shape, dims = scenario.geometry(obj)
        xyz, quat = scenario.object_pose(obj)
        co = CollisionObject(id=scenario.model_name(obj["id"]), operation=CollisionObject.ADD)
        co.header.frame_id = "table"
        co.primitives.append(SolidPrimitive(type=PRIMITIVE[shape], dimensions=dims))
        pose = Pose()
        pose.position.x, pose.position.y, pose.position.z = xyz
        pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w = quat
        co.primitive_poses.append(pose)
        return co

    def clear_scene(self) -> dict:
        """Remove every scn_* planning-scene object and Gazebo model."""
        objects = [o for o in self.scene_objects() if o.startswith(scenario.PREFIX)]
        if objects:
            self.apply_objects([], objects)
        models = []
        gz = self.gazebo()
        if gz is not None:
            models = sorted({m for m in gz.models() if m.startswith(scenario.PREFIX)})
            for m in models:
                gz.remove(m)
        return {"planning_scene": objects, "gazebo": models}

    def apply_scene(self, sc: dict, spawn: bool = True) -> dict:
        objects = [
            *sc.get("scene", {}).get("objects", []),
            *sc.get("scene", {}).get("obstacles", []),
        ]
        self.apply_objects([self.collision_object(o) for o in objects], [])
        spawned = []
        gz = self.gazebo()
        if gz is not None and spawn:
            table_xyz, table_rpy = self.node.table_pose()
            for o in objects:
                if not o.get("spawn", False):
                    continue
                xyz, quat = scenario.object_pose(o)
                gz.spawn(scenario.model_name(o["id"]), scenario.sdf(o), scenario.table_to_world(
                    xyz, table_xyz, table_rpy), quat)  # fmt: skip
                spawned.append(o["id"])
        return {"collision_objects": [o["id"] for o in objects], "spawned": spawned}

    def check_ik(self, targets: list[dict]) -> list[dict]:
        """IK (collision-aware) for every pose target; §12.3 "Check reachability"."""
        if not self.ik.wait_for_service(timeout_sec=2.0):
            raise StepFailed("/compute_ik not available (is move_group running?)")
        out = []
        state = self.node.joints()
        for t in targets:
            xyz, quat = scenario.target_pose(t)
            req = GetPositionIK.Request()
            req.ik_request.group_name = ARM
            req.ik_request.ik_link_name = TCP_LINK
            req.ik_request.avoid_collisions = True
            req.ik_request.timeout.sec, req.ik_request.timeout.nanosec = 0, 200_000_000
            ps = PoseStamped()
            ps.header.frame_id = t.get("frame", "table")
            ps.pose.position.x, ps.pose.position.y, ps.pose.position.z = xyz
            o = ps.pose.orientation
            o.x, o.y, o.z, o.w = quat
            req.ik_request.pose_stamped = ps
            if state:  # start from the current state; MoveIt logs an error for an empty one
                req.ik_request.robot_state.joint_state = JointState(
                    name=state["name"], position=state["position"]
                )
            fut = self.ik.call_async(req)
            if not _wait(fut, CALL_TIMEOUT_S) or fut.result() is None:
                out.append({**t, "reachable": False, "error": "no answer from /compute_ik"})
                continue
            code = fut.result().error_code.val
            res = {**t, "reachable": code == MoveItErrorCodes.SUCCESS, "error": "" if code ==
                   MoveItErrorCodes.SUCCESS else error_name(code)}  # fmt: skip
            if code == MoveItErrorCodes.SUCCESS:
                js = fut.result().solution.joint_state
                res["joints"] = {n: round(p, 4) for n, p in zip(js.name, js.position, strict=False)
                                 if n.startswith("joint_")}  # fmt: skip
            out.append(res)
        return out

    # --- runs ---------------------------------------------------------------------------------

    def start(self, run_id: str, sc: dict, options: dict) -> dict:
        with self._lock:
            if self.current is not None and self.current.active:
                raise RuntimeError(f"run {self.current.id} is still running")
            run = Run(self, run_id, sc, options)
            self.current = run
            self.runs[run_id] = run
            for old in list(self.runs)[:-20]:  # keep the last 20
                self.runs.pop(old, None)
        run.thread.start()
        return run.state()


class Run:
    def __init__(self, runs: Runs, run_id: str, sc: dict, options: dict):
        self.runs, self.node = runs, runs.node
        self.id, self.sc, self.options = run_id, sc, options
        self.events = telemetry.Ring(keep=2000)
        self.status = "starting"
        self.step_index = -1
        self.steps: list[dict] = []
        self.result: dict = {}
        self.started = time.time()
        self.paused = threading.Event()
        self.resume = threading.Event()
        self.aborted = threading.Event()
        self._goal = None
        self.bag: subprocess.Popen | None = None
        self.bag_path = ""
        self.thread = threading.Thread(target=self._run, name=f"run-{run_id[:8]}", daemon=True)

    @property
    def active(self) -> bool:
        return self.status in ("starting", "running", "paused")

    def emit(self, kind: str, **data) -> None:
        self.events.push({"kind": kind, "t": time.time(), **data})

    def log(self, text: str, level: str = "INFO") -> None:
        self.emit("log", level=level, text=text)
        # rclpy refuses one call site logging at two severities: one call per level
        if level == "INFO":
            self.node.get_logger().info(f"run {self.id[:8]}: {text}")
        else:
            self.node.get_logger().warning(f"run {self.id[:8]}: {text}")

    def state(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "step": self.step_index,
            "steps": self.steps,
            "result": self.result,
            "started": self.started,
            "paused": self.paused.is_set(),
            "bag": self.bag_path,
        }

    # --- control from the HTTP thread ------------------------------------------------------

    def abort(self) -> None:
        self.aborted.set()
        self.resume.set()
        goal = self._goal
        if goal is not None:
            goal.cancel_goal_async()
        self.node.stop()  # cancel everything and hold (software stop)

    def pause(self, on: bool) -> None:
        if on:
            self.resume.clear()
            self.paused.set()
            self.emit("paused")
        else:
            self.paused.clear()
            self.resume.set()
            self.emit("resumed")

    # --- the run ------------------------------------------------------------------------------

    def _check(self) -> None:
        if self.aborted.is_set():
            raise Aborted()

    def _run(self) -> None:
        sc = self.sc
        planning = sc.get("planning", {})
        self.steps = [{"index": i, "label": scenario.describe(s), "status": "pending"}
                      for i, s in enumerate(sc.get("task", {}).get("steps", []))]  # fmt: skip
        self.status = "running"
        self.emit("status", status="running", steps=self.steps)
        result = "succeeded"
        failed_step = None
        error = ""
        try:
            motion = self.node.motion()
            if not motion["allowed"]:
                raise StepFailed(f"motion not allowed: {motion['reason']}")
            if sc.get("task", {}).get("type", "sequence") not in TASK_TYPES:
                raise StepFailed(f"task type {sc['task']['type']} is not available yet")
            if self.options.get("record_bag"):
                self._start_bag()
            cleared = self.runs.clear_scene()
            self.log(f"reset: removed {len(cleared['planning_scene'])} scene object(s), "
                     f"{len(cleared['gazebo'])} Gazebo model(s)")  # fmt: skip
            self._check()
            start = sc.get("start", {"named_pose": "home"})
            self.log(f"start pose: {start}")
            self._move_start(start, planning)
            applied = self.runs.apply_scene(sc, spawn=self.node.mode == "sim")
            self.log(f"scene: {len(applied['collision_objects'])} collision object(s), "
                     f"{len(applied['spawned'])} spawned in Gazebo")  # fmt: skip
            if self.runs.gazebo() is not None and applied["spawned"]:
                time.sleep(1.0)  # let the spawned objects settle on the table
            for i, step in enumerate(sc["task"].get("steps", [])):
                self._check()
                if self.paused.is_set():
                    self.status = "paused"
                    self.log(f"paused before step {i + 1}")
                    self.resume.wait()
                    self._check()
                    self.status = "running"
                self.step_index = i
                self._step(i, step, planning)
        except Aborted:
            result, error = "aborted", "aborted by the user"
            failed_step = self.step_index if self.step_index >= 0 else None
        except StepFailed as exc:
            result, error = "failed", str(exc)
            failed_step = self.step_index if self.step_index >= 0 else None
        except Exception as exc:  # a bug must still end the run
            import traceback

            self.node.get_logger().error(f"run {self.id[:8]} crashed:\n{traceback.format_exc()}")
            result, error = "failed", f"agent error: {exc}"
            failed_step = self.step_index if self.step_index >= 0 else None
        if failed_step is not None and failed_step < len(self.steps):
            self.steps[failed_step]["status"] = result if result == "aborted" else "failed"
            self.steps[failed_step]["error"] = error
        final = self._final_poses()
        if not self.options.get("keep_scene"):
            try:
                self.runs.clear_scene()
            except StepFailed as exc:
                self.log(f"cleanup: {exc}", "WARN")
        self._stop_bag()
        self.result = {
            "result": result,
            "error": error,
            "failed_step": failed_step,
            "duration_s": round(time.time() - self.started, 3),
            "object_poses": final,
            "steps": self.steps,
            "bag": self.bag_path,
        }
        try:
            self.log(f"{result}{': ' + error if error else ''}",
                     "INFO" if result == "succeeded" else "WARN")  # fmt: skip
        finally:
            self.emit("finished", **self.result)
            self.status = result

    # --- steps ----------------------------------------------------------------------------

    def _step(self, i: int, step: dict, planning: dict) -> None:
        kind = scenario.step_kind(step)
        value = step[kind]
        st = self.steps[i]
        st["status"] = "running"
        st["started"] = time.time()
        self.emit("step", index=i, status="running", label=st["label"])
        metrics: dict = {}
        if kind == "wait":
            end = time.time() + float(value)
            while time.time() < end:
                self._check()
                time.sleep(0.05)
        elif kind == "move_named":
            joints = self.runs.named().get(ARM, {}).get(value)
            if joints is None:
                raise StepFailed(f"no named pose {value!r} in the SRDF")
            metrics = self._move(ARM, _joint_goal(joints), planning)
        elif kind == "move_joints":
            names = [f"joint_{k}" for k in range(1, len(value) + 1)]
            metrics = self._move(ARM, _joint_goal(dict(zip(names, map(float, value), strict=True))),
                                 planning)  # fmt: skip
        elif kind == "gripper":
            if isinstance(value, int | float):
                goal = {"gripper_jaw1_joint": float(value)}
            else:
                goal = self.runs.named().get(GRIPPER, {}).get(value)
                if goal is None:
                    raise StepFailed(f"no gripper state {value!r} in the SRDF")
            metrics = self._move(GRIPPER, _joint_goal(goal), planning)
        else:
            metrics = self._move(ARM, _pose_goal(value), planning, planner=value.get("planner"))
        st.update(status="succeeded", ended=time.time(), **metrics)
        st["duration_s"] = round(st["ended"] - st["started"], 3)
        self.emit("step", index=i, status="succeeded", label=st["label"], **metrics,
                  duration_s=st["duration_s"])  # fmt: skip

    def _move_start(self, start: dict, planning: dict) -> None:
        if "joints" in start:
            names = [f"joint_{k}" for k in range(1, len(start["joints"]) + 1)]
            self._move(ARM, _joint_goal(dict(zip(names, map(float, start["joints"]), strict=True))),
                       planning)  # fmt: skip
        else:
            name = start.get("named_pose", "home")
            joints = self.runs.named().get(ARM, {}).get(name)
            if joints is None:
                raise StepFailed(f"no named pose {name!r} in the SRDF")
            self._move(ARM, _joint_goal(joints), planning)
        if "gripper" in start:
            self._move(GRIPPER, _joint_goal({"gripper_jaw1_joint": float(start["gripper"])}),
                       planning)  # fmt: skip

    def _move(
        self, group: str, goal: Constraints, planning: dict, planner: str | None = None
    ) -> dict:
        """Plan (plan_only), then execute with a timeout of 3 × the planned duration."""
        if not self.runs.move.wait_for_server(timeout_sec=5.0):
            raise StepFailed("/move_action not available")
        g = MoveGroup.Goal()
        r = g.request
        r.group_name = group
        r.goal_constraints = [goal]
        pipeline = planning.get("pipeline", "ompl")
        r.pipeline_id = "pilz_industrial_motion_planner" if pipeline == "pilz" else "ompl"
        r.planner_id = planning.get("planner_id", "") if pipeline != "pilz" else "PTP"
        if planner in ("pilz_lin", "pilz_ptp"):
            r.pipeline_id = "pilz_industrial_motion_planner"
            r.planner_id = "LIN" if planner == "pilz_lin" else "PTP"
        elif planner == "ompl":
            r.pipeline_id, r.planner_id = "ompl", planning.get("planner_id", "")
        r.allowed_planning_time = float(planning.get("planning_time_s", 5.0))
        r.num_planning_attempts = 3
        r.max_velocity_scaling_factor = float(planning.get("velocity_scaling", 0.2))
        r.max_acceleration_scaling_factor = float(planning.get("acceleration_scaling", 0.2))
        r.start_state.is_diff = True
        g.planning_options.plan_only = True
        t0 = time.time()
        send = self.runs.move.send_goal_async(g)
        if not _wait(send, CALL_TIMEOUT_S) or not send.result().accepted:
            raise StepFailed("move_group did not accept the planning request")
        res = send.result().get_result_async()
        if not _wait(res, r.allowed_planning_time + CALL_TIMEOUT_S) or res.result() is None:
            raise StepFailed(f"no plan within {r.allowed_planning_time:g} s")
        self._check()
        code = res.result().result.error_code.val
        if code != MoveItErrorCodes.SUCCESS:
            raise StepFailed(f"planning failed: {error_name(code)}")
        traj = res.result().result.planned_trajectory
        planning_s = time.time() - t0
        pts = traj.joint_trajectory.points
        duration = (
            (pts[-1].time_from_start.sec + pts[-1].time_from_start.nanosec / 1e9) if pts else 0.0
        )
        timeout = scenario.execution_timeout(duration)
        eg = ExecuteTrajectory.Goal(trajectory=traj)
        if not self.runs.execute.wait_for_server(timeout_sec=5.0):
            raise StepFailed("/execute_trajectory not available")
        t1 = time.time()
        send = self.runs.execute.send_goal_async(eg)
        if not _wait(send, CALL_TIMEOUT_S) or not send.result().accepted:
            raise StepFailed("move_group did not accept the trajectory")
        self._goal = send.result()
        try:
            res = self._goal.get_result_async()
            deadline = time.time() + timeout
            while not res.done():
                if self.aborted.is_set():
                    raise Aborted()
                if time.time() > deadline:
                    self._goal.cancel_goal_async()
                    self.node.stop()
                    raise StepFailed(f"execution took longer than {timeout:.0f} s (3 × the plan)")
                time.sleep(0.02)
        finally:
            self._goal = None
        code = res.result().result.error_code.val
        if code != MoveItErrorCodes.SUCCESS:
            raise StepFailed(f"execution failed: {error_name(code)}")
        return {"planning_s": round(planning_s, 3), "execution_s": round(time.time() - t1, 3),
                "trajectory_s": round(duration, 3), "points": len(pts)}  # fmt: skip

    # --- evaluation data, rosbag ----------------------------------------------------------

    def _final_poses(self) -> dict:
        gz = self.runs.gazebo()
        if gz is None:
            return {}
        table_xyz, table_rpy = self.node.table_pose()
        out = {}
        for o in [*self.sc.get("scene", {}).get("objects", [])]:
            if not o.get("spawn"):
                continue
            p = gz.pose(scenario.model_name(o["id"]))
            if p is None:
                continue
            xyz = scenario.world_to_table(p[0], table_xyz, table_rpy)
            out[o["id"]] = {"xyz": [round(v, 4) for v in xyz], "quat": [round(v, 4) for v in p[1]]}
        return out

    def _start_bag(self) -> None:
        topics = self.node.telemetry_config.get("bag_presets", {}).get("default", [])
        out = Path(os.environ.get("AR3_WS", "/ar3_ws")) / "data" / "bags" / f"run_{self.id}"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.bag = subprocess.Popen(  # noqa: S603 (fixed argv, topics from the site config)
            ["ros2", "bag", "record", "-s", "mcap", "-o", str(out), *topics],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.bag_path = f"data/bags/run_{self.id}"
        self.log(f"recording a rosbag to {self.bag_path}")

    def _stop_bag(self) -> None:
        if self.bag is None:
            return
        try:
            os.killpg(self.bag.pid, signal.SIGINT)  # ros2 bag closes the file on SIGINT
            self.bag.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            self.bag.kill()
        self.bag = None


def _joint_goal(joints: dict[str, float], tolerance: float = 1e-3) -> Constraints:
    c = Constraints()
    for name, value in joints.items():
        c.joint_constraints.append(
            JointConstraint(
                joint_name=name,
                position=float(value),
                tolerance_above=tolerance,
                tolerance_below=tolerance,
                weight=1.0,
            )  # fmt: skip
        )
    return c


def _pose_goal(target: dict) -> Constraints:
    xyz, quat = scenario.target_pose(target)
    frame = target.get("frame", "table")
    c = Constraints()
    pc = PositionConstraint()
    pc.header.frame_id = frame
    pc.link_name = TCP_LINK
    region = SolidPrimitive(type=SolidPrimitive.SPHERE, dimensions=[POSITION_TOLERANCE_M])
    pc.constraint_region.primitives.append(region)
    pose = Pose()
    pose.position.x, pose.position.y, pose.position.z = xyz
    pose.orientation.w = 1.0
    pc.constraint_region.primitive_poses.append(pose)
    pc.weight = 1.0
    oc = OrientationConstraint()
    oc.header.frame_id = frame
    oc.link_name = TCP_LINK
    oc.orientation.x, oc.orientation.y, oc.orientation.z, oc.orientation.w = quat
    oc.absolute_x_axis_tolerance = oc.absolute_y_axis_tolerance = ORIENTATION_TOLERANCE_RAD
    oc.absolute_z_axis_tolerance = ORIENTATION_TOLERANCE_RAD
    oc.weight = 1.0
    c.position_constraints.append(pc)
    c.orientation_constraints.append(oc)
    return c
