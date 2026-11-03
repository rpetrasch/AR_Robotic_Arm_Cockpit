"""The agent's HTTP API with a fake node: token, endpoints, problem JSON."""

import pytest
from fastapi.testclient import TestClient

from ar3_cockpit_agent.app import create_app

TOKEN = "unit-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeAgent:
    mode = "sim"

    def __init__(self):
        self.stops = 0

    def health(self):
        return {"ok": True, "mode": self.mode, "ready": True, "components": {}}

    def motion(self):
        return {"allowed": True, "reason": "simulation"}

    def controllers(self):
        return [{"name": "joint_trajectory_controller", "state": "active"}]

    def graph(self):
        return {"nodes": ["/move_group"], "topics": [], "services": [], "actions": []}

    def joints(self):
        return None

    def stop(self):
        self.stops += 1
        return {"cancelled": [{"action": "/move_action", "goals": 1}], "held": [], "errors": []}


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture
def client(agent):
    return TestClient(create_app(agent, TOKEN))


def test_every_endpoint_needs_the_token(client):
    for path in ("/health", "/capabilities", "/controllers", "/graph", "/joints"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/stop").status_code == 401


def test_without_a_configured_token_nothing_is_served(agent):
    response = TestClient(create_app(agent, "")).get("/health", headers=AUTH)
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "COCKPIT_AGENT_TOKEN" in response.json()["detail"]


def test_health_and_capabilities(client):
    assert client.get("/health", headers=AUTH).json()["ready"] is True
    caps = client.get("/capabilities", headers=AUTH).json()
    assert caps["mode"] == "sim"
    assert caps["motion"]["allowed"] is True
    assert caps["stop"] is True
    assert caps["arm"]["available"] is False


def test_stop_calls_the_node(client, agent):
    body = client.post("/stop", headers=AUTH).json()
    assert agent.stops == 1
    assert body["cancelled"][0]["action"] == "/move_action"


def test_missing_joint_states_is_a_problem(client):
    response = client.get("/joints", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["title"] == "No /joint_states received yet"


def test_arm_endpoints_wait_for_phase_1(client):
    for path in ("/arm/home", "/arm/reset_estop"):
        response = client.post(path, headers=AUTH)
        assert response.status_code == 501
        assert "Phase 1" in response.json()["detail"]
    assert client.get("/arm/state", headers=AUTH).status_code == 501
