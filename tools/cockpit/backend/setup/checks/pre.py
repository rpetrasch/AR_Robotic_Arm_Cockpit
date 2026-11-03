"""Pre-Docker checks (docs/specs/spec_cockpit.md §4 bootstrap, §8.1 groups Host / Prerequisites /
Docker). cockpit.sh runs them as ``ar3ctl doctor --pre`` before the database exists; the
Install & Setup page shows the same results."""

from __future__ import annotations

import json
import re
import sys

from core.platform import Host

from .base import GB, Check, CheckContext, Outcome

DOCKER_DOCS = {
    "macos": "https://docs.docker.com/desktop/setup/install/mac-install/",
    "linux": "https://docs.docker.com/engine/install/ubuntu/",
}


def _version(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(g) for g in m.groups() if g is not None) if m else ()


# --- Host ---------------------------------------------------------------------------------


async def os_supported(ctx: CheckContext) -> Outcome:
    if ctx.host.os == "macos":
        if _version(ctx.host.os_version) >= (14,) and ctx.host.arch == "arm64":
            return Outcome("pass", ctx.host.label)
        return Outcome("warn", f"{ctx.host.label}: supported is macOS ≥ 14 on Apple Silicon")
    if ctx.host.os == "linux":
        if ctx.host.os_name == "Ubuntu" and ctx.host.os_version == "24.04":
            return Outcome("pass", ctx.host.label)
        return Outcome(
            "warn", f"{ctx.host.label}: supported is Ubuntu 24.04 (docs/manual/install.md)"
        )
    return Outcome("fail", f"{ctx.host.label}: the cockpit supports Ubuntu 24.04 and macOS")


async def cpu_arch(ctx: CheckContext) -> Outcome:
    if ctx.host.arch in ("arm64", "x86_64"):
        return Outcome("pass", ctx.host.arch)
    return Outcome("fail", f"{ctx.host.arch}: the images exist for amd64 and arm64 only")


async def ram(ctx: CheckContext) -> Outcome:
    if ctx.host.os == "macos":
        res = await ctx.run(["sysctl", "-n", "hw.memsize"], timeout=5)
        total = int(res.stdout.strip()) if res.ok and res.stdout.strip().isdigit() else None
    else:
        res = await ctx.run(["free", "-b"], timeout=5)
        m = re.search(r"^Mem:\s+(\d+)", res.stdout, re.M) if res.ok else None
        total = int(m.group(1)) if m else None
    if total is None:
        return Outcome("unknown", "could not read the memory size")
    gb = total / GB
    if gb >= 16:
        return Outcome("pass", f"{gb:.0f} GB")
    return Outcome("warn", f"{gb:.0f} GB: 16 GB or more is recommended for Gazebo + MoveIt")


async def disk(ctx: CheckContext) -> Outcome:
    free = ctx.disk_free_gb()
    if free >= 40:
        return Outcome("pass", f"{free:.0f} GB free")
    if free >= 15:
        return Outcome("warn", f"{free:.0f} GB free: 40 GB recommended (images, bags, data)")
    return Outcome("fail", f"{free:.0f} GB free: at least 15 GB are needed")


async def git(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["git", "--version"], timeout=5)
    if not res.ok:
        return Outcome("fail", "git not found", "Install git.", _pkg(ctx.host, "git"))
    return Outcome("pass", res.stdout.strip())


async def repo_clean(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["git", "-C", str(ctx.repo), "status", "--porcelain"], timeout=5)
    if not res.ok:
        return Outcome("unknown", (res.stderr or "not a git checkout").strip())
    changed = [line for line in res.stdout.splitlines() if line.strip()]
    # Information only (§8.1): local changes are normal while developing.
    return Outcome("pass", "clean" if not changed else f"{len(changed)} changed file(s)")


# --- Prerequisites --------------------------------------------------------------------------


async def python(ctx: CheckContext) -> Outcome:
    v = sys.version_info
    text = f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if (v.major, v.minor) >= (3, 12):
        return Outcome("pass", text)
    return Outcome(
        "fail", f"{text}: 3.12 or newer is needed", "uv installs it: uv python install 3.12"
    )


async def uv(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["uv", "--version"], timeout=5)
    if res.ok:
        return Outcome("pass", res.stdout.strip())
    return Outcome(
        "fail",
        "uv not found",
        "Install uv (Python package manager), then open a new terminal.",
        ["curl -LsSf https://astral.sh/uv/install.sh | sh"],
    )


async def node(ctx: CheckContext) -> Outcome:
    built = (ctx.repo / "tools/cockpit/frontend/out/index.html").exists()
    res = await ctx.run(["node", "--version"], timeout=5)
    missing = "warn" if built else "fail"
    why = "only needed to rebuild the frontend" if built else "needed to build the frontend"
    fix = (
        ["brew install node@22"]
        if ctx.host.os == "macos"
        else ["see https://nodejs.org/en/download"]
    )
    if not res.ok:
        return Outcome(missing, f"Node.js not found ({why})", "Install Node.js 22 or newer.", fix)
    if _version(res.stdout) < (22,):
        return Outcome(missing, f"Node.js {res.stdout.strip()}: 22 or newer {why}", "", fix)
    return Outcome("pass", f"Node.js {res.stdout.strip()}")


async def make(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["make", "--version"], timeout=5)
    if res.ok:
        return Outcome("pass", res.stdout.splitlines()[0])
    fix = ["xcode-select --install"] if ctx.host.os == "macos" else _pkg(ctx.host, "make")
    return Outcome("fail", "make not found", "Install make.", fix)


# --- Docker ---------------------------------------------------------------------------------


