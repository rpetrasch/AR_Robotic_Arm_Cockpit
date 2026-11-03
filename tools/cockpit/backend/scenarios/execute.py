"""Scenario runs from the cockpit's side (docs/specs/spec_cockpit.md §12.3, §12.4).

A run is a job of the step ``scenario.run`` (mutating: it moves the arm), so it has the job
log, the one-changing-job lock, Cancel (= Abort) and the CLI equivalent
``make scenario-run ID=<id>``. Per repeat:

1. validate the scenario, check the mode against the running stack and the agent's
   capabilities (REAL: ARMED and COCKPIT_REAL_SCENARIOS=1);
2. resolve it (randomize with the seed), create the ``Run`` (telemetry), its artefacts
   directory ``COCKPIT_DATA_DIR/runs/<id>/`` and switch REC on (§12a.3: always recorded);
3. hand it to the agent (``POST /run``) and follow ``/ws/run/<id>``: every event goes to the
   job log, to the run's ``events.jsonl`` and to the Channels group ``run.<id>``
   (``/ws/runs/<id>`` in the browser); job Cancel aborts the agent's run;
4. evaluate the success criteria (scenarios.evaluate), store result, steps, metrics,
   write ``run.json``, ``metrics.json``; a recorded rosbag becomes a Recording.

Pause between steps and Abort also come from the API (views), straight to the agent.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.agent import AgentError, get_agent

from . import evaluate, resolve, store, validate

CURRENT_RUN_KEY = "cockpit:run:current"  # run_ingest tags the samples with it
ROBOT_OF = {"sim_mock": "mock", "sim_gazebo": "gazebo", "real": "real"}


class RunError(Exception):
    pass


def runs_dir() -> Path:
    return Path(settings.DATA_DIR) / "runs"


def group(run_id) -> str:
    return f"run.{run_id}"


def mode_of_stack(st) -> str | None:
    """The scenario mode the running stack can run: sim_mock, sim_gazebo, real."""
    current = st.stack if st.docker else None
    if current is None or current[0] is None or current[1].state != "running":
        return None
    return {v: k for k, v in ROBOT_OF.items()}.get(current[0].robot)


async def check_runnable(data: dict, mode: str | None, agent=None) -> tuple[str, dict]:
    """(mode, capabilities), or RunError with what is missing."""
    import os

    from core.runner import get_runner
    from environment import state as stack_state

    st = await stack_state.read(get_runner())
    running = mode_of_stack(st)
    if running is None:
        raise RunError("no ROS stack is running: start one under Environment")
    if mode and mode != running:
        raise RunError(f"the running stack is {running}, not {mode}")
    mode = mode or running
    if mode not in data.get("modes", []):
        raise RunError(f"the scenario does not list {mode} in modes ({', '.join(data['modes'])})")
    if mode == "real" and os.environ.get("COCKPIT_REAL_SCENARIOS") != "1":
        raise RunError("scenarios on the real arm need COCKPIT_REAL_SCENARIOS=1 (§12.5)")
    agent = agent or get_agent()
    try:
        caps = await agent.capabilities()
    except AgentError as exc:
        raise RunError(f"the agent is not reachable ({exc.detail or exc.title})") from None
    if not caps.get("motion", {}).get("allowed"):
        raise RunError(f"the agent does not allow motion: {caps['motion'].get('reason', '')}")
    if data["task"]["type"] not in caps.get("task_types", []):
        raise RunError(f"the agent cannot run {data['task']['type']} tasks yet")
    return mode, caps


async def _broadcast(run_id, message: dict) -> None:
    layer = get_channel_layer()
    if layer is not None:
        await layer.group_send(group(run_id), {"type": "run.event", **message})


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str))


async def run_scenario(params: dict, log, cancel: asyncio.Event | None = None, user: str = "cli",
                       job_id=None, agent=None) -> int:  # fmt: skip
    """The step's action; returns the exit code (0: every repeat succeeded)."""
    from telemetry import events as tevents
    from telemetry import sessions
    from telemetry.models import Run

    sid = params["scenario"]
    try:
        text = await sync_to_async(store.read)(sid)
    except (FileNotFoundError, store.StoreError) as exc:
        await log("err", f"no scenario {sid!r} ({exc})")
        return 1
    checked = await sync_to_async(validate.validate)(text)
    for p in checked["warnings"]:
        await log("out", f"warning: {p['path']}: {p['message']}")
    if not checked["ok"]:
        for p in checked["errors"]:
            await log("err", f"line {p['line']}: {p['path']}: {p['message']}")
        return 1
    data = checked["data"]
    agent = agent or get_agent()
    try:
        mode, _caps = await check_runnable(data, params.get("mode") or None, agent)
    except RunError as exc:
        await log("err", str(exc))
        return 1
    from core.runner import get_runner
    from environment import state as stack_state

    session = await sessions.sync(await stack_state.read(get_runner()))
    if session is None:
        await log("err", "no session for the running stack")
        return 1
    if not session.recording:
        session.recording = True  # §12a.3: scenario runs are always recorded
        await session.asave(update_fields=["recording"])
        await tevents.record("rec", {"recording": True, "reason": "scenario run"}, session)
        await log("out", "REC switched on for the run")

    repeat = int(params.get("repeat") or data.get("repeat", 1))
    seed0 = int(params["seed"]) if params.get("seed") else None
    options = {"keep_scene": params.get("keep_scene") == "1",
               "record_bag": params.get("record") == "1"}  # fmt: skip
    failures = 0
    for k in range(1, repeat + 1):
        if cancel is not None and cancel.is_set():
            break
        seed = None if seed0 is None else seed0 + k - 1
        resolved, used_seed = resolve.resolve(data, seed)
        run = await Run.objects.acreate(
            session=session, scenario_id=sid, scenario_version=str(data.get("version", "")),
            resolved_yaml=yaml.safe_dump(resolved, sort_keys=False), seed=used_seed,
            started=timezone.now(), status="running", mode=mode, user=user, repeat_index=k,
            repeat_of=repeat, job_id=job_id, options=options,
        )  # fmt: skip
        await log("out", f"run {k}/{repeat}: {run.id} ({sid} v{data.get('version')}, {mode}"
                  f"{f', seed {used_seed}' if used_seed is not None else ''})")  # fmt: skip
        result = await _one(run, resolved, mode, options, log, cancel, agent)
        if result != "succeeded":
            failures += 1
        if result == "aborted":
            break
    return 0 if failures == 0 else 1


