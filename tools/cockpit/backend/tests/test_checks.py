import asyncio
import json
import os
import subprocess
import time

import pytest

from ar3ctl import cli
from config.envfiles import REPO
from core import fake
from core.fake import FakeRunner
from core.net import FakeNet
from core.platform import fake_host
from core.repo import dockerfile_hash
from setup import steps
from setup.checks import ALL_CHECKS, base, make_context, pre, run_checks, stack

from .conftest import fail, ok

MAC, UBUNTU = fake_host("macos"), fake_host("ubuntu")


def by_id(results):
    return {r.id: r for r in results}


def ctx_for(runner=None, host=MAC, repo=REPO, net=None, env=None):
    return base.CheckContext(runner or FakeRunner(), host, repo, net or FakeNet(), env or {}, 120.0)


async def fresh(tmp_path, platform="macos", **state):
    """The fake world: a fresh clone on a healthy host (core/fake.py), plus ``state``."""
    if state:
        fake.save_state(tmp_path, state)
    ctx = await make_context(fake=True, data_dir=tmp_path, fake_platform=platform, env={})
    return ctx, by_id(await run_checks(ctx))


# --- pre-Docker checks ----------------------------------------------------------------------


def healthy_runner(info: dict | None = None) -> FakeRunner:
    info = info or {"ServerVersion": "29.0", "OperatingSystem": "Docker Desktop", "MemTotal": 16e9}
    return (
        FakeRunner()
        .on(("docker", "info"), ok(json.dumps(info)))
        .on(("docker", "compose"), ok("2.39.1"))
        .on(("docker", "buildx"), ok("buildx v0.25"))
        .on(("docker", "--version"), ok("Docker version 29.0"))
        .on(("git", "-C"), ok(""))
        .on(("git",), ok("git version 2.50"))
        .on(("uv",), ok("uv 0.12"))
        .on(("node",), ok("v22.11.0"))
        .on(("make",), ok("GNU Make 4.3"))
        .on(("sysctl",), ok(str(64 * 1024**3)))
    )


async def test_all_pre_checks_pass_on_a_healthy_mac():
    results = by_id(await base.run_all(pre.PRE_CHECKS, ctx_for(healthy_runner())))
    bad = {k: (r.status, r.detail) for k, r in results.items() if r.status != "pass"}
    assert not bad
    assert results["docker.desktop"].detail == "Docker Desktop, 15 GB for containers"


async def test_docker_permission_denied_points_at_the_docker_group():
    r = healthy_runner().on(("docker", "info"), fail("permission denied while trying to connect"))
    r.handlers.insert(0, r.handlers.pop())  # the failing docker info wins
    results = by_id(await base.run_all(pre.PRE_CHECKS, ctx_for(r, UBUNTU)))
    assert results["docker.no_sudo"].status == "fail"
    assert results["docker.no_sudo"].fix_commands == ["sudo usermod -aG docker $USER"]
    assert results["docker.no_sudo"].fix_step == "groups.add"
    assert results["docker.daemon"].status == "fail"


async def test_daemon_down_on_mac_says_start_docker_desktop():
    r = FakeRunner().on(("docker", "info"), fail("Cannot connect to the Docker daemon"))
    out = await pre.docker_daemon(ctx_for(r))
    assert out.status == "fail" and "Docker Desktop" in out.fix


async def test_missing_docker_on_linux_gives_the_install_guide_not_a_sudo_run():
    out = await pre.docker_installed(ctx_for(host=UBUNTU))
    assert out.status == "fail" and out.fix_step == "docker.install"
    assert out.fix_commands == [pre.DOCKER_DOCS["linux"]]


async def test_node_missing_is_fatal_only_without_a_built_frontend(tmp_path):
    assert (await pre.node(ctx_for(repo=tmp_path))).status == "fail"
    (tmp_path / "tools/cockpit/frontend/out").mkdir(parents=True)
    (tmp_path / "tools/cockpit/frontend/out/index.html").write_text("x")
    assert (await pre.node(ctx_for(repo=tmp_path))).status == "warn"
    old = FakeRunner().on(("node",), ok("v20.1.0"))
    assert (await pre.node(ctx_for(old, repo=tmp_path))).status == "warn"


