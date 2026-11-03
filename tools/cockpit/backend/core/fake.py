"""COCKPIT_FAKE mode (docs/specs/spec_cockpit.md principle 6): a simulated Docker, ROS, host and
repo.

``FakeRunner`` answers the same argv lists as the real tools. Its world lives in
``<COCKPIT_DATA_DIR>/fake/state.json`` (shared by the web process, the workers and the e2e
tests) plus a simulated checkout in ``<COCKPIT_DATA_DIR>/fake/repo``:

    {"docker": true, "stack": {"service": "vnc", "ar3_mode": "sim"}, "arm": "NOT_HOMED",
     "images": {"ar3-dev": true}, "ollama": {"up": true, "models": []},
     "ports_busy": [], "gpu": true, "fail": {"make smoke-sim": "…error line…"},
     "lichtblick": false, "estop": false, "remote_motion": false}

``platform`` (macos | ubuntu | ubuntu-real) overrides COCKPIT_FAKE_PLATFORM, so one e2e
server can show both hosts. ``stack`` may also carry ``started`` (epoch s; the agent is
ready 3 s later), ``headless``, ``state`` ("running", "exited") and ``agent: false`` (the
agent does not answer).

By default it is a fresh clone on a healthy host: Docker works, no images, nothing built,
Ollama running without models. The scripted steps (``make image``, ``make ws-build``, …)
print a few lines and change the world as the real ones would, so the checklist can be
followed to all-green. ``fail`` makes the command with that argv print the line and exit 1.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .repo import IMAGE_INPUTS, dockerfile_hash
from .runner import CANCELLED, NOT_FOUND, CommandRunner, LineHandler, Result

DEFAULT_STATE = {
    "docker": True,
    "stack": None,
    "arm": "NOT_HOMED",
    "images": {},
    "ollama": {"up": True, "models": []},
    "ports_busy": [],
    "gpu": True,
    "fail": {},
    "lichtblick": False,
    "x11": True,  # xhost lets Docker open windows (False: until x11.allow runs)
    "estop": False,
    "remote_motion": False,
}
LABELS = "com.docker.compose.project=ar3,com.docker.compose.service={}"
HEAD = "f00dfeed" * 5  # the simulated checkout's commit
HEAD_TS = 1_790_000_000  # 2026-09-21, before any simulated result
REPO_FILES = (
    *IMAGE_INPUTS,
    "docker/.env.example",
    "config/robot.yaml",
    "config/bins.yaml",
    "config/cameras.yaml",
    "src/ar3_bringup/package.xml",
)
LINE_DELAY_S = 0.12

Handler = Callable[[tuple[str, ...]], Result]


def state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "fake" / "state.json"


def load_state(data_dir: Path) -> dict:
    path = state_path(data_dir)
    base = json.loads(json.dumps(DEFAULT_STATE))  # deep copy
    if path.exists():
        base.update(json.loads(path.read_text()))
    return base


def platform(data_dir: Path, default: str) -> str:
    """The fake host: the state file's ``platform``, else COCKPIT_FAKE_PLATFORM."""
    return load_state(data_dir).get("platform") or default


def save_state(data_dir: Path, state: dict) -> None:
    path = state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**DEFAULT_STATE, **state}, indent=2))


def ensure_repo(data_dir: Path, real_repo: Path) -> Path:
    """The simulated checkout: a fresh clone's files, nothing generated."""
    repo = Path(data_dir) / "fake" / "repo"
    for rel in REPO_FILES:
        target = repo / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(real_repo / rel, target)
    return repo


def _ok(out: str = "") -> Result:
    return Result(("fake",), 0, out)


def _fail(err: str) -> Result:
    return Result(("fake",), 1, "", err)


