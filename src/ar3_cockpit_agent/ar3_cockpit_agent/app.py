"""HTTP and WebSocket API of the cockpit agent (docs/specs/spec_cockpit.md §11.2, §16).

Every request needs ``Authorization: Bearer <COCKPIT_AGENT_TOKEN>``; without a configured
token the agent answers nothing but 503. A WebSocket without the token is closed with 1008
before it is accepted. ``agent`` is the AgentNode, or a fake in the tests. Errors are
RFC 9457 problem JSON like the backend's.

WebSockets:

- ``/ws/telemetry?rate=<Hz>``: joint samples at the rate (a divisor of 100; the cockpit's
  UI uses 10, run_ingest TELEMETRY_RATE_HZ), every 100 ms a message
  ``{"type": "telemetry", "names", "samples": [{"t", "p", "v", "e", "cp"?, "cv"?, "s"?}],
  "events", "dropped", "skipped"}``; once a second also ``rates`` and ``rtf``;
- ``/ws/rosout?after=<seq>``: /rosout lines ``{"type": "rosout", "seq", "lines"}``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import time

from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from . import scenario, telemetry

VERSION = "0.1.0"
PHASE1 = (
    "Needs ar3_safety and the hardware interface (SPEC Phase 1); "
    "the arm state and arming exist only in the cockpit's fake mode until then."
)


class Problem(Exception):
    def __init__(self, status: int, title: str, detail: str = ""):
        super().__init__(title)
        self.status, self.title, self.detail = status, title, detail


def create_app(agent, token: str) -> FastAPI:
    app = FastAPI(title="ar3_cockpit_agent", version=VERSION, docs_url=None, redoc_url=None)

    @app.exception_handler(Problem)
    async def problem(_request: Request, exc: Problem):
        body = {"type": "about:blank", "title": exc.title, "status": exc.status}
        if exc.detail:
            body["detail"] = exc.detail
        return JSONResponse(body, status_code=exc.status, media_type="application/problem+json")

    def authorized(request: Request) -> None:
        if not token:
            raise Problem(503, "No agent token", "COCKPIT_AGENT_TOKEN is not set in the stack.")
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            raise Problem(401, "Wrong agent token")

    auth = [Depends(authorized)]

    def ws_authorized(ws: WebSocket) -> bool:
        given = ws.headers.get("authorization", "")
        return bool(token) and hmac.compare_digest(given.encode(), f"Bearer {token}".encode())

    @app.get("/health", dependencies=auth)
    def get_health():
        return agent.health()

    @app.get("/capabilities", dependencies=auth)
    def capabilities():
        return {
            "version": VERSION,
            "mode": agent.mode,
            "motion": agent.motion(),
            "stop": True,
            "task_types": list(scenario.TASK_TYPES),
            "step_types": list(scenario.STEP_TYPES),
            "perception": [],  # ground_truth / camera need ar3_interfaces (SPEC Phase 2)
            "gazebo": agent.mode == "sim",
            "arm": {"available": False, "reason": PHASE1},
        }

    @app.get("/controllers", dependencies=auth)
    def controllers():
        listed = agent.controllers()
        if listed is None:
            raise Problem(503, "controller_manager not reachable")
        return listed

    @app.get("/graph", dependencies=auth)
    def graph():
        return agent.graph()

    @app.get("/graph/detail", dependencies=auth)
    def graph_detail():
        return agent.graph_detail()

    @app.get("/node/params", dependencies=auth)
    def node_params(name: str = Query(..., description="node name, e.g. /move_group")):
        try:
            return {"node": "/" + name.strip("/"), "params": agent.node_params(name)}
        except KeyError:
            raise Problem(404, "No such node", name) from None
        except TimeoutError as exc:
            raise Problem(504, "Parameters not available", str(exc)) from None

    @app.get("/topic/rate", dependencies=auth)
    def topic_rate(name: str = Query(...), seconds: float = Query(2.0, ge=0.5, le=10.0)):
        try:
            return agent.measure_rate(name, seconds)
        except KeyError:
            raise Problem(404, "No such topic", name) from None

    @app.get("/topics/rates", dependencies=auth)
    def topic_rates():
        return agent.topic_rates()

    @app.get("/cameras", dependencies=auth)
    def cameras():
        return agent.cameras()

    @app.get("/cameras/{camera}/thumb.jpg", dependencies=auth)
    def thumbnail(camera: str):
        try:
            data = agent.thumbnail(camera)
        except KeyError:
            raise Problem(404, "No such camera", camera) from None
        except ValueError as exc:
            raise Problem(415, "Image not convertible", str(exc)) from None
        if data is None:
            raise Problem(503, "No image yet", f"/{camera}/image_raw has not published (yet)")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/telemetry")
    async def ws_telemetry(ws: WebSocket, rate: int = 10):
        if not ws_authorized(ws):
            await ws.close(code=1008)
            return
        try:
            client = telemetry.Client(rate)
        except ValueError:
            await ws.close(code=1003)
            return
        await ws.accept()
        agent.sampler.add(client)
        seq = agent.events.seq
        last_rates = 0.0
        closed = asyncio.ensure_future(_until_closed(ws))
        try:
            while not closed.done():
                await asyncio.sleep(telemetry.BATCH_S)
                seq, events = agent.events.after(seq)
                msg = {
                    "type": "telemetry",
                    "rate": rate,
                    "names": agent.joint_names(),
                    "samples": client.take(),
                    "events": events,
                    "dropped": client.dropped,
                    "skipped": agent.sampler.skipped,
                }
                if time.monotonic() - last_rates >= 1.0:
                    last_rates = time.monotonic()
                    msg["rates"] = agent.topic_rates()
                    msg["rtf"] = agent.rtf()
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            closed.cancel()
            agent.sampler.remove(client)

    @app.websocket("/ws/rosout")
    async def ws_rosout(ws: WebSocket, after: int = 0):
        if not ws_authorized(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        seq = after
        closed = asyncio.ensure_future(_until_closed(ws))
        try:
            while not closed.done():
                seq, lines = agent.rosout.after(seq)
                if lines:
                    await ws.send_json({"type": "rosout", "seq": seq, "lines": lines})
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            closed.cancel()

    # --- scenarios (§12.4) -----------------------------------------------------------------

    def _runs():
        r = getattr(agent, "runs", None)
        if r is None:
            raise Problem(501, "Scenario runs not available")
        return r

    @app.post("/ik/check", dependencies=auth)
    def ik_check(body: dict):
        try:
            return {"targets": _runs().check_ik(body.get("targets", []))}
        except scenario.StepFailed as exc:
            raise Problem(503, "IK not available", str(exc)) from None

    @app.post("/scene/apply", dependencies=auth)
    def scene_apply(body: dict):
        try:
            return _runs().apply_scene(
                body.get("scenario", {}), spawn=bool(body.get("spawn", True))
            )
        except (scenario.StepFailed, ValueError) as exc:
            raise Problem(422, "Scene not applied", str(exc)) from None

    @app.post("/scene/clear", dependencies=auth)
    def scene_clear():
        try:
            return _runs().clear_scene()
        except scenario.StepFailed as exc:
            raise Problem(503, "Scene not cleared", str(exc)) from None

    @app.post("/run", dependencies=auth)
    def run_start(body: dict):
        if not body.get("run_id") or not isinstance(body.get("scenario"), dict):
            raise Problem(400, "run_id and scenario are required")
        try:
            return _runs().start(str(body["run_id"]), body["scenario"], body.get("options", {}))
        except RuntimeError as exc:
            raise Problem(409, "A run is active", str(exc)) from None

    def _run(run_id: str):
        run = _runs().runs.get(run_id)
        if run is None:
            raise Problem(404, "No such run", run_id)
        return run

    @app.get("/run/{run_id}", dependencies=auth)
    def run_state(run_id: str):
        return _run(run_id).state()

    @app.post("/run/{run_id}/abort", dependencies=auth)
    def run_abort(run_id: str):
        run = _run(run_id)
        run.abort()
        return run.state()

    @app.post("/run/{run_id}/pause", dependencies=auth)
    def run_pause(run_id: str, body: dict):
        run = _run(run_id)
        run.pause(bool(body.get("paused", True)))
        return run.state()

    @app.websocket("/ws/run/{run_id}")
    async def ws_run(ws: WebSocket, run_id: str, after: int = 0):
        if not ws_authorized(ws):
            await ws.close(code=1008)
            return
        run = getattr(getattr(agent, "runs", None), "runs", {}).get(run_id)
        if run is None:
            await ws.close(code=4404)
            return
        await ws.accept()
        seq = after
        closed = asyncio.ensure_future(_until_closed(ws))
        try:
            while not closed.done():
                seq, events = run.events.after(seq)
                if events:
                    await ws.send_json({"type": "run", "seq": seq, "events": events,
                                        "state": run.state()})  # fmt: skip
                if not run.active and not events:
                    await ws.send_json({"type": "end", "state": run.state()})
                    break
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            closed.cancel()
        with contextlib.suppress(Exception):
            await ws.close()

    @app.get("/joints", dependencies=auth)
    def joints():
        state = agent.joints()
        if state is None:
            raise Problem(503, "No /joint_states received yet")
        return state

    @app.post("/stop", dependencies=auth)
    def stop():
        return agent.stop()

    @app.get("/arm/state", dependencies=auth)
    def arm_state():
        raise Problem(501, "Arm state not available", PHASE1)

    @app.post("/arm/home", dependencies=auth)
    def arm_home():
        raise Problem(501, "Homing not available", PHASE1)

    @app.post("/arm/reset_estop", dependencies=auth)
    def arm_reset_estop():
        raise Problem(501, "E-stop reset not available", PHASE1)

    return app


async def _until_closed(ws: WebSocket) -> None:
    """Returns when the client goes away (the streams never read otherwise)."""
    try:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    except (WebSocketDisconnect, RuntimeError):
        pass