async def test_linux_ram_from_free():
    r = FakeRunner().on(("free",), ok("        total\nMem:  8589934592  1 2\n"))
    out = await pre.ram(ctx_for(r, UBUNTU))
    assert out.status == "warn" and out.detail.startswith("8 GB")


async def test_unsupported_os_versions_warn():
    old_mac = MAC.__class__("macos", "macOS", "13.6", "arm64", "m")
    assert (await pre.os_supported(ctx_for(host=old_mac))).status == "warn"
    jammy = UBUNTU.__class__("linux", "Ubuntu", "22.04", "x86_64", "w")
    assert (await pre.os_supported(ctx_for(host=jammy))).status == "warn"


async def test_slow_check_is_unknown_and_platform_check_skips(monkeypatch):
    monkeypatch.setattr(base, "TIMEOUT_S", 0.05)

    async def slow(ctx):
        await asyncio.sleep(1)

    check = base.Check("x.slow", "Slow", "Host", slow)
    assert (await check.run(ctx_for())).status == "unknown"
    mac_only = base.Check("x.mac", "Mac", "Docker", slow, platforms=("macos",))
    assert (await mac_only.run(ctx_for(host=UBUNTU))).status == "skip"


async def test_broken_probe_reports_unknown():
    async def boom(ctx):
        raise RuntimeError("bad")

    result = await base.Check("x.boom", "Boom", "Host", boom).run(ctx_for())
    assert (result.status, result.detail) == ("unknown", "check failed: bad")


async def test_shared_commands_run_once():
    r = healthy_runner()
    await base.run_all(pre.PRE_CHECKS, ctx_for(r))
    assert sum(c[:2] == ("docker", "info") for c in r.calls) == 1


# --- the whole checklist on the fake world --------------------------------------------------


async def test_fresh_clone_on_a_mac(tmp_path):
    _ctx, r = await fresh(tmp_path)
    status = {k: v.status for k, v in r.items()}
    assert [c.id for c in ALL_CHECKS] == list(r)
    assert all(status[c.id] == "pass" for c in pre.PRE_CHECKS)
    assert status["gpu.driver"] == status["hw.dialout"] == "skip"
    assert (status["repo.env"], r["repo.env"].fix_step) == ("warn", "env.create")
    assert status["repo.config"] == "pass"
    assert (status["images.dev"], r["images.dev"].fix_step) == ("fail", "images.build")
    assert (status["images.vnc"], r["images.vnc"].fix_step) == ("warn", "images.build_vnc")
    assert (status["ws.built"], r["ws.built"].fix_step) == ("fail", "ws.build")
    assert (status["tests.last"], status["sim.smoke"]) == ("warn", "warn")
    assert status["viewers.ports"] == "pass"
    assert status["net.zenohd"] == "skip"
    assert status["ai.ollama"] == "pass"
    assert r["ai.models"].fix_params == {"model": "llama3.1:8b"}


async def test_ubuntu_with_gpu_and_groups(tmp_path):
    _ctx, r = await fresh(tmp_path, "ubuntu")
    assert r["gpu.driver"].status == r["gpu.toolkit"].status == "pass"
    assert "RTX 4060" in r["gpu.driver"].detail
    assert r["hw.dialout"].status == "pass"
    assert r["hw.devices"].status == "skip"  # no invented device IDs before Phase 1
    _ctx, r = await fresh(tmp_path / "nogpu", "ubuntu", gpu=False)
    assert r["gpu.driver"].fix_step == "nvidia.driver"
    assert r["gpu.toolkit"].fix_step == "nvidia.toolkit"


async def test_busy_port_names_the_command_to_find_the_program(tmp_path):
    _ctx, r = await fresh(tmp_path, ports_busy=[8765])
    assert r["viewers.ports"].status == "fail"
    assert "8765 (foxglove_bridge)" in r["viewers.ports"].detail
    assert r["viewers.ports"].fix_commands == ["lsof -nP -iTCP:8765 -sTCP:LISTEN"]


