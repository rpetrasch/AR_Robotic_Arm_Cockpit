"""Checks after the pre-Docker ones (docs/specs/spec_cockpit.md §8.1): GPU, repo config, images,
workspace, tests, simulation smoke, viewers, network, hardware, AI.

Hardware facts that are not confirmed yet (device IDs, udev rules) are not checked: those
items report ``skip`` until Phase 1 delivers them (CLAUDE.md: do not invent hardware facts).
"""

from __future__ import annotations

import json
import re
import shlex
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import yaml

from core.repo import dockerfile_hash

from .base import Check, CheckContext, Outcome

TEST_PACKAGES = ("ar3_description", "ar3_moveit_config", "ar3_bringup")
# Changes to these need `colcon build`; Python, launch and config files are symlinked.
BUILD_INPUTS = re.compile(
    r"(CMakeLists\.txt|package\.xml|setup\.(py|cfg)|\.(cpp|hpp|h|msg|srv|action))$"
)
VIEWER_PORTS = {8765: "foxglove_bridge", 6080: "noVNC", 8080: "Lichtblick web"}
HOST_NETWORK_SERVICES = ("linux", "linux-gpu", "dev", "dev-gpu", "zenohd")


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


async def _head(ctx: CheckContext) -> tuple[str, float] | None:
    res = await ctx.run(["git", "-C", str(ctx.repo), "log", "-1", "--format=%H %ct"])
    if not res.ok or not res.stdout.strip():
        return None
    sha, ts = res.stdout.split()
    return sha, float(ts)


def _dotenv(path: Path) -> dict[str, str]:
    from config.envfiles import parse

    return parse(path)


# --- GPU (Linux) ----------------------------------------------------------------------------


async def gpu_driver(ctx: CheckContext) -> Outcome:
    res = await ctx.run(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]
    )
    if not res.ok:
        return Outcome(
            "warn",
            "no NVIDIA driver (nvidia-smi failed); GPU rendering (GPU=1) and Phase 4 need it",
            "Install the driver (needs sudo), then reboot.",
            ["sudo ubuntu-drivers install", "sudo reboot"],
            fix_step="nvidia.driver",
        )
    name, driver, mem = (x.strip() for x in res.stdout.splitlines()[0].split(","))
    return Outcome("pass", f"{name}, driver {driver}, {mem}")


async def gpu_toolkit(ctx: CheckContext) -> Outcome:
    info = await ctx.run(["docker", "info", "--format", "{{json .}}"])
    if not info.ok:
        return Outcome("unknown", "Docker not reachable")
    runtimes = json.loads(info.stdout).get("Runtimes", {}) or {}
    if "nvidia" in runtimes:
        return Outcome("pass", "nvidia runtime registered in Docker")
    return Outcome(
        "warn",
        "NVIDIA Container Toolkit not configured (no nvidia runtime in docker info)",
        "Install the toolkit and register it with Docker (needs sudo); see docs/manual/install.md.",
        [
            "sudo nvidia-ctk runtime configure --runtime=docker",
            "sudo systemctl restart docker",
        ],
        fix_step="nvidia.toolkit",
    )


# --- Repo config ----------------------------------------------------------------------------

ROS_RMWS = ("rmw_fastrtps_cpp", "rmw_cyclonedds_cpp", "rmw_zenoh_cpp")


async def docker_env(ctx: CheckContext) -> Outcome:
    path = ctx.repo / "docker/.env"
    if not path.exists():
        return Outcome(
            "warn",
            "docker/.env missing (defaults apply: Fast DDS, single host)",
            "Create it from docker/.env.example.",
            ["cp docker/.env.example docker/.env"],
            fix_step="env.create",
        )
    values = _dotenv(path)
    rmw = values.get("ROS_RMW", "rmw_fastrtps_cpp")
    if rmw not in ROS_RMWS:
        return Outcome("fail", f"ROS_RMW={rmw}: use one of {', '.join(ROS_RMWS)}")
    if rmw == "rmw_zenoh_cpp" and not values.get("ZENOH_ROUTER"):
        return Outcome("fail", "ROS_RMW=rmw_zenoh_cpp needs ZENOH_ROUTER (Workstation A address)")
    return Outcome("pass", f"ROS_RMW={rmw}")


