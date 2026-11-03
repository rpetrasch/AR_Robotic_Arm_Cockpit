"""Check model (docs/specs/spec_cockpit.md §8.1). Checks are read-only, idempotent and get 10 s;
a check that runs out of time reports ``unknown``.

A probe gets a ``CheckContext``: the command runner, the host, the repository, network probes
and the settings. In fake mode all of them are simulated (core.fake), so the same probes run.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.net import Net
from core.platform import Host
from core.runner import CommandRunner, Result

STATUSES = ("pass", "warn", "fail", "skip", "unknown", "running")
TIMEOUT_S = 10.0
GB = 1024**3


@dataclass
class Outcome:
    status: str
    detail: str
    fix: str = ""  # what to do, in words (manual items and needs-root steps)
    fix_commands: list[str] = field(default_factory=list)  # commands to copy
    fix_step: str = ""  # a step id the cockpit can run (§8.2)
    fix_params: dict[str, Any] = field(default_factory=dict)
    cli: list[str] | None = None  # "Run it yourself" with this host's values (else the Check's)


@dataclass
class CheckResult:
    id: str
    title: str
    group: str
    status: str
    detail: str
    fix: str
    fix_commands: list[str]
    fix_step: str
    fix_params: dict[str, Any]
    cli: list[str]  # "Run it yourself" (principle 8)
    docs: str
    last_checked: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckContext:
    runner: CommandRunner
    host: Host
    repo: Path
    net: Net
    env: Mapping[str, str]
    disk_free_gb_override: float | None = None  # fake mode
    _memo: dict[str, asyncio.Task] = field(default_factory=dict)

    async def run(self, argv: list[str], timeout: float = 8.0) -> Result:
        """Runs a command once per context: several checks share e.g. ``docker info``."""
        key = "\0".join(argv)
        if key not in self._memo:
            self._memo[key] = asyncio.ensure_future(self.runner.run(argv, timeout=timeout))
        return await asyncio.shield(self._memo[key])

    def disk_free_gb(self) -> float:
        if self.disk_free_gb_override is not None:
            return self.disk_free_gb_override
        return shutil.disk_usage(self.repo).free / GB


Probe = Callable[[CheckContext], Awaitable[Outcome]]


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    group: str
    probe: Probe
    cli: tuple[str, ...] = ()
    docs: str = "docs/manual/cockpit.md"
    platforms: tuple[str, ...] = ("linux", "macos")

    async def run(self, ctx: CheckContext) -> CheckResult:
        now = datetime.now(UTC).isoformat()
        if ctx.host.os not in self.platforms:
            outcome = Outcome("skip", f"not needed on {ctx.host.os_name}")
        else:
            try:
                outcome = await asyncio.wait_for(self.probe(ctx), TIMEOUT_S)
            except TimeoutError:
                outcome = Outcome("unknown", f"no answer within {TIMEOUT_S:g} s")
            except Exception as exc:  # a broken probe reports, it does not crash the list
                outcome = Outcome("unknown", f"check failed: {exc}")
        return CheckResult(
            id=self.id,
            title=self.title,
            group=self.group,
            status=outcome.status,
            detail=outcome.detail,
            fix=outcome.fix,
            fix_commands=outcome.fix_commands,
            fix_step=outcome.fix_step,
            fix_params=outcome.fix_params,
            cli=list(self.cli if outcome.cli is None else outcome.cli),
            docs=self.docs,
            last_checked=now,
        )


async def run_all(checks, ctx: CheckContext) -> list[CheckResult]:
    return list(await asyncio.gather(*(c.run(ctx) for c in checks)))