async def test_port_held_by_our_container_is_fine():
    row = {
        "Names": "ar3-vnc-1",
        "Labels": "com.docker.compose.project=ar3,com.docker.compose.service=vnc",
        "Ports": "0.0.0.0:6080->6080/tcp, 0.0.0.0:8765->8765/tcp",
    }
    r = FakeRunner().on(("docker", "ps"), ok(json.dumps(row) + "\n"))
    out = await stack.viewer_ports(ctx_for(r, net=FakeNet(busy={8765, 6080})))
    assert out.status == "pass" and "8765 used by ar3-vnc-1" in out.detail


async def test_image_needs_rebuild_when_the_dockerfile_changed():
    info = {"Architecture": "arm64", "Config": {"Labels": {"ar3.dockerfile_hash": "0ld"}}}
    r = FakeRunner().on(("docker", "image"), ok(json.dumps(info)))
    out = await stack._image_check("ar3-dev", "images.build", True)(ctx_for(r))
    assert (out.status, out.fix_step) == ("warn", "images.build")
    assert "the Dockerfile changed" in out.detail
    info["Architecture"] = "amd64"
    r = FakeRunner().on(("docker", "image"), ok(json.dumps(info)))
    out = await stack._image_check("ar3-dev", "images.build", True)(ctx_for(r))
    assert out.status == "fail" and "x86_64" in out.detail


def test_makefile_and_python_compute_the_same_image_hash():
    out = subprocess.run(
        ["make", "-s", "dockerfile-hash"], cwd=REPO, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == dockerfile_hash(REPO)


async def test_site_config_problems_are_named(tmp_path):
    ctx, _ = await fresh(tmp_path)
    robot = ctx.repo / "config/robot.yaml"
    robot.write_text(robot.read_text().replace("velocity_scaling: 0.2", "velocity_scaling: 2"))
    (ctx.repo / "config/bins.yaml").write_text("bins: [")
    out = await stack.site_config(ctx)
    assert out.status == "fail"
    assert "motion.velocity_scaling must be in (0, 1]" in out.detail
    assert "bins.yaml: invalid YAML" in out.detail


async def test_workspace_stale_after_a_build_input_changed(tmp_path):
    ctx, _ = await fresh(tmp_path)
    (ctx.repo / "install").mkdir()
    (ctx.repo / "install/setup.bash").write_text("")
    assert (await stack.workspace(ctx)).status == "pass"
    later = time.time() + 10
    os.utime(ctx.repo / "src/ar3_bringup/package.xml", (later, later))
    out = await stack.workspace(ctx)
    assert out.status == "warn" and "package.xml" in out.detail


async def test_test_results_failed_stale_and_ok(tmp_path):
    ctx, _ = await fresh(tmp_path)
    results = ctx.repo / "build/ar3_bringup/test_results/ar3_bringup"
    results.mkdir(parents=True)
    xml = results / "a.xunit.xml"
    xml.write_text('<testsuites><testsuite tests="3" failures="1" errors="0"/></testsuites>')
    out = await stack.last_tests(ctx)
    assert out.status == "fail" and out.detail.startswith("3 test cases, 1 failed")
    xml.write_text('<testsuite tests="3" failures="0" errors="0"/>')
    os.utime(xml, (fake.HEAD_TS - 100, fake.HEAD_TS - 100))
    assert (await stack.last_tests(ctx)).status == "warn"  # older than the last commit
    os.utime(xml, None)
    assert (await stack.last_tests(ctx)).status == "pass"


async def test_smoke_result(tmp_path):
    ctx, _ = await fresh(tmp_path)
    path = ctx.repo / stack.SMOKE_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"rc": 2, "commit": fake.HEAD, "finished": "x"}))
    assert (await stack.sim_smoke(ctx)).status == "fail"
    path.write_text(json.dumps({"rc": 0, "commit": "other", "finished": "x"}))
    assert (await stack.sim_smoke(ctx)).status == "warn"
    path.write_text(json.dumps({"rc": 0, "commit": fake.HEAD, "finished": "x"}))
    assert (await stack.sim_smoke(ctx)).status == "pass"


