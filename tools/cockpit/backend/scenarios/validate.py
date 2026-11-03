"""Scenario validation before save and before run (docs/specs/spec_cockpit.md §12.3).

``validate(text)`` returns problems ``{severity, path, message, line}`` (line 1-based, from the
YAML node marks, so the editor can underline them):

- YAML syntax and the JSON Schema (scenario.schema.json);
- object and obstacle ids unique;
- objects on the table, not overlapping each other or a bin (footprints, with yaw);
- named poses and gripper states exist in the SRDF; joints within config/robot.yaml limits;
- referenced files exist (bins, task params, meshes), bins exist in config/bins.yaml;
- the Gazebo world exists; the modes are supported on this host; REAL only with
  COCKPIT_REAL_SCENARIOS=1 and without randomize;
- the task type and perception are available (sequence now; pick_place / sort after SPEC
  Phase 3, perception camera after Phase 2).

Errors block saving and running; warnings do not.
"""

from __future__ import annotations

import json
import math
import os
from functools import cache
from pathlib import Path

import yaml

from . import context

SCHEMA_FILE = Path(__file__).with_name("scenario.schema.json")
PHASE3 = "needs RunTask (SPEC Phase 3); only `sequence` runs now"


def _meshes() -> Path:
    from . import store

    return store.directory() / "meshes"


@cache
def schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text())


def _problem(severity: str, path: list, message: str, lines: dict | None = None) -> dict:
    key = tuple(path)
    line = None
    if lines is not None:
        while key and key not in lines:
            key = key[:-1]
        line = lines.get(key)
    return {"severity": severity, "path": "/".join(str(p) for p in path), "message": message,
            "line": line}  # fmt: skip


def _lines(node, path=(), out=None) -> dict[tuple, int]:
    """JSON path -> 1-based line of the YAML node."""
    out = {} if out is None else out
    out[path] = node.start_mark.line + 1
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            _lines(v, (*path, k.value), out)
            out.setdefault((*path, k.value), k.start_mark.line + 1)
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            _lines(v, (*path, i), out)
    return out


def parse(text: str) -> tuple[dict | None, dict, list[dict]]:
    """(data, lines, problems) of a YAML text."""
    try:
        node = yaml.compose(text)
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        msg = str(getattr(exc, "problem", None) or exc)
        return None, {}, [{"severity": "error", "path": "", "message": f"YAML: {msg}",
                           "line": mark.line + 1 if mark else None}]  # fmt: skip
    if not isinstance(data, dict):
        return None, {}, [_problem("error", [], "a scenario is a YAML mapping")]
    return data, _lines(node) if node is not None else {}, []


def _footprint(obj: dict) -> list[tuple[float, float]]:
    """Corners of an object's footprint on the table (x, y in metres)."""
    shape, size = obj.get("shape"), obj.get("size") or [0.0]
    if shape == "box":
        hx, hy = size[0] / 2, (size[1] if len(size) > 1 else size[0]) / 2
    else:  # cylinder, sphere, mesh: a square around the diameter (conservative)
        hx = hy = size[0] / 2
    yaw = math.radians(float(obj["pose"].get("yaw_deg", 0.0)))
    cx, cy = float(obj["pose"]["x"]), float(obj["pose"]["y"])
    c, s = math.cos(yaw), math.sin(yaw)
    return [(cx + c * x - s * y, cy + s * x + c * y) for x, y in ((hx, hy), (-hx, hy), (-hx, -hy),
                                                                  (hx, -hy))]  # fmt: skip


def _bin_footprint(b: dict) -> list[tuple[float, float]]:
    x, y = b["xyz"][0], b["xyz"][1]
    hx, hy = b["size"][0] / 2, b["size"][1] / 2
    yaw = float(b.get("yaw", 0.0))
    c, s = math.cos(yaw), math.sin(yaw)
    return [(x + c * u - s * v, y + s * u + c * v) for u, v in ((hx, hy), (-hx, hy), (-hx, -hy),
                                                                (hx, -hy))]  # fmt: skip


def overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    """Separating-axis test of two convex polygons."""
    for poly in (a, b):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            ax, ay = y1 - y2, x2 - x1
            pa = [ax * x + ay * y for x, y in a]
            pb = [ax * x + ay * y for x, y in b]
            if max(pa) <= min(pb) or max(pb) <= min(pa):
                return False
    return True


