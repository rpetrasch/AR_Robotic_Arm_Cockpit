"""ar3ctl: the cockpit's checks and operations without the web UI (docs/specs/spec_cockpit.md
principle 5).

    ar3ctl doctor [--pre] [--json]   the Install & Setup checklist (--pre: host, prerequisites
                                     and Docker only); needs no database
    ar3ctl create-user [USERNAME]    create a cockpit user (password prompt)
    ar3ctl status [--json]           host and the running stack, as in the header
    ar3ctl start MODE [--lichtblick] [--switch]
                                     start a mode (ids: ar3ctl start --list) like the
                                     Environment page: the same make command, then wait
                                     until the stack is ready
    ar3ctl stop                      make down
    ar3ctl components [--markdown]   the component catalogue (config/cockpit/components.yaml)
    ar3ctl sessions [--json]         recorded sessions (§12a), newest first
    ar3ctl telemetry-export SESSION [--format parquet|csv] [--run ID] [--from T] [--to T]
                                     a session to data/exports/ (Parquet: pandas.read_parquet)
    ar3ctl telemetry-compress [--all]
                                     run the compression policies now (--all: every chunk)
    ar3ctl scenarios [--json]        the scenarios of config/scenarios/
    ar3ctl scenario-validate ID|FILE  validate a scenario (exit 1 with errors)
    ar3ctl scenario-run ID [--mode M] [--repeat N] [--seed S] [--keep-scene] [--bag]
                                     run a scenario in the running simulation (as the UI)
    ar3ctl scenarios-pack ID... [-o FILE]   pack-and-go: scenarios into one zip
    ar3ctl scenarios-unpack FILE [--replace | --copy]
                                     import a pack (default: show what it would do)
    ar3ctl init-env                  generate missing secrets into <repo>/.env (cockpit.sh)
    ar3ctl env-export KEY...         print `export KEY=value` lines for the shell (cockpit.sh)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shlex
import sys
from pathlib import Path

from config import envfiles

ICONS = {"pass": "✔", "warn": "!", "fail": "✘", "skip": "-", "unknown": "?", "running": "…"}
GENERATED_SECRETS = ("DJANGO_SECRET_KEY", "COCKPIT_DB_PASSWORD", "COCKPIT_DB_READER_PASSWORD")


def _django():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def doctor_results(pre: bool) -> tuple[dict, list[dict]]:
    """The same checks, context and JSON as GET /api/checks (setup.checks.run_checks)."""
    from setup.checks import make_context, run_checks
    from setup.checks.pre import PRE_CHECKS

    envfiles.load()

    async def go():
        ctx = await make_context(
            fake=envfiles.flag("COCKPIT_FAKE"),
            data_dir=Path(os.path.expanduser(os.environ.get("COCKPIT_DATA_DIR", "~/.ar3cockpit"))),
            fake_platform=os.environ.get("COCKPIT_FAKE_PLATFORM", "macos"),
        )
        ids = [c.id for c in PRE_CHECKS] if pre else None
        return ctx.host, await run_checks(ctx, ids)

    host, results = asyncio.run(go())
    return host.to_dict(), [r.to_dict() for r in results]


def cmd_doctor(args) -> int:
    from setup.steps import BY_ID as STEPS

    host, results = doctor_results(args.pre)
    failed = [r for r in results if r["status"] == "fail"]
    if args.json:
        print(json.dumps({"host": host, "checks": results}))
        return 1 if failed else 0
    group = None
    for r in results:
        if r["group"] != group:
            group = r["group"]
            print(f"\n{group}")
        print(f"  {ICONS[r['status']]} {r['title']:<40} {r['detail']}")
        if r["status"] in ("fail", "warn"):
            if r["fix"]:
                print(f"      → {r['fix']}")
            for c in r["fix_commands"]:
                print(f"        $ {c}")
            step = STEPS.get(r["fix_step"])
            if step and not step.needs_root and not r["fix_commands"]:
                print(f"        $ {step.command(r['fix_params'] or None)}")
    counted = [r for r in results if r["status"] != "skip"]
    n_pass = sum(r["status"] == "pass" for r in counted)
    print(f"\n{n_pass} / {len(counted)} passed, {len(failed)} failed")
    return 1 if failed else 0


def cmd_create_user(args) -> int:
    _django()
    from django.core.management import call_command

    call_command("create_admin", username=args.username)
    return 0


def cmd_status(args) -> int:
    _django()
    from environment import status

    snap = asyncio.run(status.snapshot())
    if args.json:
        print(json.dumps(snap, indent=2))
        return 0
    mode = snap["mode"]
    print(f"host    {snap['host']['label']}{'  (fake)' if snap['fake'] else ''}")
    print(f"status  {snap['overall']}  {'; '.join(snap['reasons'])}")
    if mode:
        print(f"mode    {mode['chip']}  ({mode['container']}, {mode['status']})")
        print(f"cli     {mode['command']}")
    else:
        print("mode    STOPPED")
    for v in snap["viewers"]:
        if v["available"]:
            print(f"viewer  {v['label']} on port {v['port']}")
    return 0


def _make(env: dict[str, str], argv: tuple[str, ...]) -> int:
    import subprocess

    from django.conf import settings

    from core import agent
    from setup.steps import shell_words

    print(f"$ {shell_words((env, argv))}", flush=True)
    full = {**os.environ, "COCKPIT_AGENT_TOKEN": agent.token(settings.DATA_DIR), **env}
    return subprocess.run(list(argv), cwd=settings.REPO_DIR, env=full).returncode


async def _print(stream: str, text: str) -> None:
    print(text, file=sys.stderr if stream == "err" else sys.stdout, flush=True)


def cmd_start(args) -> int:
    _django()
    from platform import system

    from environment import health, modes

    host_os = "macos" if system() == "Darwin" else system().lower()
    if args.list or not args.mode:
        for m in modes.ALL_MODES:
            if m.startable and host_os in m.platforms:
                print(f"{m.id:28} {m.chip:14} {m.command}")
        return 0 if args.list else 2
    try:
        mode = modes.startable(args.mode, host_os)
    except modes.ModeError as exc:
        print(f"ar3ctl: {exc}", file=sys.stderr)
        return 2
    if args.switch and (rc := _make({}, ("make", "down"))) != 0:
        return rc
    env, argv = mode.start(lichtblick=args.lichtblick)
    rc = _make(env, argv)
    if rc != 0:
        return rc
    return asyncio.run(health.wait_ready(mode.id, _print))


def cmd_stop(args) -> int:
    _django()
    return _make({}, ("make", "down"))


def cmd_components(args) -> int:
    _django()
    from environment import components

    items = components.catalogue()
    if args.markdown:
        sys.stdout.write(components.markdown(items))
        return 0
    for c in items:
        ports = ",".join(map(str, c["ports"])) or "-"
        print(f"{c['id']:16} {ports:12} {c['name']}")
    return 0


def cmd_sessions(args) -> int:
    _django()
    from telemetry.models import Session
    from telemetry.sessions import to_dict

    rows = [to_dict(s) for s in Session.objects.all()[: args.limit]]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for s in rows:
        rec = "REC" if s["recording"] else "   "
        print(f"{s['id']}  {s['started'][:19]}  {s['mode']:10} {rec} {s['samples']:>9} samples"
              f"{'' if s['ended'] else '  (open)'}")  # fmt: skip
    return 0


def _time(value: str | None):
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None


def cmd_telemetry_export(args) -> int:
    _django()
    from telemetry.export import ExportError, export

    try:
        export(args.session, args.format, args.run, _time(args.since), _time(args.until))
    except (ExportError, ValueError) as exc:
        print(f"ar3ctl: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_telemetry_compress(args) -> int:
    _django()
    from telemetry import policies

    for row in policies.compress(everything=args.all):
        if not row["chunks"] or row["table"] == "log_lines":  # not compressed: pruned, searched
            continue
        size = ""
        if row["bytes_before"]:
            size = f"  {row['bytes_before'] / 1e6:.1f} MB -> {row['bytes_after'] / 1e6:.1f} MB"
        print(
            f"{row['table']:16} {row['compressed_chunks']}/{row['chunks']} chunks compressed{size}"
        )
    return 0


def cmd_scenarios(args) -> int:
    _django()
    from scenarios import store

    rows = store.all_scenarios()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for d in rows:
        steps = len((d.get("task") or {}).get("steps") or [])
        modes = ",".join(d.get("modes", []))
        print(f"{d.get('id', '?'):32} v{d.get('version', '?'):<3} {modes:22} {steps} steps  "
              f"{d.get('name', '')}")  # fmt: skip
    return 0


def cmd_scenario_validate(args) -> int:
    _django()
    from scenarios import store, validate

    p = Path(args.scenario)
    text = (
        p.read_text() if p.suffix in (".yaml", ".yml") and p.exists() else store.read(args.scenario)
    )
    res = validate.validate(text)
    for prob in res["errors"] + res["warnings"]:
        print(f"{prob['severity']:7} line {prob['line']}: {prob['path']}: {prob['message']}")
    print("valid" if res["ok"] else f"{len(res['errors'])} error(s)")
    return 0 if res["ok"] else 1


def cmd_scenario_run(args) -> int:
    _django()
    from scenarios.execute import run_scenario

    params = {"scenario": args.scenario, "mode": args.mode or "", "repeat": str(args.repeat),
              "seed": "" if args.seed is None else str(args.seed),
              "keep_scene": "1" if args.keep_scene else "0",
              "record": "1" if args.bag else "0"}  # fmt: skip

    async def log(_stream, text):
        print(text, flush=True)

    from asgiref.sync import async_to_sync

    # async_to_sync, not asyncio.run: the database work stays on this thread's connection
    return async_to_sync(run_scenario)(params, log, user=os.environ.get("USER", "cli"))


def cmd_scenarios_pack(args) -> int:
    _django()
    from scenarios import pack

    name, data = pack.pack(args.ids)
    out = Path(args.output or name)
    out.write_bytes(data)
    print(f"{out} ({len(args.ids)} scenario(s), {len(data) / 1e3:.1f} kB)")
    return 0


def cmd_scenarios_unpack(args) -> int:
    _django()
    from scenarios import pack

    prev = pack.preview(Path(args.file).read_bytes())
    print(f"pack from {prev['source_host']} ({prev['created']}, commit {prev['git_commit'][:10]})")
    for w in prev["config_warnings"]:
        print(f"warning: {w}")
    decision = "replace" if args.replace else "copy" if args.copy else None
    for s in prev["scenarios"]:
        what = {"new": "new", "same": "unchanged", "differs": "differs from the one here"}[
            s["status"]
        ]
        print(f"  {s['id']} v{s['version']}: {what}{' (INVALID)' if s['errors'] else ''}")
    if decision is None:
        print("nothing imported: add --replace (overwrite) or --copy (keep both)")
        return 0
    decisions = {
        s["id"]: ("replace" if s["status"] == "new" else decision) for s in prev["scenarios"]
    }
    for d in pack.apply(prev["token"], decisions)["done"]:
        print(f"  {d['id']}: {d['action']}")
    return 0


def cmd_assistant_index(args) -> int:
    _django()
    from assistant.index import refresh

    value = refresh(full=args.full, log_line=print)
    return 0 if value.get("chunks") else 1


def cmd_init_env(args) -> int:
    """Append missing secrets to <repo>/.env. Never rewrites existing values."""
    path = envfiles.LOCAL
    have = envfiles.parse(path)
    lines = [
        f"{key}={secrets.token_urlsafe(48 if key == 'DJANGO_SECRET_KEY' else 24)}"
        for key in GENERATED_SECRETS
        if not have.get(key) and not os.environ.get(key)
    ]
    if not lines:
        return 0
    existing = path.read_text() if path.exists() else ""
    with path.open("a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        if "AR3 Cockpit secrets" not in existing:
            fh.write("# AR3 Cockpit secrets, generated by cockpit.sh (docs/manual/cockpit.md)\n")
        fh.write("\n".join(lines) + "\n")
    path.chmod(0o600)
    print(f"wrote {len(lines)} generated secret(s) to {path}")
    return 0


def cmd_env_export(args) -> int:
    envfiles.load()
    for key in args.keys:
        print(f"export {key}={shlex.quote(os.environ.get(key, ''))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ar3ctl", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor", help="run the checks")
    p.add_argument("--pre", action="store_true", help="only host, prerequisites and Docker")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("create-user", help="create a cockpit user")
    p.add_argument("username", nargs="?", default="admin")
    p.set_defaults(fn=cmd_create_user)
    p = sub.add_parser("status", help="host and running stack")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("start", help="start a mode (as the Environment page does)")
    p.add_argument("mode", nargs="?")
    p.add_argument("--list", action="store_true", help="the modes this host can start")
    p.add_argument("--lichtblick", action="store_true", help="also start Lichtblick web")
    p.add_argument("--switch", action="store_true", help="stop the running stack first")
    p.set_defaults(fn=cmd_start)
    p = sub.add_parser("stop", help="stop the running stack (make down)")
    p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("components", help="the component catalogue")
    p.add_argument("--markdown", action="store_true", help="as docs/manual/cockpit-components.md")
    p.set_defaults(fn=cmd_components)
    p = sub.add_parser("sessions", help="recorded sessions")
    p.add_argument("--json", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_sessions)
    p = sub.add_parser("telemetry-export", help="export a session to Parquet or CSV")
    p.add_argument("session")
    p.add_argument("--format", choices=("parquet", "csv"), default="parquet")
    p.add_argument("--run")
    p.add_argument("--from", dest="since", help="ISO 8601 time")
    p.add_argument("--to", dest="until", help="ISO 8601 time")
    p.set_defaults(fn=cmd_telemetry_export)
    p = sub.add_parser("telemetry-compress", help="run the compression policies now")
    p.add_argument("--all", action="store_true", help="every chunk, also today's")
    p.set_defaults(fn=cmd_telemetry_compress)
    p = sub.add_parser("scenarios", help="the scenarios")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_scenarios)
    p = sub.add_parser("scenario-validate", help="validate a scenario (id or .yaml file)")
    p.add_argument("scenario")
    p.set_defaults(fn=cmd_scenario_validate)
    p = sub.add_parser("scenario-run", help="run a scenario in the running simulation")
    p.add_argument("scenario")
    p.add_argument("--mode", choices=("sim_mock", "sim_gazebo", "real"))
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--seed", type=int)
    p.add_argument("--keep-scene", action="store_true")
    p.add_argument("--bag", action="store_true", help="record a rosbag of the run")
    p.set_defaults(fn=cmd_scenario_run)
    p = sub.add_parser("scenarios-pack", help="pack scenarios into a zip (pack-and-go)")
    p.add_argument("ids", nargs="+")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_scenarios_pack)
    p = sub.add_parser("scenarios-unpack", help="import a scenario pack")
    p.add_argument("file")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--replace", action="store_true", help="overwrite scenarios with the same id")
    g.add_argument("--copy", action="store_true", help="import those under a new id")
    p.set_defaults(fn=cmd_scenarios_unpack)
    p = sub.add_parser("assistant-index", help="bring the assistant's knowledge index up to date")
    p.add_argument("--full", action="store_true", help="index and embed every file again")
    p.set_defaults(fn=cmd_assistant_index)
    p = sub.add_parser("init-env", help="generate missing secrets into .env")
    p.set_defaults(fn=cmd_init_env)
    p = sub.add_parser("env-export", help="print export lines for the given keys")
    p.add_argument("keys", nargs="+")
    p.set_defaults(fn=cmd_env_export)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