@dataclass
class FakeRunner(CommandRunner):
    """Scripted runner: the first handler whose argv prefix matches answers the call."""

    handlers: list[tuple[tuple[str, ...], Handler]] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    data_dir: Path | None = None
    repo: Path | None = None
    arch: str = "arm64"
    last_env: dict = field(default_factory=dict)  # env of the running script (AR3_MODE=…)

    def on(self, prefix: tuple[str, ...], handler: Handler | Result) -> FakeRunner:
        fn = handler if callable(handler) else (lambda argv, r=handler: r)
        self.handlers.append((prefix, fn))
        return self

    async def run(self, argv, timeout: float = 10.0) -> Result:
        argv = tuple(argv)
        self.calls.append(argv)
        for prefix, fn in self.handlers:
            if argv[: len(prefix)] == prefix:
                return fn(argv)
        return Result(argv, NOT_FOUND, "", f"{argv[0]}: not simulated in fake mode")

    async def stream(
        self,
        argv,
        on_line: LineHandler,
        *,
        cwd=None,
        env=None,
        timeout: float = 600.0,
        cancel: asyncio.Event | None = None,
    ) -> int:
        argv = tuple(argv)
        self.calls.append(argv)
        self.last_env = dict(env or {})
        if argv[:3] == ("docker", "logs", "-f") and self.data_dir is not None:
            return await _follow_logs(self.data_dir, argv, on_line, cancel)
        script = SCRIPTS.get(argv[:2])
        if script is None or self.data_dir is None:
            await on_line("err", f"{argv[0]}: not simulated in fake mode")
            return NOT_FOUND
        lines, effect = script
        state = load_state(self.data_dir)
        fail = state.get("fail", {}).get(" ".join(argv))
        if argv[1] in STACK_TARGETS and state.get("stack"):
            running = state["stack"]["service"]
            fail = (
                f"A ROS stack is already running ({running}). Stop it first (make down, or "
                "Ctrl-C in its terminal)."
            )
        if argv[1] in ("bag", "restart") and not state.get("stack"):
            fail = "No ROS stack is running."
        for line in lines(argv):
            if cancel is not None and cancel.is_set():
                await on_line("err", "cancelled")
                return CANCELLED
            await on_line("out", line)
            await asyncio.sleep(LINE_DELAY_S)
        if fail:
            await on_line("err", fail)
            await on_line("err", f"make: *** [{argv[-1]}] Error 1")
            return 1
        effect(self, argv)
        return 0

    @classmethod
    def from_state(cls, data_dir: Path, real_repo: Path | None = None, arch: str = "arm64"):
        """The simulated world from the state file. ``real_repo``: source of the fake checkout
        (needed by the setup checks and steps; the header status works without)."""
        state = load_state(data_dir)
        runner = cls(data_dir=Path(data_dir), arch=arch)
        if real_repo is not None:
            runner.repo = ensure_repo(data_dir, real_repo)
        tools = {
            ("git", "--version"): "git version 2.50.0",
            ("uv", "--version"): "uv 0.12.0",
            ("node", "--version"): "v22.11.0",
            ("make", "--version"): "GNU Make 4.3",
            ("sysctl",): str(64 * 1024**3),
            ("free",): "              total\nMem:    68719476736 1 2\n",
            ("id", "-nG"): "ubuntu adm dialout video docker",
            ("docker", "--version"): "Docker version 29.0.0 (fake)",
        }
        for prefix, out in tools.items():
            runner.on(prefix, _ok(out))
        runner.on(("git", "-C"), _git)
        xhost = "access control enabled, only authorized clients can connect\n"
        xhost += "LOCAL:\n" if state.get("x11", True) else "SI:localuser:ubuntu\n"
        runner.on(("xhost",), _ok(xhost))
        if not state["docker"]:
            down = "Cannot connect to the Docker daemon. Is the docker daemon running?"
            return runner.on(("docker",), _fail(down))
        info = {
            "ServerVersion": "29.0.0",
            "OperatingSystem": "Docker Desktop" if arch == "arm64" else "Ubuntu 24.04 LTS",
            "MemTotal": 16 * 1024**3,
            "Runtimes": {"runc": {}, **({"nvidia": {}} if state.get("gpu") else {})},
        }
        runner.on(("docker", "info"), _ok(json.dumps(info)))
        runner.on(("docker", "compose", "version"), _ok("2.39.1"))
        runner.on(("docker", "buildx"), _ok("github.com/docker/buildx v0.25.0"))
        if state.get("gpu"):
            temp = state.get("gpu_temp", 52)
            runner.on(
                ("nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,"
                 "temperature.gpu"),
                _ok(f"NVIDIA GeForce RTX 4060, 23, 1024, 8188, {temp}\n"),
            )  # fmt: skip
            runner.on(("nvidia-smi",), _ok("NVIDIA GeForce RTX 4060, 550.120, 8188 MiB"))
        else:
            runner.on(("nvidia-smi",), _fail("NVIDIA-SMI has failed"))
        runner.on(("docker", "image", "inspect"), lambda argv: _image(runner, state, argv[3]))
        ps_out, inspect_out = _containers(state)
        runner.on(("docker", "stats"), lambda argv: _ok(_stats(argv[5:])))
        runner.on(("docker", "ps"), _ok(ps_out))
        runner.on(("docker", "inspect"), _ok(inspect_out))
        return runner