async def _docker_info(ctx: CheckContext) -> tuple[dict | None, str]:
    res = await ctx.run(["docker", "info", "--format", "{{json .}}"], timeout=8)
    if not res.ok:
        return None, (res.stderr or res.stdout).strip()
    try:
        return json.loads(res.stdout), ""
    except json.JSONDecodeError:
        return None, "docker info returned no JSON"


async def docker_installed(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["docker", "--version"], timeout=5)
    if res.ok:
        return Outcome("pass", res.stdout.strip())
    if ctx.host.os == "macos":
        return Outcome(
            "fail",
            "docker not found",
            "Install Docker Desktop (or OrbStack) for Apple Silicon, then start it.",
            [DOCKER_DOCS["macos"], "brew install --cask docker   # or: brew install orbstack"],
            fix_step="docker.install",
        )
    return Outcome(
        "fail",
        "docker not found",
        "Install Docker Engine (needs sudo; see the guide).",
        [DOCKER_DOCS["linux"]],
        fix_step="docker.install",
    )


async def docker_daemon(ctx: CheckContext) -> Outcome:
    info, err = await _docker_info(ctx)
    if info is not None:
        return Outcome("pass", f"server {info.get('ServerVersion', '?')}")
    if "permission denied" in err.lower():
        return Outcome("fail", "the daemon runs, but this user may not use it (see next check)")
    start = "Start Docker Desktop (or OrbStack)." if ctx.host.os == "macos" else "Start the daemon."
    cmds = [] if ctx.host.os == "macos" else ["sudo systemctl enable --now docker"]
    return Outcome("fail", f"daemon not reachable: {err[:200]}", start, cmds)


async def docker_no_sudo(ctx: CheckContext) -> Outcome:
    info, err = await _docker_info(ctx)
    if info is not None:
        return Outcome("pass", "docker works without sudo")
    if "permission denied" in err.lower():
        return Outcome(
            "fail",
            "permission denied on the Docker socket",
            "Add yourself to the docker group (needs sudo), then log out and back in.",
            ["sudo usermod -aG docker $USER"],
            fix_step="groups.add",
        )
    return Outcome("unknown", "daemon not reachable, so this cannot be checked")


async def compose_v2(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["docker", "compose", "version", "--short"], timeout=5)
    if res.ok and _version(res.stdout) >= (2,):
        return Outcome("pass", f"Compose {res.stdout.strip()}")
    fix = [] if ctx.host.os == "macos" else ["sudo apt install docker-compose-plugin"]
    return Outcome("fail", "docker compose (v2) not found", "Install the Compose v2 plugin.", fix)


async def buildx(ctx: CheckContext) -> Outcome:
    res = await ctx.run(["docker", "buildx", "version"], timeout=5)
    if res.ok:
        return Outcome("pass", res.stdout.strip())
    fix = [] if ctx.host.os == "macos" else ["sudo apt install docker-buildx-plugin"]
    return Outcome("warn", "buildx not found (only make image-multiarch needs it)", "", fix)


async def desktop_vm(ctx: CheckContext) -> Outcome:
    info, err = await _docker_info(ctx)
    if info is None:
        return Outcome("unknown", "daemon not reachable")
    name = info.get("OperatingSystem", "")
    product = "OrbStack" if "orbstack" in name.lower() else "Docker Desktop"
    if "docker desktop" not in name.lower() and product != "OrbStack":
        return Outcome("warn", f"unexpected Docker host: {name}")
    gb = int(info.get("MemTotal", 0)) / GB
    if gb >= 8:
        return Outcome("pass", f"{product}, {gb:.0f} GB for containers")
    return Outcome(
        "warn",
        f"{product}, {gb:.1f} GB for containers: give it at least 8 GB",
        "Docker Desktop: Settings → Resources → Memory.",
    )


def _pkg(host: Host, name: str) -> list[str]:
    return [f"sudo apt install {name}"] if host.os == "linux" else [f"brew install {name}"]


PRE_CHECKS = (
    Check("ctx.host.os", "Operating system supported", "Host", os_supported),
    Check("ctx.host.arch", "CPU architecture", "Host", cpu_arch, ("uname -m",)),
    Check("host.ram", "Memory ≥ 16 GB", "Host", ram, ("sysctl hw.memsize  # macOS", "free -g")),
    Check("host.disk", "Free disk space", "Host", disk, ("df -h .",)),
    Check("host.git", "git", "Host", git, ("git --version",)),
    Check("host.repo", "Repository state", "Host", repo_clean, ("git status --short",)),
    Check("prereq.python", "Python ≥ 3.12", "Prerequisites", python, ("uv run python -V",)),
    Check("prereq.uv", "uv", "Prerequisites", uv, ("uv --version",)),
    Check("prereq.node", "Node.js ≥ 22", "Prerequisites", node, ("node --version",)),
    Check("prereq.make", "make", "Prerequisites", make, ("make --version",)),
    Check(
        "docker.installed", "Docker installed", "Docker", docker_installed, ("docker --version",)
    ),
    Check("docker.daemon", "Docker daemon reachable", "Docker", docker_daemon, ("docker info",)),
    Check("docker.no_sudo", "Docker without sudo", "Docker", docker_no_sudo, ("docker ps",)),
    Check(
        "docker.compose",
        "Compose v2",
        "Docker",
        compose_v2,
        ("docker compose version",),
    ),
    Check("docker.buildx", "buildx", "Docker", buildx, ("docker buildx version",)),
    Check(
        "docker.desktop",
        "Docker Desktop / OrbStack memory ≥ 8 GB",
        "Docker",
        desktop_vm,
        ("docker info --format '{{.OperatingSystem}} {{.MemTotal}}'",),
        platforms=("macos",),
    ),
)
