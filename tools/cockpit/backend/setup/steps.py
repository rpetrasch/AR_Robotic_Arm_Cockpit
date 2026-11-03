"""The step registry (docs/specs/spec_cockpit.md §8.2): the whitelist of everything the cockpit may
run. There is no endpoint that runs anything else (principle 2).

A step is a Make target (or, for ``env.create``, a few lines of Python), typed parameters
validated by a regex, a timeout, the checks it requires and the checks to re-run afterwards.
Steps that need root are never executed: the UI shows their instructions to copy
(principle 3). Every step shows its CLI equivalent (principle 8).
"""

from __future__ import annotations

import contextvars
import re
import shlex
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .checks.pre import DOCKER_DOCS


class StepError(ValueError):
    pass


# The job a Python step runs for (set by setup.executor): its user and id.
CURRENT_JOB: contextvars.ContextVar = contextvars.ContextVar("current_job", default=None)


@dataclass(frozen=True)
class Param:
    name: str
    pattern: str
    description: str
    default: str | None = None  # used when the parameter is left out


# (repo, log, params, cancel event) -> exit code
PythonAction = Callable[
    [Path, Callable[[str, str], Awaitable[None]], dict[str, str], object], Awaitable[int]
]
# A planned command: (environment additions, argv). Plans run in order, stopping at a failure.
Cmd = tuple[dict[str, str], tuple[str, ...]]
# After the commands succeeded: wait for something (e.g. the stack to be ready); exit code.
AfterHook = Callable[[dict, Callable[[str, str], Awaitable[None]], object], Awaitable[int]]


def shell_words(cmd: Cmd) -> str:
    env, argv = cmd
    return shlex.join([f"{k}={v}" for k, v in env.items()] + list(argv))


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    description: str
    argv: tuple[str, ...] = ()  # "{model}" placeholders are filled from params
    params: tuple[Param, ...] = ()
    python: PythonAction | None = None
    cli: str = ""  # shown for python steps (argv steps show their argv)
    platforms: tuple[str, ...] = ("linux", "macos")
    requires: tuple[str, ...] = ()  # check ids that must pass first
    timeout_s: float = 600.0
    mutating: bool = True
    needs_root: bool = False
    instructions: dict[str, tuple[str, ...]] = field(default_factory=dict)  # os -> commands
    instructions_text: str = ""
    # os -> the official install guide, shown as a link in the UI
    doc_urls: dict[str, str] = field(default_factory=dict)
    post_checks: tuple[str, ...] = ()
    plan: Callable[[dict[str, str]], list[Cmd]] | None = None  # instead of argv
    host_check: Callable[[dict[str, str], str], None] | None = None  # raises StepError
    after: AfterHook | None = None
    group: str = "setup"  # setup | environment | monitoring | simulations (where the UI offers it)
    cli_for: Callable[[dict[str, str]], str] | None = None  # python steps: CLI with params

    def validate(self, params: dict | None) -> dict[str, str]:
        params = dict(params or {})
        unknown = set(params) - {p.name for p in self.params}
        if unknown:
            raise StepError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        out = {}
        for p in self.params:
            value = params.get(p.name, p.default)
            if not isinstance(value, str) or not re.fullmatch(p.pattern, value):
                raise StepError(f"{p.name}: {p.description}")
            out[p.name] = value
        return out

    def build_argv(self, params: dict[str, str]) -> list[str]:
        return [part.format(**params) for part in self.argv]

    def commands(self, params: dict[str, str]) -> list[Cmd]:
        if self.plan is not None:
            return self.plan(params)
        return [({}, tuple(self.build_argv(params)))]

    def command(self, params: dict[str, str] | None = None) -> str:
        """The CLI equivalent (principle 8); placeholders stay visible without params."""
        if self.python is not None:
            if params and callable(self.cli_for):
                return self.cli_for(params)
            return self.cli
        if params is None:
            return self.cli or " ".join(self.argv)
        return " && ".join(shell_words(c) for c in self.commands(params))

    def to_dict(self, host_os: str) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "kind": "manual" if self.needs_root else ("python" if self.python else "command"),
            "command": self.command(),
            "params": [p.__dict__ for p in self.params],
            "available": host_os in self.platforms,
            "requires": list(self.requires),
            "timeout_s": self.timeout_s,
            "mutating": self.mutating,
            "needs_root": self.needs_root,
            "instructions": list(self.instructions.get(host_os, ())),
            "instructions_text": self.instructions_text,
            "doc_url": self.doc_urls.get(host_os, ""),
            "post_checks": list(self.post_checks),
            "group": self.group,
        }


