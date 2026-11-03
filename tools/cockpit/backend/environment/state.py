"""The running stack, read from Docker (docs/specs/spec_cockpit.md §3.1, §9.1).

The state comes from container labels, never from cockpit memory, so it is right after a
cockpit restart and for stacks started from the CLI.

A stack container that is still there but exited abnormally (killed, crashed, not a clean
stop by SIGINT / SIGTERM) or keeps restarting is ``dead``: `make down` removes containers, so
one that is left exited was not stopped on purpose (alert container_down, §10.5).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from core.runner import CommandRunner

from .modes import INFRA_SERVICES, PROJECT, STACK_SERVICES, Mode, identify

_EXITED = re.compile(r"Exited \((-?\d+)\)")
# 0: clean; 130 / 143: SIGINT / SIGTERM (Ctrl-C, docker stop); 137 is SIGKILL (docker kill, OOM)
CLEAN_EXIT = (0, 130, 143)


@dataclass
class Container:
    id: str
    name: str
    service: str
    state: str  # running | restarting | exited | ...
    status: str  # "Up 3 minutes"
    created: str = ""  # docker's CreatedAt; with the id it names one run of a stack
    env: dict[str, str] = field(default_factory=dict)

    @property
    def session(self) -> str:
        return f"{self.id}@{self.created}"

    @property
    def exit_code(self) -> int | None:
        m = _EXITED.search(self.status)
        return int(m.group(1)) if m else None

    @property
    def dead(self) -> bool:
        """Not running, and not stopped on purpose (see the module docstring)."""
        if self.state == "restarting" or self.state == "dead":
            return True
        return self.state == "exited" and self.exit_code not in CLEAN_EXIT

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "name", "service", "state", "status")}


@dataclass
class StackState:
    docker: bool  # daemon reachable
    containers: list[Container]
    stacks: list[tuple[Mode | None, Container]]
    error: str = ""
    dead_stacks: list[tuple[Mode | None, Container]] = field(default_factory=list)

    @property
    def stack(self) -> tuple[Mode | None, Container] | None:
        return self.stacks[0] if self.stacks else None

    @property
    def infra(self) -> list[Container]:
        return [c for c in self.containers if c.service in INFRA_SERVICES]


def _labels(raw: str) -> dict[str, str]:
    out = {}
    for part in raw.split(","):
        key, sep, value = part.partition("=")
        if sep:
            out[key] = value
    return out


async def read(runner: CommandRunner) -> StackState:
    ps = await runner.run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
            "--format",
            "{{json .}}",
        ],
        timeout=5,
    )
    if not ps.ok:
        return StackState(False, [], [], (ps.stderr or ps.stdout).strip()[:300])
    containers = []
    for line in ps.stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels = _labels(row.get("Labels", ""))
        containers.append(
            Container(
                id=row["ID"],
                name=row["Names"],
                service=labels.get("com.docker.compose.service", ""),
                state=row.get("State", ""),
                status=row.get("Status", ""),
                created=row.get("CreatedAt", ""),
            )
        )
    live = [
        c for c in containers if c.service in STACK_SERVICES and c.state not in ("exited", "dead")
    ]
    dead = [
        c for c in containers if c.service in STACK_SERVICES and c.dead and c.state != "restarting"
    ]
    inspected = live + dead
    if inspected:
        inspect = await runner.run(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", *[c.id for c in inspected]],
            timeout=5,
        )
        if inspect.ok:
            for c, line in zip(inspected, inspect.stdout.splitlines(), strict=False):
                c.env = dict(item.partition("=")[::2] for item in json.loads(line))

    def ident(c: Container):
        return identify(c.service, c.env.get("AR3_MODE"), c.env.get("AR3_HEADLESS") == "1")

    stacks = [(ident(c), c) for c in live]
    dead_stacks = [(ident(c), c) for c in dead]
    return StackState(True, containers, stacks, dead_stacks=dead_stacks)
