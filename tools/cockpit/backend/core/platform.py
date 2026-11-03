"""Host detection (docs/specs/spec_cockpit.md §4: the backend runs natively so it sees the real
host).

``detect(runner)`` returns a ``Host``. In fake mode ``fake_host(name)`` returns one of the
simulated hosts instead (macos, ubuntu, ubuntu-real).
"""

from __future__ import annotations

import platform as _platform
import socket
from dataclasses import asdict, dataclass
from pathlib import Path

from .runner import CommandRunner


@dataclass(frozen=True)
class Host:
    os: str  # "macos" | "linux" | other platform.system().lower()
    os_name: str  # "macOS", "Ubuntu", ...
    os_version: str
    arch: str  # "arm64" | "x86_64"
    hostname: str
    real_devices: bool = False  # fake ubuntu-real only, until C2 checks devices

    @property
    def label(self) -> str:
        return f"{self.os_name} {self.os_version} · {self.arch}".replace("  ", " ")

    def to_dict(self) -> dict:
        return {**asdict(self), "label": self.label}


def _arch(machine: str) -> str:
    return {"aarch64": "arm64", "amd64": "x86_64"}.get(machine.lower(), machine.lower())


def os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep:
                out[key] = value.strip().strip('"')
    return out


async def detect(runner: CommandRunner | None = None) -> Host:
    runner = runner or CommandRunner()
    system = _platform.system()
    arch = _arch(_platform.machine())
    hostname = socket.gethostname()
    if system == "Darwin":
        # platform.mac_ver() can report a compatibility version (16.0) for interpreters built
        # against an older SDK; sw_vers always reports the real one.
        res = await runner.run(["sw_vers", "-productVersion"], timeout=5)
        version = res.stdout.strip() if res.ok else _platform.mac_ver()[0]
        return Host("macos", "macOS", version, arch, hostname)
    if system == "Linux":
        rel = os_release()
        return Host("linux", rel.get("NAME", "Linux"), rel.get("VERSION_ID", ""), arch, hostname)
    return Host(system.lower(), system, _platform.release(), arch, hostname)


FAKE_HOSTS = {
    "macos": Host("macos", "macOS", "15.5", "arm64", "fake-mac"),
    "ubuntu": Host("linux", "Ubuntu", "24.04", "x86_64", "fake-workstation-a"),
    "ubuntu-real": Host("linux", "Ubuntu", "24.04", "x86_64", "fake-workstation-a", True),
}


def fake_host(name: str) -> Host:
    try:
        return FAKE_HOSTS[name]
    except KeyError:
        raise ValueError(f"COCKPIT_FAKE_PLATFORM must be one of {', '.join(FAKE_HOSTS)}") from None


async def current_host(runner: CommandRunner | None = None) -> Host:
    from django.conf import settings

    if settings.FAKE:
        from .fake import platform

        return fake_host(platform(settings.DATA_DIR, settings.FAKE_PLATFORM))
    return await detect(runner)