def _config_problems(config: Path) -> list[str]:
    problems: list[str] = []

    def load(name: str) -> dict:
        try:
            data = yaml.safe_load((config / name).read_text())
        except FileNotFoundError:
            problems.append(f"{name} missing")
            return {}
        except yaml.YAMLError as exc:
            problems.append(f"{name}: invalid YAML ({exc.__class__.__name__})")
            return {}
        if not isinstance(data, dict):
            problems.append(f"{name}: not a mapping")
            return {}
        return data

    robot = load("robot.yaml")
    for key in ("table", "mount", "joint_limits", "motion"):
        if robot and key not in robot:
            problems.append(f"robot.yaml: `{key}` missing")
    for joint, lim in (robot.get("joint_limits") or {}).items():
        if not (isinstance(lim, list) and len(lim) == 2 and lim[0] < lim[1]):
            problems.append(f"robot.yaml: joint_limits.{joint} must be [lower, upper]")
    for key in ("velocity_scaling", "acceleration_scaling"):
        value = (robot.get("motion") or {}).get(key)
        if robot.get("motion") is not None and not (
            isinstance(value, int | float) and 0 < value <= 1
        ):
            problems.append(f"robot.yaml: motion.{key} must be in (0, 1]")
    bins = load("bins.yaml")
    if bins and not bins.get("bins"):
        problems.append("bins.yaml: `bins` missing or empty")
    cameras = load("cameras.yaml")
    for cam in ("cam_overhead", "cam_wrist"):
        if cameras and cam not in cameras:
            problems.append(f"cameras.yaml: `{cam}` missing")
    return problems


async def site_config(ctx: CheckContext) -> Outcome:
    problems = _config_problems(ctx.repo / "config")
    if problems:
        return Outcome("fail", "; ".join(problems), "Fix the files in config/ (SPEC §8).")
    return Outcome("pass", "robot.yaml, bins.yaml, cameras.yaml valid")


# --- Images ---------------------------------------------------------------------------------


def _image_check(image: str, step: str, required: bool):
    async def probe(ctx: CheckContext) -> Outcome:
        res = await ctx.run(["docker", "image", "inspect", image, "--format", "{{json .}}"])
        missing = "fail" if required else "warn"
        if not res.ok:
            if "Cannot connect" in res.stderr:
                return Outcome("unknown", "Docker not reachable")
            return Outcome(missing, f"{image} not built", fix_step=step)
        info = json.loads(res.stdout)
        arch = {"aarch64": "arm64", "amd64": "x86_64"}.get(info.get("Architecture"), "")
        if arch and arch != ctx.host.arch:
            return Outcome(
                missing, f"{image} is {arch}, this host is {ctx.host.arch}", fix_step=step
            )
        label = ((info.get("Config") or {}).get("Labels") or {}).get("ar3.dockerfile_hash", "")
        want = dockerfile_hash(ctx.repo)
        if label != want:
            why = "built before the hash label existed" if not label else "the Dockerfile changed"
            return Outcome("warn", f"{image}: rebuild needed ({why})", fix_step=step)
        return Outcome("pass", f"{image}, {ctx.host.arch}, up to date ({want[:8]})")

    return probe


# --- Workspace ------------------------------------------------------------------------------


async def workspace(ctx: CheckContext) -> Outcome:
    setup = ctx.repo / "install/setup.bash"
    if not setup.exists():
        return Outcome("fail", "not built (install/setup.bash missing)", fix_step="ws.build")
    built = setup.stat().st_mtime
    newer = [
        p.relative_to(ctx.repo).as_posix()
        for p in (ctx.repo / "src").rglob("*")
        if p.is_file() and BUILD_INPUTS.search(p.name) and p.stat().st_mtime > built
    ]
    if newer:
        more = f" and {len(newer) - 1} more" if len(newer) > 1 else ""
        return Outcome("warn", f"rebuild needed: {newer[0]}{more} changed", fix_step="ws.build")
    return Outcome("pass", f"built {_when(built)}")


# --- Tests / simulation smoke ---------------------------------------------------------------