async def test_zenoh_configured_but_unreachable(tmp_path):
    ctx, _ = await fresh(tmp_path)
    (ctx.repo / "docker/.env").write_text("ROS_RMW=rmw_zenoh_cpp\nZENOH_ROUTER=10.0.0.1\n")
    out = await stack.zenohd(ctx)
    assert out.status == "fail" and "10.0.0.1:7447" in out.detail
    assert out.cli == ["nc -vz 10.0.0.1 7447"]  # pasteable: the address, not a placeholder
    (ctx.repo / "docker/.env").write_text("ROS_RMW=rmw_fastrtps_cpp\nZENOH_ROUTER=10.0.0.1\n")
    out = await stack.zenohd(ctx)
    assert out.status == "skip" and "not needed" in out.detail
    assert out.cli == ["nc -vz 10.0.0.1 7447"]
    (ctx.repo / "docker/.env").write_text("ROS_RMW=rmw_zenoh_cpp\nZENOH_ROUTER=10.0.0.1\n")
    assert (await stack.docker_env(ctx)).status == "pass"
    (ctx.repo / "docker/.env").write_text("ROS_RMW=rmw_zenoh_cpp\n")
    assert (await stack.docker_env(ctx)).status == "fail"


def test_x11_allowed_reads_xhost():
    head = "access control enabled, only authorized clients can connect\n"
    assert stack.x11_allowed(head + "LOCAL:\n")
    assert stack.x11_allowed("access control disabled, clients can connect from any host\n")
    assert not stack.x11_allowed(head + "SI:localuser:roland\nSI:localuser:gdm\n")
    assert not stack.x11_allowed(head)


async def test_x11_access_check_and_fix(tmp_path):
    _ctx, r = await fresh(tmp_path, platform="ubuntu")
    assert r["viewers.x11"].status == "pass"
    _ctx, r = await fresh(tmp_path, platform="ubuntu", x11=False)
    out = r["viewers.x11"]
    assert out.status == "warn" and out.fix_step == "x11.allow"
    assert out.fix_commands == ["xhost +local:docker"] and out.cli == ["xhost"]
    step = steps.get("x11.allow")
    assert step.command() == "xhost +local:docker" and step.post_checks == ("viewers.x11",)


async def test_ollama_down_and_models(tmp_path):
    _ctx, r = await fresh(tmp_path, ollama={"up": False, "models": []})
    assert r["ai.ollama"].status == "warn" and r["ai.models"].status == "unknown"
    assert "brew install ollama" in r["ai.ollama"].fix_commands
    models = {"up": True, "models": ["llama3.1:8b", "bge-m3:latest"]}
    _ctx, r = await fresh(tmp_path / "2", ollama=models)
    assert r["ai.models"].status == "pass"


# --- ar3ctl ---------------------------------------------------------------------------------


def test_doctor_json_lists_every_check(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("COCKPIT_FAKE", "1")
    monkeypatch.setenv("COCKPIT_DATA_DIR", str(tmp_path))
    code = cli.main(["doctor", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in out["checks"]] == [c.id for c in ALL_CHECKS]
    assert code == 1  # the fresh clone has no images yet
    assert all(set(c) >= {"status", "detail", "cli", "docs", "fix_step"} for c in out["checks"])
    cli.main(["doctor", "--pre", "--json"])
    pre_out = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in pre_out["checks"]] == [c.id for c in pre.PRE_CHECKS]


def test_init_env_appends_only_missing_secrets(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("DJANGO_SECRET_KEY=keep-me")
    monkeypatch.setattr(cli.envfiles, "LOCAL", env)
    for key in cli.GENERATED_SECRETS:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["init-env"]) == 0
    values = cli.envfiles.parse(env)
    assert values["DJANGO_SECRET_KEY"] == "keep-me"
    assert len(values["COCKPIT_DB_PASSWORD"]) >= 24
    assert env.stat().st_mode & 0o777 == 0o600
    before = env.read_text()
    cli.main(["init-env"])
    assert env.read_text() == before


@pytest.mark.parametrize("value", ["plain", "with space", "q'uote"])
def test_env_export_is_shell_safe(monkeypatch, capsys, value):
    import shlex

    monkeypatch.setenv("X_TEST", value)
    cli.main(["env-export", "X_TEST"])
    line = capsys.readouterr().out.strip()
    assert shlex.split(line) == ["export", f"X_TEST={value}"]
