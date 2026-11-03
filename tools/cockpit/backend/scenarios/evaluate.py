"""Success criteria of a run (docs/specs/spec_cockpit.md §12.1 ``success``, §12.4 step 6).

- ``all_steps_succeed`` (default true): the agent finished every step;
- ``objects_in_bins`` {object: bin}: the object's final Gazebo position (agent, table frame)
  inside the bin's inner volume (footprint minus the walls, 0 … height); sim_gazebo only,
  "skipped" without Gazebo poses (sim_mock: there is no physics to move anything);
- ``max_duration_s``: the run's duration.

The run succeeds when the agent succeeded and no criterion failed (skipped ones do not count).
"""

from __future__ import annotations

import math

from . import context, validate


def in_bin(xyz, b: dict) -> bool:
    x, y, z = xyz
    bx, by, bz = b["xyz"]
    yaw = float(b.get("yaw", 0.0))
    c, s = math.cos(-yaw), math.sin(-yaw)
    u, v = c * (x - bx) - s * (y - by), s * (x - bx) + c * (y - by)
    wall = float(b.get("wall", 0.0))
    hx, hy = b["size"][0] / 2 - wall, b["size"][1] / 2 - wall
    return abs(u) <= hx and abs(v) <= hy and bz - 0.005 <= z <= bz + b["size"][2]


def evaluate(sc: dict, agent_result: dict, mode: str) -> tuple[str, list[dict]]:
    """(result, criteria): result succeeded | failed | aborted."""
    success = sc.get("success") or {}
    agent = agent_result.get("result", "failed")
    criteria = []
    if success.get("all_steps_succeed", True):
        ok = agent == "succeeded"
        fs, err = agent_result.get("failed_step"), agent_result.get("error") or agent
        detail = (
            "every step succeeded"
            if ok
            else (f"step {fs + 1}: {err}" if isinstance(fs, int) else err)
        )
        criteria.append({"criterion": "all_steps_succeed", "status": "pass" if ok else "fail",
                         "detail": detail})  # fmt: skip
    wanted = success.get("objects_in_bins") or {}
    if wanted:
        site = context.site()
        bins, _ = validate.resolved_bins(sc, site)
        bins = {**{b["name"]: b for b in site["bins"]}, **bins}
        poses = agent_result.get("object_poses") or {}
        for obj, bin_name in wanted.items():
            name = f"objects_in_bins.{obj}"
            if mode != "sim_gazebo" or obj not in poses:
                why = "no Gazebo in this mode" if mode != "sim_gazebo" else "no final pose"
                criteria.append({"criterion": name, "status": "skipped", "detail": why})
                continue
            xyz = poses[obj]["xyz"]
            ok = bin_name in bins and in_bin(xyz, bins[bin_name])
            criteria.append({"criterion": name, "status": "pass" if ok else "fail",
                             "detail": f"{obj} at ({xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f}) "
                             f"{'in' if ok else 'not in'} {bin_name}"})  # fmt: skip
    if "max_duration_s" in success:
        d = float(agent_result.get("duration_s") or 0)
        ok = d <= float(success["max_duration_s"])
        criteria.append(
            {
                "criterion": "max_duration_s",
                "status": "pass" if ok else "fail",
                "detail": f"{d:.1f} s of at most {success['max_duration_s']} s",
            }
        )
    if agent == "aborted":
        return "aborted", criteria
    failed = any(c["status"] == "fail" for c in criteria)
    return ("failed" if failed or agent != "succeeded" else "succeeded"), criteria
