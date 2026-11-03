"""The one way the cockpit runs programs (docs/specs/spec_cockpit.md §5, principle 2).

``CommandRunner.run(argv)`` executes an argv list with ``asyncio.create_subprocess_exec``,
never through a shell. ``stream()`` does the same for long jobs: line by line, in its own
process group, cancellable (SIGTERM, then SIGKILL after 10 s, §8.3). ``FakeRunner``
(core.fake) answers the same calls in COCKPIT_FAKE mode and in tests. ``get_runner()`` picks
the implementation from the settings.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    returncode: int  # -1: not found, -2: timed out
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


NOT_FOUND = -1
TIMED_OUT = -2
CANCELLED = -3
KILL_AFTER_S = 10.0

# on_line(stream, text): stream is "out" or "err"
LineHandler = Callable[[str, str], Awaitable[None]]


@dataclass
class CommandRunner:
    env: dict[str, str] = field(default_factory=dict)

    async def run(self, argv: list[str] | tuple[str, ...], timeout: float = 10.0) -> Result:
        argv = tuple(argv)
        if not argv or not isinstance(argv[0], str):
            raise ValueError("argv must be a non-empty list of strings")
        exe = shutil.which(argv[0])
        if exe is None:
            return Result(argv, NOT_FOUND, "", f"{argv[0]}: command not found")
        proc = await asyncio.create_subprocess_exec(
            exe,
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env={**os.environ, **self.env},
            start_new_session=True,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return Result(argv, TIMED_OUT, "", f"timed out after {timeout:g} s")
        return Result(
            argv, proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")
        )

    async def stream(
        self,
        argv: list[str] | tuple[str, ...],
        on_line: LineHandler,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 600.0,
        cancel: asyncio.Event | None = None,
    ) -> int:
        """Run ``argv``, pass every output line to ``on_line``; returns the exit code, or
        NOT_FOUND / TIMED_OUT / CANCELLED."""
        argv = tuple(argv)
        exe = shutil.which(argv[0])
        if exe is None:
            await on_line("err", f"{argv[0]}: command not found")
            return NOT_FOUND
        proc = await asyncio.create_subprocess_exec(
            exe,
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env={**os.environ, **self.env, **(env or {})},
            start_new_session=True,  # own process group: cancel reaches make's children too
        )

        async def pump(reader: asyncio.StreamReader, name: str) -> None:
            while line := await reader.readline():
                await on_line(name, line.decode(errors="replace").rstrip("\r\n"))

        pumps = asyncio.gather(pump(proc.stdout, "out"), pump(proc.stderr, "err"))
        waiter = asyncio.ensure_future(proc.wait())
        cancelled = asyncio.ensure_future((cancel or asyncio.Event()).wait())
        done, _ = await asyncio.wait(
            {waiter, cancelled}, timeout=timeout, return_when="FIRST_COMPLETED"
        )
        outcome = None if waiter in done else (CANCELLED if cancelled in done else TIMED_OUT)
        if outcome is not None:
            await on_line(
                "err", "cancelled" if outcome == CANCELLED else f"timed out after {timeout:g} s"
            )
            _signal_group(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.shield(waiter), KILL_AFTER_S)
            except TimeoutError:
                _signal_group(proc.pid, signal.SIGKILL)
                await waiter
        cancelled.cancel()
        await pumps
        return outcome if outcome is not None else proc.returncode


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def get_runner() -> CommandRunner:
    from django.conf import settings

    if settings.FAKE:
        from .fake import FakeRunner, platform
        from .platform import fake_host

        arch = fake_host(platform(settings.DATA_DIR, settings.FAKE_PLATFORM)).arch
        return FakeRunner.from_state(settings.DATA_DIR, settings.REPO_DIR, arch)
    return CommandRunner()