def _test_results(repo: Path) -> tuple[int, int, float] | None:
    files = [
        f for pkg in TEST_PACKAGES for f in (repo / "build" / pkg / "test_results").rglob("*.xml")
    ]
    if not files:
        return None
    tests = failures = 0
    for f in files:
        root = ET.parse(f).getroot()
        suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
        for s in suites:
            tests += int(s.get("tests", 0))
            failures += int(s.get("failures", 0)) + int(s.get("errors", 0))
    return tests, failures, max(f.stat().st_mtime for f in files)


async def last_tests(ctx: CheckContext) -> Outcome:
    results = _test_results(ctx.repo)
    if results is None:
        return Outcome("warn", "make test has not run in this checkout", fix_step="tests.run")
    tests, failures, when = results
    # Test cases from the xunit files; colcon test-result also counts one ctest entry per file.
    detail = f"{tests} test cases, {failures} failed, {_when(when)}"
    if failures:
        return Outcome("fail", detail, fix_step="tests.run")
    head = await _head(ctx)
    if head and head[1] > when:
        return Outcome("warn", f"{detail}; older than the last commit", fix_step="tests.run")
    return Outcome("pass", detail)


SMOKE_FILE = ".cockpit/results/smoke-sim.json"


async def sim_smoke(ctx: CheckContext) -> Outcome:
    path = ctx.repo / SMOKE_FILE
    if not path.exists():
        return Outcome("warn", "make smoke-sim has not run in this checkout", fix_step="sim.smoke")
    data = json.loads(path.read_text())
    detail = f"{data.get('finished', '?')}, commit {data.get('commit', '?')[:8]}"
    if data.get("rc") != 0:
        return Outcome("fail", f"failed (exit {data.get('rc')}), {detail}", fix_step="sim.smoke")
    head = await _head(ctx)
    if head and data.get("commit") and head[0] != data["commit"]:
        return Outcome("warn", f"passed for an older commit ({detail})", fix_step="sim.smoke")
    return Outcome("pass", f"passed, {detail}")


# --- Viewers --------------------------------------------------------------------------------


async def viewer_ports(ctx: CheckContext) -> Outcome:
    ps = await ctx.run(["docker", "ps", "--format", "{{json .}}"])
    ours, host_net = {}, []
    if ps.ok:
        for line in ps.stdout.splitlines():
            row = json.loads(line)
            labels = row.get("Labels", "")
            if "com.docker.compose.project=ar3" not in labels:
                continue
            for port in map(int, re.findall(r":(\d+)->", row.get("Ports", ""))):
                ours[port] = row["Names"]
            service = re.search(r"com\.docker\.compose\.service=([\w-]+)", labels)
            if service and service.group(1) in HOST_NETWORK_SERVICES:
                host_net.append(row["Names"])
    busy, used = [], []
    for port, what in VIEWER_PORTS.items():
        if not await ctx.net.tcp_open("127.0.0.1", port):
            continue
        owner = ours.get(port) or (host_net[0] if host_net else None)
        (used if owner else busy).append((port, what, owner))
    if busy:
        names = ", ".join(f"{p} ({w})" for p, w, _ in busy)
        return Outcome(
            "fail",
            f"port {names} in use by another program",
            "Stop the program that holds the port; this shows which one:",
            [f"lsof -nP -iTCP:{p} -sTCP:LISTEN" for p, _w, _o in busy],
        )
    detail = "8765, 6080, 8080 free"
    if used:
        detail = "; ".join(f"{p} used by {o}" for p, _w, o in used) + "; the rest free"
    return Outcome("pass", detail)


def x11_allowed(xhost_output: str) -> bool:
    """Does the X server let a Docker container (root, over the unix socket) connect?

    ``xhost`` lists "LOCAL:" after ``xhost +local:docker``, "access control disabled" after
    ``xhost +``; with only per-user entries (SI:localuser:…) the container is refused.
    """
    return "access control disabled" in xhost_output or any(
        line.strip().startswith(("LOCAL:", "SI:localuser:root"))
        for line in xhost_output.splitlines()
    )


