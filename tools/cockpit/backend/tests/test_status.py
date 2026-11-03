import json

import pytest

from core import fake
from core.agent import AgentError
from core.fake import FakeRunner
from core.net import FakeNet
from core.platform import fake_host, os_release
from environment import modes, state, status

from .conftest import StubAgent, fail, ok, ps_line

pytestmark = pytest.mark.django_db


def runner_with(ps: list[str], env: list[list[str]] | None = None) -> FakeRunner:
    r = FakeRunner()
    r.on(("docker", "ps"), ok("\n".join(ps) + "\n"))
    r.on(("docker", "inspect"), ok("".join(json.dumps(e) + "\n" for e in env or [])))
    return r


@pytest.mark.parametrize(
    ("service", "ar3_mode", "chip", "rviz", "command"),
    [
        ("mac", "mock", "SIM · MOCK", False, "make up-mac"),
        ("mac", "sim", "SIM · GAZEBO", False, "make sim-mac"),
        ("vnc", "mock", "SIM · MOCK", True, "make vnc"),
        ("vnc", "sim", "SIM · GAZEBO", True, "AR3_MODE=sim make vnc"),
    ],
)
def test_mac_matrix_matches_spec_3_2(service, ar3_mode, chip, rviz, command):
    mode = modes.identify(service, ar3_mode)
    assert (mode.chip, mode.rviz, mode.command) == (chip, rviz, command)


@pytest.mark.parametrize(
    ("service", "ar3_mode", "headless", "command", "viewers"),
    [
        ("linux", "mock", False, "make up-linux", "RViz window"),
        ("linux", "sim", False, "make sim-linux", "Gazebo and RViz windows"),
        ("linux", "sim", True, "make sim-linux HEADLESS=1", "Lichtblick"),
        ("linux-gpu", "sim", False, "make sim-linux GPU=1", "Gazebo and RViz windows"),
        ("linux-gpu", "mock", True, "make up-linux HEADLESS=1 GPU=1", "Lichtblick"),
    ],
)
def test_linux_rows(service, ar3_mode, headless, command, viewers):
    mode = modes.identify(service, ar3_mode, headless)
    assert (mode.command, mode.viewers) == (command, viewers)
    assert mode.platforms == ("linux",)


def test_every_make_target_in_the_modes_exists():
    import re

    from config.envfiles import REPO

    targets = set(re.findall(r"^([a-z-]+):", (REPO / "Makefile").read_text(), re.M))
    for mode in modes.ALL_MODES:
        m = re.search(r"make ([a-z-]+)", mode.command)
        if m:
            assert m.group(1) in targets, mode.id


async def test_headless_linux_stack_from_container_env():
    st = await state.read(runner_with([ps_line("linux")], [["AR3_MODE=sim", "AR3_HEADLESS=1"]]))
    assert st.stack[0].id == "linux_gazebo_headless"


def test_dev_container_and_unknown_services():
    assert modes.identify("dev", None).id == "dev_shell"
    assert modes.identify("dev", "real").id == "real"
    assert modes.identify("zenohd", None) is None


async def test_reads_the_stack_from_labels_and_env():
    r = runner_with(
        [ps_line("vnc"), ps_line("cockpit-db"), ps_line("zenohd")], [["AR3_MODE=sim", "A=b=c"]]
    )
    st = await state.read(r)
    assert st.docker
    (mode, container) = st.stack
    assert mode.id == "sim_gazebo_rviz" and container.name == "ar3-vnc-1"
    assert container.env["A"] == "b=c"
    assert [c.service for c in st.infra] == ["cockpit-db"]
    # only the stack container is inspected
    assert r.calls[1][-1] == "id-vnc"


async def test_exited_stack_containers_do_not_count():
    st = await state.read(runner_with([ps_line("mac", state="exited")]))
    assert st.stack is None


async def test_docker_down_is_an_error():
    r = FakeRunner().on(("docker",), fail("Cannot connect to the Docker daemon"))
    snap = await status.snapshot(r, StubAgent(), NET)
    assert snap["overall"] == "ERROR"
    assert "Cannot connect" in snap["reasons"][0]
    assert snap["docker"]["reachable"] is False


async def test_two_stacks_warn():
    r = runner_with([ps_line("mac"), ps_line("vnc")], [["AR3_MODE=mock"], ["AR3_MODE=mock"]])
    snap = await status.snapshot(r, StubAgent(), NET)
    assert snap["overall"] == "WARN"
    assert "More than one" in snap["reasons"][0]


NET = FakeNet(busy={8765})  # foxglove_bridge up, nothing else