async def create_docker_env(repo: Path, log, params=None, cancel=None) -> int:
    """docker/.env from docker/.env.example (Python, not a shell copy). Never overwrites."""
    src, dst = repo / "docker/.env.example", repo / "docker/.env"
    if dst.exists():
        await log("out", "docker/.env exists already; left unchanged")
        return 0
    shutil.copyfile(src, dst)
    await log("out", "created docker/.env from docker/.env.example")
    await log(
        "out", "Edit it only for Zenoh (ROS_RMW, ZENOH_ROUTER); see docs/manual/usage-mac.md."
    )
    return 0


# --- environment steps (§9.1): start, stop, restart the stack; Lichtblick web ------------


def _flag(value: str | None) -> bool:
    return value == "1"


def _start_plan(params: dict[str, str]) -> list[Cmd]:
    from environment import modes

    try:
        mode = modes.startable(params["mode"])
    except modes.ModeError as exc:
        raise StepError(str(exc)) from None
    plan: list[Cmd] = [({}, ("make", "down"))] if _flag(params.get("switch")) else []
    return [*plan, mode.start(lichtblick=_flag(params.get("lichtblick")))]


def _start_host_check(params: dict[str, str], host_os: str) -> None:
    from environment import modes

    try:
        modes.startable(params["mode"], host_os)
    except modes.ModeError as exc:
        raise StepError(str(exc)) from None


async def _wait_ready(params, log, cancel) -> int:
    from environment.health import wait_ready

    return await wait_ready(params["mode"], log, cancel)


async def _wait_stopped(params, log, cancel) -> int:
    from environment.health import wait_stopped

    return await wait_stopped(log, cancel)


MODE = Param("mode", r"[a-z][a-z0-9_]{0,40}", "a mode id from /api/env/modes")


def _flag_param(name: str, description: str) -> Param:
    return Param(name, r"[01]", description, default="0")


async def _export(repo: Path, log, params: dict[str, str], cancel=None) -> int:
    """telemetry.export: the same code as `ar3ctl telemetry-export` (telemetry.export)."""
    from asgiref.sync import sync_to_async

    from telemetry.export import ExportError, export

    lines: list[str] = []
    try:
        await sync_to_async(export, thread_sensitive=False)(
            params["session"],
            params["format"],
            params.get("run") or None,
            _iso(params.get("from")),
            _iso(params.get("to")),
            log=lines.append,
        )
    except ExportError as exc:
        await log("err", str(exc))
        return 1
    finally:
        for line in lines:
            await log("out", line)
    return 0


def _iso(value: str | None):
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None


def _export_cli(params: dict[str, str]) -> str:
    parts = [
        "make",
        "telemetry-export",
        f"SESSION={params['session']}",
        f"FORMAT={params['format']}",
    ]
    for key, var in (("run", "RUN"), ("from", "FROM"), ("to", "TO")):
        if params.get(key):
            parts.append(f"{var}={params[key]}")
    return shlex.join(parts)


async def _register_bag(params, log, cancel) -> int:
    from telemetry.recordings import register_newest

    rec = await register_newest(params)
    if rec is None:
        await log("err", "no bag found under data/bags/")
        return 1
    await log("out", f"registered recording {rec['path']} ({rec['size'] / 1e6:.1f} MB)")
    return 0