async def _one(run, resolved: dict, mode: str, options: dict, log, cancel, agent) -> str:
    from telemetry import events as tevents

    folder = runs_dir() / str(run.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "scenario.yaml").write_text(run.resolved_yaml)
    events_file = (folder / "events.jsonl").open("a", encoding="utf-8")
    log_file = (folder / "run.log").open("a", encoding="utf-8")
    await cache.aset(CURRENT_RUN_KEY, str(run.id), timeout=24 * 3600)
    await tevents.record("run_start", {"scenario": run.scenario_id, "mode": mode},
                         run.session_id, run)  # fmt: skip
    final: dict = {}
    aborting = False

    async def watch_cancel():
        nonlocal aborting
        while cancel is not None and not cancel.is_set():
            await asyncio.sleep(0.2)
        if cancel is not None:
            aborting = True
            await log("err", "abort: cancelling the goal, holding")
            try:
                await agent.run_abort(str(run.id))
            except AgentError:
                pass

    async def rlog(stream: str, text: str):
        log_file.write(f"{datetime.now(UTC).isoformat(timespec='milliseconds')} {text}\n")
        log_file.flush()
        await log(stream, text)

    watcher = asyncio.ensure_future(watch_cancel())
    try:
        await agent.run_start(str(run.id), resolved, options)
        async for msg in agent.run_events(str(run.id)):
            for ev in msg.get("events", []):
                events_file.write(json.dumps(ev, default=str) + "\n")
                kind = ev.get("kind")
                if kind == "log":
                    await rlog("out" if ev.get("level") == "INFO" else "err", ev["text"])
                elif kind == "step":
                    extra = (
                        (
                            f" ({ev['planning_s']:.2f} s planning, {ev['execution_s']:.2f} s "
                            "execution)"
                        )
                        if ev.get("status") == "succeeded" and "planning_s" in ev
                        else ""
                    )
                    err = f": {ev['error']}" if ev.get("error") else ""
                    await rlog(
                        "out" if ev["status"] != "failed" else "err",
                        f"step {ev['index'] + 1}: {ev['label']} {ev['status']}{extra}{err}",
                    )
                elif kind == "finished":
                    final = {k: v for k, v in ev.items() if k not in ("kind", "t")}
            events_file.flush()
            if msg.get("state", {}).get("steps"):
                run.steps = msg["state"]["steps"]
                await run.asave(update_fields=["steps"])
            # the browser gets the cockpit's run shape: while running, the agent's steps only
            # (the agent's own state has a different `result`); run_dict at the end
            await _broadcast(
                run.id,
                {
                    "events": msg.get("events", []),
                    "state": {"steps": run.steps, "status": "running"},
                },
            )
            if final:
                break
    except AgentError as exc:
        final = {"result": "failed", "error": f"agent: {exc.detail or exc.title}"}
        await rlog("err", final["error"])
    finally:
        watcher.cancel()
        events_file.close()
    if aborting and final.get("result") != "aborted":
        final["result"] = "aborted"
    result, criteria = evaluate.evaluate(resolved, final, mode)
    steps = final.get("steps") or run.steps
    metrics = {
        "duration_s": final.get("duration_s"),
        "planning_s": round(sum(s.get("planning_s") or 0 for s in steps), 3),
        "execution_s": round(sum(s.get("execution_s") or 0 for s in steps), 3),
        "steps": [
            {
                "index": s["index"],
                "label": s["label"],
                "status": s["status"],
                "planning_s": s.get("planning_s"),
                "execution_s": s.get("execution_s"),
                "duration_s": s.get("duration_s"),
            }
            for s in steps
        ],  # fmt: skip
        "object_poses": final.get("object_poses") or {},
    }
    run.status = run.result = result
    run.ended = timezone.now()
    run.failed_step = final.get("failed_step")
    run.error = final.get("error") or ""
    run.steps = steps
    run.evaluation = criteria
    run.metrics = metrics
    await run.asave()
    for c in criteria:
        await rlog("out" if c["status"] != "fail" else "err",
                   f"criterion {c['criterion']}: {c['status']} ({c['detail']})")  # fmt: skip
    await rlog("out" if result == "succeeded" else "err", f"run {run.id}: {result}")
    log_file.close()
    _write_json(folder / "metrics.json", metrics)
    _write_json(folder / "run.json", run_dict(run))
    if final.get("bag"):
        from telemetry.models import Recording

        await Recording.objects.acreate(
            session_id=run.session_id,
            run=run,
            kind="mcap",
            path=final["bag"],
            start=run.started,
            end=run.ended,
        )
    await cache.adelete(CURRENT_RUN_KEY)
    await tevents.record("run_end", {"scenario": run.scenario_id, "result": result},
                         run.session_id, run)  # fmt: skip
    await _broadcast(
        run.id,
        {
            "events": [{"kind": "result", "result": result, "evaluation": criteria}],
            "state": run_dict(run),
        },
    )
    return result


def run_dict(r) -> dict:
    return {
        "id": str(r.id),
        "scenario": r.scenario_id,
        "version": r.scenario_version,
        "session": str(r.session_id),
        "mode": r.mode,
        "user": r.user,
        "status": r.status,
        "result": r.result,
        "seed": r.seed,
        "repeat": [r.repeat_index, r.repeat_of],
        "started": r.started.isoformat() if r.started else None,
        "ended": r.ended.isoformat() if r.ended else None,
        "duration_s": (r.ended - r.started).total_seconds() if r.ended and r.started else None,
        "failed_step": r.failed_step,
        "error": r.error,
        "steps": r.steps,
        "evaluation": r.evaluation,
        "metrics": r.metrics,
        "options": r.options,
        "job_id": str(r.job_id) if r.job_id else None,
    }


def new_id() -> str:
    return str(uuid.uuid4())