def step_label(step: dict) -> str:
    """A step as the agent describes it (ar3_cockpit_agent.scenario.describe)."""
    ((kind, v),) = step.items()
    if kind == "move_pose":
        return f"move_pose ({v['x']}, {v['y']}, {v['z']}) {v.get('planner', 'ompl')}"
    if kind == "move_joints":
        return "move_joints [" + ", ".join(f"{float(x):.3f}" for x in v) + "]"
    return f"{kind} {v}{' s' if kind == 'wait' else ''}"


def _git(argv: tuple[str, ...]) -> Result:
    if "status" in argv:
        return _ok("")
    if "log" in argv:
        return _ok(f"{HEAD} {HEAD_TS}\n")
    return _fail("fatal: not simulated")


def _image(runner: FakeRunner, state: dict, name: str) -> Result:
    if not state["images"].get(name):
        return _fail(f"Error: No such image: {name}")
    labels = {"ar3.dockerfile_hash": dockerfile_hash(runner.repo)} if runner.repo else {}
    docker_arch = "arm64" if runner.arch == "arm64" else "amd64"
    return _ok(json.dumps({"Architecture": docker_arch, "Config": {"Labels": labels}}))


def _containers(state: dict) -> tuple[str, str]:
    """`docker ps` and `docker inspect` output for the simulated stack and the cockpit infra."""
    rows, envs = [], []
    stack = state.get("stack")
    if stack:
        service = stack["service"]
        state_ = stack.get("state", "running")
        status = "Up 5 minutes"
        if state_ == "exited":
            status = f"Exited ({stack.get('exit_code', 137)}) 3 seconds ago"
        row = _row("fake0001", service, state_, status)
        row["CreatedAt"] = str(stack.get("started", 0))
        rows.append(row)
        envs.append(
            [
                f"AR3_MODE={stack.get('ar3_mode') or ''}",
                f"AR3_HEADLESS={stack.get('headless', 0)}",
            ]
        )
    if state.get("lichtblick"):
        rows.append(_row("fake-lichtblick", "lichtblick", "running", "Up 1 minute"))
    for service in ("cockpit-db", "cockpit-redis"):
        rows.append(_row(f"fake-{service}", service, "running", "Up 1 hour (healthy)"))
    ps_out = "".join(json.dumps(r) + "\n" for r in rows)
    return ps_out, "".join(json.dumps(e) + "\n" for e in envs)


def _stats(names) -> str:
    """`docker stats --no-stream --format json <names>` of the simulated containers."""
    rows = []
    for i, name in enumerate(names):
        heavy = not name.startswith("ar3-cockpit")
        rows.append(
            {
                "Name": name,
                "CPUPerc": f"{(85.0 if heavy else 1.5) + i:.2f}%",
                "MemUsage": f"{1.8 if heavy else 0.1}GiB / 15.6GiB",
                "MemPerc": f"{11.5 if heavy else 0.6}%",
                "PIDs": str(240 if heavy else 12),
            }
        )
    return "".join(json.dumps(r) + "\n" for r in rows)


# What a stack container prints at start (`docker logs`), and state.json's ``logs``
# (container name -> lines) after it.
FAKE_STACK_LOG = (
    "[INFO] [launch]: All log files can be found below /home/ubuntu/.ros/log",
    "[ros2_control_node-1] [INFO] [1790000000.100] [controller_manager]: Loaded "
    "joint_trajectory_controller",
    "[move_group-4] [INFO] [1790000000.200] [move_group]: You can start planning now!",
)