async def _scenario_run(repo: Path, log, params: dict[str, str], cancel=None) -> int:
    from scenarios.execute import run_scenario

    job = CURRENT_JOB.get()
    user = job.user if job is not None else "cli"
    return await run_scenario(params, log, cancel, user=user, job_id=job.id if job else None)


def _scenario_cli(params: dict[str, str]) -> str:
    parts = ["make", "scenario-run", f"ID={params['scenario']}"]
    for key, var in (("mode", "MODE"), ("repeat", "REPEAT"), ("seed", "SEED")):
        if params.get(key) and not (key == "repeat" and params[key] == "1"):
            parts.append(f"{var}={params[key]}")
    if params.get("keep_scene") == "1":
        parts.append("KEEP_SCENE=1")
    if params.get("record") == "1":
        parts.append("BAG=1")
    return shlex.join(parts)


async def _assistant_index(repo: Path, log, params: dict[str, str], cancel=None) -> int:
    """assistant.index: the same code as `ar3ctl assistant-index` (assistant.index.refresh)."""
    from asgiref.sync import async_to_sync, sync_to_async

    from assistant.index import refresh

    def line(text: str) -> None:
        async_to_sync(log)("out", text)

    def run() -> dict:
        from django.db import connection

        try:
            return refresh(full=params.get("full") == "1", log_line=line)
        finally:
            connection.close()  # this worker thread's own connection

    value = await sync_to_async(run, thread_sensitive=False)()
    if value.get("degraded"):
        await log("out", f"[WARN] full-text only: {value.get('error') or 'no embeddings'}")
    return 0


UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ISO_TIME = r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)?"

MODEL = Param("model", r"[a-z0-9][a-z0-9._/-]{0,80}(:[a-z0-9._-]{1,40})?", "an Ollama model name")