async def x11_access(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["xhost"])
    if res.returncode == -1:
        return Outcome("skip", "xhost not installed (needed only for RViz / Gazebo windows)")
    if not res.ok:
        return Outcome(
            "skip",
            "no desktop session to open windows on (RViz in the browser works without)",
        )
    if x11_allowed(res.stdout):
        return Outcome("pass", "Docker may open windows on this desktop")
    return Outcome(
        "warn",
        "Docker may not open windows on this desktop: RViz and Gazebo windows would fail "
        "(qt.qpa.xcb: could not connect to display)",
        "Allow local Docker clients once per login (it resets when you log out).",
        ["xhost +local:docker"],
        fix_step="x11.allow",
    )


LICHTBLICK_IMAGE = "ghcr.io/lichtblick-suite/lichtblick:1.30.0"  # = docker/compose.yaml


async def lichtblick_web(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["docker", "image", "inspect", LICHTBLICK_IMAGE, "--format", "{{.Id}}"])
    if res.ok:
        return Outcome("pass", f"{LICHTBLICK_IMAGE} present")
    if "Cannot connect" in res.stderr:
        return Outcome("unknown", "Docker not reachable")
    return Outcome(
        "warn",
        f"{LICHTBLICK_IMAGE} not pulled (Lichtblick in the browser, :8080)",
        fix_step="images.pull_viewers",
    )


# --- Network --------------------------------------------------------------------------------


async def zenohd(ctx: CheckContext) -> Outcome:
    values = _dotenv(ctx.repo / "docker/.env")
    host, port = values.get("ZENOH_ROUTER", ""), int(values.get("ZENOH_PORT", "7447"))
    # The command with the configured address, so that it can be pasted as it is.
    cli = [f"nc -vz {shlex.quote(host) if host else '<ZENOH_ROUTER>'} {port}"]
    if values.get("ROS_RMW") != "rmw_zenoh_cpp":
        return Outcome(
            "skip",
            "not needed: ROS_RMW is not rmw_zenoh_cpp in docker/.env "
            "(only for a Mac that connects to Workstation A)",
            cli=cli,
        )
    if not host:
        return Outcome("fail", "ZENOH_ROUTER is not set in docker/.env", cli=cli)
    if await ctx.net.tcp_open(host, port, timeout=2):
        return Outcome("pass", f"tcp/{host}:{port} reachable", cli=cli)
    return Outcome(
        "fail",
        f"tcp/{host}:{port} not reachable",
        "Start `make zenohd` on Workstation A and open TCP port 7447 there. "
        "Check ZENOH_ROUTER in docker/.env: it is Workstation A's address.",
        cli=cli,
    )


# --- Hardware (Linux; REAL only) ------------------------------------------------------------


async def dialout(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["id", "-nG"])
    if not res.ok:
        return Outcome("unknown", "could not read the group list")
    if "dialout" in res.stdout.split():
        return Outcome("pass", "member of dialout")
    return Outcome(
        "warn",
        "not in the dialout group (needed for the Teensy and Nano serial ports, Phase 1)",
        "Add yourself (needs sudo), then log out and back in.",
        ["sudo usermod -aG dialout,video $USER"],
        fix_step="groups.add",
    )


async def hw_pending(ctx: CheckContext) -> Outcome:
    return Outcome("skip", "REAL only: device IDs and udev rules arrive with Phase 1 (SPEC §12)")


# --- AI -------------------------------------------------------------------------------------


async def ollama(ctx: CheckContext) -> Outcome:
    base = ctx.env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    status, _ = await ctx.net.http_json(f"{base}/api/tags")
    if status == 200:
        return Outcome("pass", f"reachable at {base}")
    install = (
        ["brew install ollama", "ollama serve   # or start the Ollama app"]
        if ctx.host.os == "macos"
        else ["curl -fsSL https://ollama.com/install.sh | sh"]
    )
    return Outcome(
        "warn",
        f"not reachable at {base} (the assistant needs it)",
        "Install and start Ollama (native, outside Docker).",
        install,
    )