async def _follow_logs(data_dir, argv, on_line, cancel) -> int:
    """`docker logs -f --timestamps [--since T | --tail N] <name>`: the container's lines, then
    whatever state.json adds, until the container is gone."""
    name = argv[-1]
    sent = 0
    since = argv[argv.index("--since") + 1] if "--since" in argv else ""
    while True:
        if cancel is not None and cancel.is_set():
            return CANCELLED
        state = load_state(data_dir)
        stack = state.get("stack")
        alive = (
            {f"ar3-{stack['service']}-1"}
            if stack and stack.get("state", "running") == "running"
            else set()
        )
        alive |= {"ar3-cockpit-db-1", "ar3-cockpit-redis-1"}
        if state.get("lichtblick"):
            alive.add("ar3-lichtblick-1")
        if name not in alive:
            return 0
        lines = list(FAKE_STACK_LOG if "cockpit" not in name else ()) + list(
            state.get("logs", {}).get(name, [])
        )
        started = float((stack or {}).get("started") or 0) or time.time()
        for i, text in enumerate(lines[sent:], start=sent):
            stamp = datetime.fromtimestamp(started + i * 0.01, UTC)
            iso = stamp.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"
            if since and iso <= since:
                continue
            await on_line("out", f"{iso} {text}")
        sent = len(lines)
        await asyncio.sleep(0.3)


def _row(cid: str, service: str, state: str, status: str) -> dict:
    return {
        "ID": cid,
        "Names": f"ar3-{service}-1",
        "Labels": LABELS.format(service),
        "State": state,
        "Status": f"{status} (fake)",
        "Ports": "",
    }


# --- scripted steps: argv[:2] -> (lines(argv), effect(runner, argv)) ----------------------


def _set_image(name: str):
    def effect(runner: FakeRunner, argv) -> None:
        state = load_state(runner.data_dir)
        state["images"][name] = True
        save_state(runner.data_dir, state)

    return effect


def _write(rel: str, content: Callable[[], str]):
    def effect(runner: FakeRunner, argv) -> None:
        path = runner.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content())

    return effect


def _smoke_result() -> str:
    finished = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return json.dumps({"rc": 0, "commit": HEAD, "finished": finished})


def _pull(runner: FakeRunner, argv) -> None:
    state = load_state(runner.data_dir)
    state["ollama"]["models"] = sorted({*state["ollama"]["models"], argv[2]})
    save_state(runner.data_dir, state)


def _start_stack(target: str):
    """`make <target> DETACH=1`: the stack container runs; the agent is ready 3 s later."""

    def effect(runner: FakeRunner, argv) -> None:
        service, ar3_mode = STACK_TARGETS[target]
        if service == "linux" and "GPU=1" in argv:
            service = "linux-gpu"
        env = runner.last_env
        state = load_state(runner.data_dir)
        state["stack"] = {
            "service": service,
            "ar3_mode": env.get("AR3_MODE", ar3_mode),
            "headless": 1 if "HEADLESS=1" in argv else 0,
            "started": time.time(),
        }
        if "LICHTBLICK=1" in argv:
            state["lichtblick"] = True
        save_state(runner.data_dir, state)

    return effect


def _down(runner: FakeRunner, argv) -> None:
    state = load_state(runner.data_dir)
    state["stack"], state["lichtblick"] = None, False
    save_state(runner.data_dir, state)


def _restart(runner: FakeRunner, argv) -> None:
    state = load_state(runner.data_dir)
    if state.get("stack"):
        state["stack"]["started"] = time.time()
    save_state(runner.data_dir, state)


def _set_flag(key: str):
    def effect(runner: FakeRunner, argv) -> None:
        state = load_state(runner.data_dir)
        state[key] = True
        save_state(runner.data_dir, state)

    return effect


# make target -> (compose service, AR3_MODE); argv[1] of the start commands
STACK_TARGETS = {
    "up-mac": ("mac", "mock"),
    "sim-mac": ("mac", "sim"),
    "vnc": ("vnc", "mock"),
    "up-linux": ("linux", "mock"),
    "sim-linux": ("linux", "sim"),
    "remote": ("remote", "remote"),
}


def _start_lines(target: str):
    def lines(argv) -> list[str]:
        service = STACK_TARGETS[target][0]
        return [
            f"docker compose -f docker/compose.yaml up --detach --force-recreate {service}",
            f" Container ar3-{service}-1  Started",
        ]

    return lines


def _bag_lines(argv) -> list[str]:
    duration = next((a.split("=", 1)[1] for a in argv if a.startswith("DURATION=")), "60")
    return [f"recording {duration} s of: /joint_states /tf /rosout", "[INFO] [rosbag2_recorder]: "
            "Recording...", "bag: data/bags/fake"]  # fmt: skip