async def test_no_stack_is_offline_and_running_stack_is_ok(settings):
    snap = await status.snapshot(runner_with([ps_line("cockpit-db")]), StubAgent(), NET)
    assert (snap["overall"], snap["mode"], snap["ros_running"]) == ("OFFLINE", None, False)
    snap = await status.snapshot(
        runner_with([ps_line("mac")], [["AR3_MODE=sim"]]), StubAgent(), NET
    )
    assert snap["overall"] == "OK", snap["reasons"]  # slow cameras do not count against it
    assert snap["mode"]["chip"] == "SIM · GAZEBO" and snap["mode"]["command"] == "make sim-mac"
    assert snap["ros_running"] and snap["arm"] is None and snap["ready"]
    assert snap["stop"]["available"] is True
    rows = {c["id"]: c["status"] for c in snap["components"]}
    assert rows["agent"] == rows["move_group"] == rows["foxglove_bridge"] == "ready"
    assert rows["cameras"] == "warn" and rows["novnc"] == "off"


async def test_unreachable_agent_warns_and_disables_the_stop(settings):
    down = StubAgent(error=AgentError(0, "Agent not reachable", "connection refused"))
    snap = await status.snapshot(runner_with([ps_line("mac")], [["AR3_MODE=sim"]]), down, NET)
    assert snap["overall"] == "WARN"
    assert snap["stop"]["available"] is False
    assert "agent" in snap["reasons"][0]
    rows = {c["id"]: c["status"] for c in snap["components"]}
    assert rows["agent"] == "waiting" and rows["move_group"] == "unknown"


async def test_wrong_agent_token_is_an_error(settings):
    refused = StubAgent(error=AgentError(401, "Wrong agent token"))
    snap = await status.snapshot(runner_with([ps_line("mac")], [["AR3_MODE=mock"]]), refused, NET)
    assert snap["overall"] == "ERROR"
    assert "token mismatch" in snap["reasons"][0]


async def test_restarting_stack_warns():
    snap = await status.snapshot(
        runner_with([ps_line("mac", "restarting")], [["AR3_MODE=sim"]]), StubAgent(), NET
    )
    assert snap["overall"] == "WARN"


async def test_fake_mode_reads_the_shared_state_file(settings):
    settings.FAKE = True
    settings.FAKE_PLATFORM = "ubuntu-real"
    fake.save_state(settings.DATA_DIR, {"stack": {"service": "dev", "ar3_mode": "real"}})
    snap = await status.snapshot(FakeRunner.from_state(settings.DATA_DIR))
    assert snap["fake"] is True
    assert snap["host"]["label"] == "Ubuntu 24.04 · x86_64"
    assert snap["mode"]["chip"] == "REAL · AR3"
    assert snap["arm"] == "NOT_HOMED"
    viewers = {v["id"]: v["available"] for v in snap["viewers"]}
    assert viewers == {"lichtblick_web": False, "lichtblick": True, "novnc": False, "native": False}


async def test_native_windows_viewer_says_how_to_get_them(settings):
    settings.FAKE = True
    settings.FAKE_PLATFORM = "ubuntu"
    fake.save_state(settings.DATA_DIR, {})
    snap = await status.snapshot(FakeRunner.from_state(settings.DATA_DIR))
    native = {v["id"]: v for v in snap["viewers"]}["native"]
    assert native["kind"] == "note" and native["available"] is False
    assert [c.split()[0:2] for c in native["commands"]] == [
        ["xhost", "+local:docker"],
        ["make", "up-linux"],
        ["make", "sim-linux"],
    ]
    settings.FAKE_PLATFORM = "macos"
    snap = await status.snapshot(FakeRunner.from_state(settings.DATA_DIR, arch="arm64"))
    native = {v["id"]: v for v in snap["viewers"]}["native"]
    assert native["commands"] == [] and "noVNC" in native["hint"]


async def test_fake_vnc_stack_offers_novnc(settings):
    settings.FAKE = True
    fake.save_state(settings.DATA_DIR, {"stack": {"service": "vnc", "ar3_mode": "mock"}})
    snap = await status.snapshot(FakeRunner.from_state(settings.DATA_DIR))
    assert snap["host"]["os"] == "macos"
    assert {v["id"]: v["available"] for v in snap["viewers"]}["novnc"] is True


async def test_fake_docker_down(settings):
    settings.FAKE = True
    fake.save_state(settings.DATA_DIR, {"docker": False})
    snap = await status.snapshot(FakeRunner.from_state(settings.DATA_DIR))
    assert snap["overall"] == "ERROR"


def test_fake_hosts_and_os_release(tmp_path):
    assert fake_host("macos").label == "macOS 15.5 · arm64"
    with pytest.raises(ValueError):
        fake_host("windows")
    rel = tmp_path / "os-release"
    rel.write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\n')
    assert os_release(rel) == {"NAME": "Ubuntu", "VERSION_ID": "24.04"}


@pytest.mark.django_db
def test_state_and_modes_api(client_in, settings):
    settings.FAKE = True
    snap = client_in.get("/api/env/state").json()
    assert snap["overall"] == "OFFLINE" and snap["host"]["os"] == "macos"
    ids = [m["id"] for m in client_in.get("/api/env/modes").json()]
    assert ids[:4] == ["sim_mock", "sim_gazebo", "sim_mock_rviz", "sim_gazebo_rviz"]
    assert "linux_gazebo" in ids
    assert client_in.get("/api/platform").json()["label"] == "macOS 15.5 · arm64"
