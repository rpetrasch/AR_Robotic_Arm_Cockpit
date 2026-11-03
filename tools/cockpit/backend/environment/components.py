"""The component catalogue (docs/specs/spec_cockpit.md §3.4) from config/cockpit/components.yaml,
joined with live health (§9.2) for /api/components and rendered to Markdown for the docs."""

from __future__ import annotations

import posixpath
from functools import cache
from pathlib import Path

import yaml
from django.conf import settings

from .modes import Mode

FIELDS = ("id", "name", "what", "runs_in", "ports", "depends", "health", "layer", "links", "cli")
KINDS = {"every_stack", "gazebo", "vnc", "linux_windows", "real", "remote", "cockpit", "optional"}
LAYERS = ("browser", "host", "stack", "infra")
DOC_DIR = "docs/manual"  # where cockpit-components.md lives: its links are relative to it
CLI_KEYS = ("start", "stop", "logs", "status", "shell")


def path(repo: Path | None = None) -> Path:
    return (repo or settings.REPO_DIR) / "config/cockpit/components.yaml"


@cache
def _load(file: Path) -> tuple[dict, ...]:
    return tuple(yaml.safe_load(file.read_text()))


def catalogue(repo: Path | None = None) -> list[dict]:
    return [dict(c) for c in _load(path(repo))]


def validate(items: list[dict]) -> list[str]:
    """Problems in the catalogue (a test keeps the shipped file clean)."""
    problems, ids = [], {c.get("id") for c in items}
    for c in items:
        cid = c.get("id", "?")
        problems += [f"{cid}: missing {f}" for f in FIELDS if f not in c]
        problems += [f"{cid}: unknown runs_in {k}" for k in c.get("runs_in", []) if k not in KINDS]
        if c.get("layer") not in LAYERS:
            problems.append(f"{cid}: layer must be one of {LAYERS}")
        problems += [f"{cid}: depends on unknown {d}" for d in c.get("depends", []) if d not in ids]
        problems += [
            f"{cid}: link to unknown {lk.get('to')}"
            for lk in c.get("links", [])
            if lk.get("to") not in ids
        ]
        problems += [f"{cid}: cli.{k} missing" for k in CLI_KEYS if k not in c.get("cli", {})]
    return problems


def applies(component: dict, mode: Mode | None) -> bool:
    """Does the component run in ``mode`` (None: no stack)?"""
    kinds = set(component["runs_in"])
    if kinds & {"cockpit", "optional"}:
        return True
    if mode is None:
        return False
    if mode.robot == "remote":
        return "remote" in kinds
    if mode.robot == "real":
        return bool(kinds & {"every_stack", "real"})
    stack = {"every_stack"}
    if mode.robot == "gazebo":
        stack.add("gazebo")
    if mode.service == "vnc":
        stack.add("vnc")
    if mode.service in ("linux", "linux-gpu") and not mode.headless:
        stack.add("linux_windows")
    return bool(kinds & stack)


def with_status(items: list[dict], health_rows: list[dict], mode: Mode | None) -> list[dict]:
    rows = {r["id"]: r for r in health_rows}
    out = []
    for c in items:
        live = rows.get(c["health"]) if c.get("health") else None
        out.append({**c, "in_mode": applies(c, mode), "live": live})
    return out


def markdown(items: list[dict]) -> str:
    """docs/manual/cockpit-components.md (`make cockpit-docs`)."""
    lines = [
        "# AR3 Cockpit: components",
        "",
        "Generated from `config/cockpit/components.yaml` by `make cockpit-docs`; edit the YAML,",
        "not this file. The cockpit shows the same under Environment → Components, with live",
        "status ([cockpit.md](cockpit.md)).",
        "",
    ]
    for c in items:
        ports = ", ".join(str(p) for p in c["ports"]) or "—"
        lines += [
            f"## {c['name']}",
            "",
            " ".join(c["what"].split()),
            "",
            f"- **Runs in:** {', '.join(c['runs_in'])}",
            f"- **Ports:** {ports}",
            f"- **Depends on:** {', '.join(c['depends']) or '—'}",
            f"- **Docs:** [{c['docs']}]({posixpath.relpath(c['docs'], DOC_DIR)})",
            "",
            "| | Command |",
            "|---|---|",
        ]
        lines += [f"| {k} | `{c['cli'][k]}` |" for k in CLI_KEYS]
        lines.append("")
    return "\n".join(lines)