def _bag(runner: FakeRunner, argv) -> None:
    """A rosbag2 directory with the metadata.yaml `ros2 bag record` writes."""
    duration = int(next((a.split("=", 1)[1] for a in argv if a.startswith("DURATION=")), "60"))
    name = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "_default"
    bag = runner.repo / "data/bags" / name
    bag.mkdir(parents=True, exist_ok=True)
    (bag / f"{name}_0.mcap").write_bytes(b"\x89MCAP0\r\n" + b"\0" * 2048)
    start_ns = int(time.time() * 1e9)  # the simulated recording starts now
    topics = [("/joint_states", "sensor_msgs/msg/JointState", 100 * duration),
              ("/rosout", "rcl_interfaces/msg/Log", 12)]  # fmt: skip
    meta = {
        "rosbag2_bagfile_information": {
            "version": 9,
            "storage_identifier": "mcap",
            "duration": {"nanoseconds": duration * 1_000_000_000},
            "starting_time": {"nanoseconds_since_epoch": start_ns},
            "message_count": sum(t[2] for t in topics),
            "topics_with_message_count": [
                {
                    "topic_metadata": {"name": n, "type": t, "serialization_format": "cdr"},
                    "message_count": c,
                }
                for n, t, c in topics
            ],  # fmt: skip
        }
    }
    import yaml

    (bag / "metadata.yaml").write_text(yaml.safe_dump(meta))


XUNIT = '<testsuite name="fake" tests="34" failures="0" errors="0"></testsuite>\n'

SCRIPTS: dict[tuple[str, ...], tuple[Callable, Callable]] = {
    ("xhost", "+local:docker"): (
        lambda a: ["non-network local connections being added to access control list"],
        _set_flag("x11"),
    ),
    ("make", "image"): (
        lambda a: [
            "docker build -f docker/Dockerfile --target dev -t ar3-dev .",
            "#5 [dev 2/8] RUN apt-get update && apt-get install -y ...",
            "#12 naming to docker.io/library/ar3-dev done",
        ],
        _set_image("ar3-dev"),
    ),
    ("make", "image-vnc"): (
        lambda a: [
            "docker build -f docker/Dockerfile --target vnc -t ar3-dev-vnc .",
            "#8 [vnc 2/4] RUN apt-get install -y tigervnc-standalone-server novnc",
        ],
        _set_image("ar3-dev-vnc"),
    ),
    ("make", "ws-build"): (
        lambda a: [
            "Starting >>> ar3_description",
            "Finished <<< ar3_description [1.2s]",
            "Summary: 8 packages finished [12.4s]",
        ],
        _write("install/setup.bash", lambda: "# fake\n"),
    ),
    ("make", "test"): (
        lambda a: [
            "colcon test --packages-select ar3_description ar3_moveit_config ar3_bringup",
            "Summary: 34 tests, 0 errors, 0 failures, 0 skipped",
        ],
        _write("build/ar3_bringup/test_results/ar3_bringup/fake.xunit.xml", lambda: XUNIT),
    ),
    ("make", "smoke-sim"): (
        lambda a: [
            "launch_test src/ar3_gazebo/test/test_sim.py",
            "[move_group-4] You can start planning now!",
            "Ran 2 tests in 71.3s",
        ],
        _write(".cockpit/results/smoke-sim.json", _smoke_result),
    ),
    ("ollama", "pull"): (
        lambda a: [f"pulling manifest {a[2]}", "verifying sha256 digest", "success"],
        _pull,
    ),
    **{("make", t): (_start_lines(t), _start_stack(t)) for t in STACK_TARGETS},
    ("make", "down"): (
        lambda a: [
            "docker compose -f docker/compose.yaml --profile '*' rm --stop --force mac vnc "
            "linux linux-gpu remote lichtblick",
            " Container ar3-mac-1  Removed",
        ],
        _down,
    ),
    ("make", "restart"): (lambda a: [" Container ar3-mac-1  Restarting"], _restart),
    ("make", "lichtblick"): (
        lambda a: [" Container ar3-lichtblick-1  Started"],
        _set_flag("lichtblick"),
    ),
    ("make", "bag"): (_bag_lines, _bag),
    ("make", "pull-viewers"): (
        lambda a: ["lichtblick Pulling", "lichtblick Pulled"],
        _set_image("ghcr.io/lichtblick-suite/lichtblick:1.30.0"),
    ),
}