def resolved_bins(data: dict, site: dict) -> tuple[dict[str, dict], list[dict]]:
    """name -> bin of the scenario (config refs and inline bins); problems."""
    out, problems = {}, []
    config_bins = {b["name"]: b for b in site["bins"]}
    for i, b in enumerate((data.get("scene") or {}).get("bins") or []):
        name = b.get("name", "")
        if "ref" in b:
            if not (context.repo() / b["ref"]).exists():
                problems.append(_problem("error", ["scene", "bins", i, "ref"],
                                         f"{b['ref']} does not exist"))  # fmt: skip
            elif b["ref"] == "config/bins.yaml" and name not in config_bins:
                problems.append(_problem("error", ["scene", "bins", i, "name"],
                                         f"no bin {name[4:]!r} in config/bins.yaml "
                                         f"({', '.join(n[4:] for n in config_bins)})"))  # fmt: skip
            else:
                out[name] = config_bins.get(name, {**b})
        else:
            out[name] = {**b, "yaw": b.get("yaw", 0.0)}
    return out, problems


def validate(text: str, host_modes: list[str] | None = None, capabilities: dict | None = None
             ) -> dict:  # fmt: skip
    """{"ok", "errors", "warnings", "problems", "data"}. ``host_modes``: the robot kinds this host
    can start (mock, gazebo, real); ``capabilities``: the agent's, when one is running."""
    import jsonschema

    data, lines, problems = parse(text)
    if data is None:
        return _result(None, problems)
    validator = jsonschema.Draft202012Validator(schema())
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = list(err.absolute_path)
        problems.append(_problem("error", path, err.message, lines))
    if any(p["severity"] == "error" for p in problems):
        return _result(data, problems)  # the semantic checks need a valid structure

    site = context.site()
    scene = data.get("scene") or {}
    objects = [("objects", i, o) for i, o in enumerate(scene.get("objects") or [])]
    objects += [("obstacles", i, o) for i, o in enumerate(scene.get("obstacles") or [])]

    seen: dict[str, list] = {}
    for kind, i, o in objects:
        if o["id"] in seen:
            problems.append(_problem("error", ["scene", kind, i, "id"],
                                     f"object id {o['id']!r} is used twice", lines))  # fmt: skip
        seen[o["id"]] = ["scene", kind, i]

    bins, bin_problems = resolved_bins(data, site)
    for p in bin_problems:
        p["line"] = _problem("error", p["path"].split("/"), "", lines)["line"]
    problems += bin_problems
    tx, ty = site["table"]["size"][0] / 2, site["table"]["size"][1] / 2
    feet = []
    for kind, i, o in objects:
        if o["shape"] == "box" and len(o["size"]) != 3:
            problems.append(_problem("error", ["scene", kind, i, "size"], "a box needs [x, y, z]",
                                     lines))  # fmt: skip
            continue
        if o["shape"] == "cylinder" and len(o["size"]) != 2:
            problems.append(_problem("error", ["scene", kind, i, "size"],
                                     "a cylinder needs [diameter, height]", lines))  # fmt: skip
            continue
        if o["shape"] == "mesh":
            mesh = o.get("mesh")
            if not mesh or not (_meshes() / mesh).exists():
                problems.append(_problem("error", ["scene", kind, i, "mesh"],
                                         f"mesh file {mesh} is not in the scenarios' meshes/",
                                         lines))  # fmt: skip
            elif o.get("spawn"):
                problems.append(_problem("warning", ["scene", kind, i, "spawn"],
                                         "meshes are not spawned in Gazebo (planning scene only)",
                                         lines))  # fmt: skip
        fp = _footprint(o)
        if any(abs(x) > tx or abs(y) > ty for x, y in fp):
            problems.append(_problem("error", ["scene", kind, i, "pose"],
                                     f"{o['id']} is not on the table ({2 * tx:g} × {2 * ty:g} m "
                                     "around the table frame)", lines))  # fmt: skip
        for other_id, other_fp in feet:
            if overlap(fp, other_fp):
                problems.append(_problem("error", ["scene", kind, i, "pose"],
                                         f"{o['id']} overlaps {other_id}", lines))  # fmt: skip
        for name, b in {**{b["name"]: b for b in site["bins"]}, **bins}.items():
            if "xyz" in b and overlap(fp, _bin_footprint(b)):
                problems.append(_problem("error", ["scene", kind, i, "pose"],
                                         f"{o['id']} overlaps {name}", lines))  # fmt: skip
        feet.append((o["id"], fp))

    named = site["named_poses"]
    limits = site["joint_limits_deg"]
    lo_g, hi_g = site["gripper_range_m"]

    def joints_ok(path, values):
        for k, v in enumerate(values):
            name = f"joint_{k + 1}"
            if name in limits:
                lo, hi = limits[name]
                if not lo <= math.degrees(v) <= hi:
                    problems.append(
                        _problem(
                            "error",
                            [*path, k],
                            f"{name} {math.degrees(v):.1f}° is outside "
                            f"[{lo:g}, {hi:g}]° (config/robot.yaml)",
                            lines,
                        )
                    )

    start = data.get("start") or {}
    if "named_pose" in start and start["named_pose"] not in named.get("arm", {}):
        poses = ", ".join(named.get("arm", {}))
        problems.append(_problem("error", ["start", "named_pose"],
                                 f"no named pose {start['named_pose']!r} ({poses})",
                                 lines))  # fmt: skip
    if "joints" in start:
        joints_ok(["start", "joints"], start["joints"])
    if "gripper" in start and not lo_g <= start["gripper"] <= hi_g:
        problems.append(_problem("error", ["start", "gripper"],
                                 f"gripper {start['gripper']} m is outside "
                                 f"[{lo_g}, {hi_g}] m (URDF)",
                                 lines))  # fmt: skip

    task = data["task"]
    caps_types = (capabilities or {}).get("task_types")
    if task["type"] != "sequence":
        problems.append(_problem("error", ["task", "type"], f"{task['type']} {PHASE3}", lines))
    elif caps_types is not None and "sequence" not in caps_types:
        problems.append(_problem("error", ["task", "type"], "the agent cannot run sequences",
                                 lines))  # fmt: skip
    rules = (task.get("params") or {}).get("rules")
    if isinstance(rules, str) and not (context.repo() / rules).exists():
        problems.append(_problem("error", ["task", "params", "rules"], f"{rules} does not exist",
                                 lines))  # fmt: skip
    for i, step in enumerate(task.get("steps") or []):
        ((kind, value),) = step.items()
        path = ["task", "steps", i, kind]
        if kind == "move_named" and value not in named.get("arm", {}):
            problems.append(_problem("error", path, f"no named pose {value!r} "
                                     f"({', '.join(named.get('arm', {}))})", lines))  # fmt: skip
        elif kind == "gripper" and isinstance(value, str) and value not in named.get("gripper", {}):
            problems.append(_problem("error", path, f"no gripper state {value!r}", lines))
        elif kind == "gripper" and not isinstance(value, str) and not lo_g <= value <= hi_g:
            problems.append(_problem("error", path, f"gripper {value} m is outside [{lo_g}, "
                                     f"{hi_g}] m (URDF)", lines))  # fmt: skip
        elif kind == "move_joints":
            joints_ok(path, value)
        elif kind == "move_pose" and value.get("frame", "table") == "table":
            r = math.hypot(
                value["x"] - site["mount"]["xyz"][0], value["y"] - site["mount"]["xyz"][1]
            )
            if r > site["reach"]["radius_m"]:
                problems.append(_problem("warning", path,
                                         f"{r:.2f} m from the base, beyond the approximate reach "
                                         f"of {site['reach']['radius_m']} m: check reachability",
                                         lines))  # fmt: skip

    if data.get("perception") == "camera":
        problems.append(_problem("error", ["perception"], "perception: camera needs SPEC Phase 2",
                                 lines))  # fmt: skip
    elif data.get("perception") == "ground_truth":
        problems.append(
            _problem(
                "warning",
                ["perception"],
                "ground_truth detections are not " "published yet (ar3_interfaces, SPEC Phase 2)",
                lines,
            )
        )

    world = data.get("world", "workcell")
    if world not in site["worlds"]:
        problems.append(_problem("error", ["world"], f"no Gazebo world {world!r} "
                                 f"({', '.join(site['worlds'])})", lines))  # fmt: skip

    for name, criterion_bin in ((data.get("success") or {}).get("objects_in_bins") or {}).items():
        if name not in seen:
            problems.append(_problem("error", ["success", "objects_in_bins", name],
                                     f"no object {name!r} in the scene", lines))  # fmt: skip
        if criterion_bin not in {**{b["name"]: b for b in site["bins"]}, **bins}:
            problems.append(_problem("error", ["success", "objects_in_bins", name],
                                     f"no bin {criterion_bin!r}", lines))  # fmt: skip

    kinds = {"sim_mock": "mock", "sim_gazebo": "gazebo", "real": "real"}
    for i, mode in enumerate(data["modes"]):
        if mode == "real":
            if os.environ.get("COCKPIT_REAL_SCENARIOS") != "1":
                problems.append(
                    _problem(
                        "error",
                        ["modes", i],
                        "real needs " "COCKPIT_REAL_SCENARIOS=1 in .env (§12.5)",
                        lines,
                    )
                )
            if scene.get("randomize"):
                problems.append(
                    _problem(
                        "error",
                        ["scene", "randomize"],
                        "randomize is not allowed on the real arm",
                        lines,
                    )
                )
        if host_modes is not None and kinds[mode] not in host_modes:
            problems.append(_problem("warning", ["modes", i], f"{mode} cannot be started on this "
                                     "host", lines))  # fmt: skip
    return _result(data, problems)


def _result(data, problems) -> dict:
    errors = [p for p in problems if p["severity"] == "error"]
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": [p for p in problems if p["severity"] != "error"],
        "data": data,
    }
