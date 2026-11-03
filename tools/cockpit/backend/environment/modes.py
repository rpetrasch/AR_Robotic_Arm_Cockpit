"""The stacks the cockpit knows (docs/specs/spec_cockpit.md §3, docs/manual/cockpit.md "Modes").

A stack = compose service + AR3_MODE (+ AR3_HEADLESS on Linux). foxglove_bridge (:8765)
runs in every stack, so Lichtblick can always connect. Confirmed by the owner: the macOS
matrix (§3.2) and the Linux rows with native RViz / Gazebo windows (2026-11-01).
"""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass

PROJECT = "ar3"
# One at a time (§3.1); `make down` stops all but dev / dev-gpu (interactive `make shell`).
STACK_SERVICES = ("mac", "vnc", "linux", "linux-gpu", "remote", "dev", "dev-gpu")
INFRA_SERVICES = ("cockpit-db", "cockpit-redis")  # cockpit profile, outside the rule (§6)


@dataclass(frozen=True)
class Mode:
    id: str
    chip: str  # header mode chip (§7.2)
    robot: str  # "mock" | "gazebo" | "real" | "unknown"
    rviz: bool  # RViz in the browser (noVNC, :6080)
    viewers: str  # what you see besides Lichtblick
    command: str  # the CLI command that starts it (principle 8)
    service: str
    ar3_mode: str | None
    platforms: tuple[str, ...]
    headless: bool | None = None  # AR3_HEADLESS of the linux services; None: not used
    startable: bool = True  # the cockpit can start it (REAL waits for Phase 1)
    why_not: str = ""  # shown when not startable

    def to_dict(self) -> dict:
        return asdict(self)

    def start(self, lichtblick: bool = False) -> tuple[dict[str, str], tuple[str, ...]]:
        """(env, argv) the cockpit runs: the CLI command, detached (the cockpit stops it with
        `make down`), with Lichtblick web if asked. ``AR3_MODE=sim make vnc`` keeps its
        environment prefix, so the job shows the same command a terminal would run."""
        words = shlex.split(self.command)
        env = {}
        while words and "=" in words[0] and not words[0].startswith("make"):
            key, _sep, value = words.pop(0).partition("=")
            env[key] = value
        extra = ["DETACH=1"] + (["LICHTBLICK=1"] if lichtblick else [])
        return env, (*words, *extra)


BROWSER = "RViz in the browser (:6080)"

MAC_MODES = (
    Mode("sim_mock", "SIM · MOCK", "mock", False, "Lichtblick", "make up-mac", "mac", "mock",
         ("macos",)),
    Mode("sim_gazebo", "SIM · GAZEBO", "gazebo", False, "Lichtblick", "make sim-mac", "mac",
         "sim", ("macos",)),
)  # fmt: skip

# The vnc profile runs on both hosts (software rendering, no GPU).
VNC_MODES = (
    Mode("sim_mock_rviz", "SIM · MOCK", "mock", True, BROWSER, "make vnc", "vnc", "mock",
         ("macos", "linux")),
    Mode("sim_gazebo_rviz", "SIM · GAZEBO", "gazebo", True, BROWSER, "AR3_MODE=sim make vnc",
         "vnc", "sim", ("macos", "linux")),
)  # fmt: skip


def _linux_modes() -> tuple[Mode, ...]:
    rows = []
    for service, gpu in (("linux", ""), ("linux-gpu", " GPU=1")):
        suffix = "_gpu" if gpu else ""
        rows += [
            Mode(f"linux_mock{suffix}", "SIM · MOCK", "mock", False, "RViz window",
                 f"make up-linux{gpu}", service, "mock", ("linux",), False),
            Mode(f"linux_gazebo{suffix}", "SIM · GAZEBO", "gazebo", False,
                 "Gazebo and RViz windows", f"make sim-linux{gpu}", service, "sim", ("linux",),
                 False),
            Mode(f"linux_mock_headless{suffix}", "SIM · MOCK", "mock", False, "Lichtblick",
                 f"make up-linux HEADLESS=1{gpu}", service, "mock", ("linux",), True),
            Mode(f"linux_gazebo_headless{suffix}", "SIM · GAZEBO", "gazebo", False, "Lichtblick",
                 f"make sim-linux HEADLESS=1{gpu}", service, "sim", ("linux",), True),
        ]  # fmt: skip
    return tuple(rows)


LINUX_MODES = _linux_modes()

# REAL needs ar3_hardware (SPEC Phase 1) and is refused by the bringup today. The entry lets
# fake mode (COCKPIT_FAKE_PLATFORM=ubuntu-real) show the REAL header; C3 replaces the command.
REAL_MODE = Mode(
    "real", "REAL · AR3", "real", False, "RViz window",
    "ros2 launch ar3_bringup bringup.launch.py mode:=real  # Phase 1", "dev", "real", ("linux",),
    startable=False,
    why_not="REAL needs the AR3 hardware interface and ar3_safety (SPEC Phase 1); the bringup "
    "refuses mode:=real until then.",
)  # fmt: skip

# REAL on the workstation, watched (and with COCKPIT_REMOTE_MOTION=1 there, driven) from the
# Mac: only the cockpit agent runs here, as a Zenoh client of Workstation A (§3.3, §9.5).
REMOTE_MODE = Mode(
    "remote", "REMOTE", "remote", False, "Lichtblick via Workstation A's bridge", "make remote",
    "remote", "remote", ("macos",),
)  # fmt: skip

# An interactive dev container (`make shell`): the cockpit cannot tell what runs inside it.
DEV_MODES = (
    Mode("dev_shell", "ROS · DEV", "unknown", False, "", "make shell", "dev", None, ("linux",),
         startable=False, why_not="An interactive shell: start it in a terminal."),
    Mode("dev_gpu_shell", "ROS · DEV", "unknown", False, "",
         "docker compose -f docker/compose.yaml run --rm dev-gpu", "dev-gpu", None, ("linux",),
         startable=False, why_not="An interactive shell: start it in a terminal."),
)  # fmt: skip

ALL_MODES = MAC_MODES + VNC_MODES + LINUX_MODES + (REAL_MODE, REMOTE_MODE) + DEV_MODES
BY_ID = {m.id: m for m in ALL_MODES}


class ModeError(ValueError):
    pass


def startable(mode_id: str, host_os: str | None = None) -> Mode:
    """The mode, if the cockpit may start it (on ``host_os``); else ModeError."""
    mode = BY_ID.get(mode_id)
    if mode is None:
        raise ModeError(f"unknown mode {mode_id!r}")
    if not mode.startable:
        raise ModeError(mode.why_not or f"{mode_id} cannot be started from the cockpit")
    if host_os is not None and host_os not in mode.platforms:
        raise ModeError(f"{mode_id} is not available on this host")
    return mode


def identify(service: str, ar3_mode: str | None, headless: bool = False) -> Mode | None:
    """The mode of a running stack container, or None for a non-stack service."""
    for mode in ALL_MODES:
        if mode.service != service:
            continue
        if mode.ar3_mode is not None and mode.ar3_mode != ar3_mode:
            continue
        if mode.headless is not None and mode.headless != headless:
            continue
        return mode
    return None