STEPS: tuple[Step, ...] = (
    Step(
        "env.create",
        "Create docker/.env",
        "Copies docker/.env.example to docker/.env (compose settings: ROS middleware, Zenoh).",
        python=create_docker_env,
        cli="cp docker/.env.example docker/.env",
        timeout_s=10,
        post_checks=("repo.env", "net.zenohd"),
    ),
    Step(
        "images.build",
        "Build the dev image",
        "Builds ar3-dev (ROS 2 Jazzy, MoveIt, Gazebo) for this host's architecture. "
        "The first build takes about 10 minutes.",
        argv=("make", "image"),
        timeout_s=3600,
        requires=("docker.daemon",),
        post_checks=("images.dev",),
    ),
    Step(
        "images.build_vnc",
        "Build the noVNC image",
        "Builds ar3-dev-vnc (RViz in the browser, `make vnc`) on top of ar3-dev.",
        argv=("make", "image-vnc"),
        timeout_s=3600,
        requires=("docker.daemon",),
        post_checks=("images.dev", "images.vnc"),
    ),
    Step(
        "ws.build",
        "Build the workspace",
        "colcon build inside the dev container (install/ in the repo).",
        argv=("make", "ws-build"),
        timeout_s=1800,
        requires=("images.dev",),
        post_checks=("ws.built",),
    ),
    Step(
        "tests.run",
        "Run the tests",
        "Unit and mock integration tests (the same as CI).",
        argv=("make", "test"),
        timeout_s=1800,
        requires=("ws.built",),
        post_checks=("tests.last",),
    ),
    Step(
        "sim.smoke",
        "Simulation smoke test",
        "Headless Gazebo: cameras publish, MoveIt moves through the named poses (1–2 min).",
        argv=("make", "smoke-sim"),
        timeout_s=900,
        requires=("ws.built",),
        post_checks=("sim.smoke",),
    ),
    Step(
        "ollama.pull",
        "Pull an Ollama model",
        "Downloads a model into the local Ollama (for the assistant).",
        argv=("ollama", "pull", "{model}"),
        params=(MODEL,),
        timeout_s=3600,
        requires=("ai.ollama",),
        post_checks=("ai.models",),
    ),
    Step(
        "assistant.index",
        "Rebuild the assistant's index",
        "Indexes the docs, READMEs and config/ for the assistant: full-text, and embeddings "
        "with the embed model when Ollama runs. Without FULL only changed files are indexed.",
        params=(_flag_param("full", "1: index and embed every file again"),),
        python=_assistant_index,
        cli="make assistant-index",
        cli_for=lambda p: "make assistant-index" + (" FULL=1" if p.get("full") == "1" else ""),
        timeout_s=3600,
        mutating=False,
    ),
    Step(
        "images.pull_viewers",
        "Pull the Lichtblick web image",
        "Downloads Lichtblick for the browser (pinned in docker/compose.yaml), served on :8080.",
        argv=("make", "pull-viewers"),
        timeout_s=900,
        requires=("docker.daemon",),
        post_checks=("viewers.lichtblick",),
    ),
    Step(
        "x11.allow",
        "Allow Docker windows on this desktop",
        "Runs `xhost +local:docker` so that RViz and Gazebo in the container can open windows "
        "on your screen. It lasts until you log out.",
        argv=("xhost", "+local:docker"),
        platforms=("linux",),
        timeout_s=15,
        post_checks=("viewers.x11",),
    ),
    Step(
        "env.start",
        "Start the ROS stack",
        "Starts the chosen mode in the background and waits until MoveIt and the controllers "
        "are ready (at most 180 s after the containers are up).",
        params=(
            MODE,
            _flag_param("lichtblick", "1: also start Lichtblick web (:8080)"),
            _flag_param("switch", "1: stop the running stack first (make down)"),
        ),
        plan=_start_plan,
        host_check=_start_host_check,
        after=_wait_ready,
        cli="make <mode target> DETACH=1",
        timeout_s=3600,  # includes `make image` when the image is missing
        requires=("docker.daemon",),
        group="environment",
    ),
    Step(
        "env.stop",
        "Stop the ROS stack",
        "Stops and removes the running stack's container and Lichtblick web.",
        argv=("make", "down"),
        after=_wait_stopped,
        timeout_s=120,
        group="environment",
    ),
    Step(
        "env.restart",
        "Restart the ROS stack",
        "Restarts the running stack's container with the same mode and settings.",
        argv=("make", "restart"),
        timeout_s=120,
        group="environment",
    ),
    Step(
        "viewers.lichtblick",
        "Start Lichtblick web",
        "Lichtblick in the browser on :8080; it connects to foxglove_bridge (:8765).",
        argv=("make", "lichtblick"),
        timeout_s=900,
        requires=("docker.daemon",),
        group="environment",
    ),
    Step(
        "telemetry.export",
        "Export telemetry",
        "Writes a recorded session (or a run, or a time range of it) to data/exports/ as "
        "Parquet or CSV: joint, gripper and safety samples, and the events.",
        params=(
            Param("session", UUID, "a session id (GET /api/sessions)"),
            Param("format", r"parquet|csv", "parquet or csv", default="parquet"),
            Param("run", rf"({UUID})?", "a run id, or empty", default=""),
            Param("from", rf"({ISO_TIME})?", "ISO 8601 start, or empty", default=""),
            Param("to", rf"({ISO_TIME})?", "ISO 8601 end, or empty", default=""),
        ),
        python=_export,
        cli="make telemetry-export SESSION=<id> FORMAT=parquet",
        cli_for=_export_cli,
        timeout_s=1800,
        mutating=False,
        group="monitoring",
    ),
    Step(
        "rosbag.record",
        "Record a rosbag",
        "Records a preset of topics (config/cockpit/telemetry.yaml) as MCAP in the running "
        "stack, for the given number of seconds, to data/bags/. Runs next to other jobs.",
        argv=("make", "bag", "PRESET={preset}", "DURATION={duration}"),
        params=(
            Param("preset", r"[a-z][a-z0-9_]{0,30}", "a preset of bag_presets", default="default"),
            Param("duration", r"[1-9]\d{0,3}", "seconds, 1 to 9999", default="60"),
        ),
        after=_register_bag,
        timeout_s=10_100,
        mutating=False,
        group="monitoring",
    ),
    Step(
        "scenario.run",
        "Run a scenario",
        "Runs a scenario of config/scenarios/ in the running simulation: scene, steps, success "
        "criteria; the run and its telemetry are stored. Cancel aborts it (goal cancelled, hold).",
        params=(
            Param("scenario", r"[a-z0-9][a-z0-9-]{0,63}", "a scenario id"),
            Param(
                "mode",
                r"(sim_mock|sim_gazebo|real)?",
                "the mode, or empty: the running one",
                default="",
            ),
            Param("repeat", r"[1-9]\d?|100", "1 to 100 runs", default="1"),
            Param("seed", r"(\d{1,9})?", "randomize seed, or empty", default=""),
            _flag_param("keep_scene", "1: leave the scene objects after the run"),
            _flag_param("record", "1: record a rosbag of the run"),
        ),  # fmt: skip
        python=_scenario_run,
        cli="make scenario-run ID=<id>",
        cli_for=_scenario_cli,
        timeout_s=4 * 3600,
        group="simulations",
    ),
    # --- needs root: instructions only (principle 3) --------------------------------------
    Step(
        "docker.install",
        "Install Docker",
        "Docker runs the ROS stacks and the cockpit's database.",
        needs_root=True,
        instructions={
            "macos": (
                "brew install --cask docker   # or download Docker Desktop / OrbStack",
                "open -a Docker               # then Settings → Resources → Memory ≥ 8 GB",
            ),
            "linux": (
                "sudo apt install docker-ce docker-ce-cli containerd.io "
                "docker-buildx-plugin docker-compose-plugin",
            ),
        },
        instructions_text="Then start ./cockpit.sh again.",
        doc_urls=DOCKER_DOCS,
    ),
    Step(
        "groups.add",
        "Add yourself to the docker, dialout and video groups",
        "Docker without sudo; serial ports and cameras for the real arm.",
        platforms=("linux",),
        needs_root=True,
        instructions={"linux": ("sudo usermod -aG docker,dialout,video $USER",)},
        instructions_text="Log out and back in afterwards. Membership in `docker` is "
        "equivalent to root on this machine.",
    ),
    Step(
        "nvidia.driver",
        "Install the NVIDIA driver",
        "Needed for GPU rendering (GPU=1) and the AI phases.",
        platforms=("linux",),
        needs_root=True,
        instructions={"linux": ("sudo ubuntu-drivers install", "sudo reboot")},
        instructions_text="Afterwards `nvidia-smi` lists the GPU (docs/manual/install.md).",
    ),
    Step(
        "nvidia.toolkit",
        "Install the NVIDIA Container Toolkit",
        "Lets containers use the GPU (`GPU=1`, service dev-gpu).",
        platforms=("linux",),
        needs_root=True,
        instructions={
            "linux": (
                "sudo nvidia-ctk runtime configure --runtime=docker",
                "sudo systemctl restart docker",
            )
        },
        doc_urls={
            "linux": "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/"
            "install-guide.html"
        },
    ),
    Step(
        "udev.install",
        "Install the udev rules",
        "Stable device names for the Teensy, the Nano and the cameras.",
        platforms=("linux",),
        needs_root=True,
        instructions_text="Arrives with Phase 1: the rules need the confirmed device IDs.",
    ),
)

BY_ID = {s.id: s for s in STEPS}


def get(step_id: str) -> Step:
    try:
        return BY_ID[step_id]
    except KeyError:
        raise StepError(f"unknown step {step_id!r}") from None