async def ollama_models(ctx: CheckContext) -> Outcome:
    base = ctx.env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    status, body = await ctx.net.http_json(f"{base}/api/tags")
    if status != 200:
        return Outcome("unknown", "Ollama not reachable")
    have = {m["name"] for m in (body or {}).get("models", [])}
    have |= {n.removesuffix(":latest") for n in have}
    wanted = [
        ctx.env.get("ASSISTANT_CHAT_MODEL", "llama3.1:8b"),
        ctx.env.get("ASSISTANT_EMBED_MODEL", "bge-m3"),
    ]
    missing = [m for m in wanted if m not in have]
    if missing:
        return Outcome(
            "warn",
            f"not pulled: {', '.join(missing)}",
            fix_step="ollama.pull",
            fix_params={"model": missing[0]},
        )
    return Outcome("pass", ", ".join(wanted))


async def anthropic_key(ctx: CheckContext) -> Outcome:
    if ctx.env.get("ANTHROPIC_API_KEY"):
        return Outcome("pass", "set (used only with ASSISTANT_ALLOW_EXTERNAL=1)")
    return Outcome("skip", "not set (only needed for ASSISTANT_PROVIDER=anthropic)")


L = ("linux",)
STACK_CHECKS = (
    Check("gpu.driver", "NVIDIA driver", "GPU", gpu_driver, ("nvidia-smi",),
          "docs/manual/install.md", L),
    Check("gpu.toolkit", "NVIDIA Container Toolkit", "GPU", gpu_toolkit,
          ("docker info --format '{{json .Runtimes}}'",), "docs/manual/install.md", L),
    Check("repo.env", "docker/.env", "Repo config", docker_env, ("cat docker/.env",),
          "docs/manual/usage-mac.md"),
    Check("repo.config", "Site config (config/*.yaml)", "Repo config", site_config,
          ("ls config/",), "docs/manual/usage-sim.md"),
    Check("images.dev", "Dev image (ar3-dev)", "Images",
          _image_check("ar3-dev", "images.build", True),
          ("docker image inspect ar3-dev",), "docs/manual/install.md"),
    Check("images.vnc", "noVNC image (ar3-dev-vnc)", "Images",
          _image_check("ar3-dev-vnc", "images.build_vnc", False),
          ("docker image inspect ar3-dev-vnc",), "docs/manual/usage-mac.md"),
    Check("ws.built", "Workspace built", "Workspace", workspace, ("ls install/setup.bash",),
          "docs/manual/install.md"),
    Check("tests.last", "Last make test", "Tests", last_tests, ("make test",),
          "docs/dev/testing.md"),
    Check("sim.smoke", "Simulation smoke (make smoke-sim)", "Simulation smoke", sim_smoke,
          ("make smoke-sim",), "docs/dev/testing.md"),
    Check("viewers.ports", "Ports 8765 / 6080 / 8080", "Viewers", viewer_ports,
          ("lsof -nP -iTCP:8765 -sTCP:LISTEN",), "docs/manual/troubleshooting.md"),
    Check("viewers.x11", "Desktop windows for Docker (xhost)", "Viewers", x11_access,
          ("xhost",), "docs/manual/usage-sim.md", L),
    Check("viewers.lichtblick", "Lichtblick web image", "Viewers", lichtblick_web,
          ("make pull-viewers",), "docs/manual/cockpit.md"),
    Check("net.zenohd", "Zenoh router (REMOTE)", "Network", zenohd, ("nc -vz <ZENOH_ROUTER> 7447",),
          "docs/manual/usage-mac.md"),
    Check("hw.dialout", "Serial access (dialout group)", "Hardware", dialout, ("id -nG",),
          "docs/manual/install.md", L),
    Check("hw.devices", "Teensy, Nano, cameras, udev rules", "Hardware", hw_pending, (),
          "docs/specs/spec_v0.4.md", L),
    Check("ai.ollama", "Ollama", "AI", ollama, ("curl -s http://127.0.0.1:11434/api/tags",),
          "docs/specs/spec_cockpit.md"),
    Check("ai.models", "Chat and embedding models", "AI", ollama_models, ("ollama list",),
          "docs/specs/spec_cockpit.md"),
    Check("ai.anthropic", "Anthropic API key", "AI", anthropic_key, (),
          "docs/specs/spec_cockpit.md"),
)  # fmt: skip
