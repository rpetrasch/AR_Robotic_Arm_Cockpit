"""Scenario files (docs/specs/spec_cockpit.md §12.1): one YAML per scenario in config/scenarios/,
reviewed like code.

The directory is SCENARIOS_DIR (COCKPIT_SCENARIOS_DIR; default <repo>/config/scenarios), so
the tests and the e2e server use their own. Saving keeps the text as written (comments,
order) and only bumps ``version:`` (inserted after ``id:`` when missing). The file name is the
id. Writes are atomic (temp file + rename).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml
from django.conf import settings

ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION_LINE = re.compile(r"^version:\s*\d+\s*(#.*)?$", re.M)
ID_LINE = re.compile(r"^id:.*$", re.M)


class StoreError(ValueError):
    pass


def directory() -> Path:
    d = Path(settings.SCENARIOS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def path(scenario_id: str) -> Path:
    if not ID.match(scenario_id or ""):
        raise StoreError(f"invalid scenario id {scenario_id!r} (a-z, 0-9, -)")
    return directory() / f"{scenario_id}.yaml"


def exists(scenario_id: str) -> bool:
    return path(scenario_id).exists()


def read(scenario_id: str) -> str:
    p = path(scenario_id)
    if not p.exists():
        raise FileNotFoundError(scenario_id)
    return p.read_text()


def load(scenario_id: str) -> dict:
    return yaml.safe_load(read(scenario_id)) or {}


def ids() -> list[str]:
    return sorted(p.stem for p in directory().glob("*.yaml") if ID.match(p.stem))


def all_scenarios() -> list[dict]:
    out = []
    for sid in ids():
        try:
            data = load(sid)
        except yaml.YAMLError:
            data = {"id": sid, "name": sid, "broken": True}
        out.append(data if isinstance(data, dict) else {"id": sid, "name": sid, "broken": True})
    return out


def set_field(text: str, key: str, value: str) -> str:
    """Replace a top-level ``key: …`` line, or insert it after ``id:``."""
    line = re.compile(rf"^{key}:.*$", re.M)
    if line.search(text):
        return line.sub(f"{key}: {value}", text, count=1)
    m = ID_LINE.search(text)
    if m:
        return text[: m.end()] + f"\n{key}: {value}" + text[m.end() :]
    return f"{key}: {value}\n" + text


def _write(p: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.stem}.", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, p)


def save(text: str, expected_id: str | None = None) -> tuple[str, int]:
    """Write the scenario (already validated); returns (id, new version)."""
    data = yaml.safe_load(text) or {}
    sid = data.get("id", "")
    if expected_id is not None and sid != expected_id:
        raise StoreError(f"the id is {sid!r}; rename by saving as a new scenario")
    p = path(sid)
    old = int((yaml.safe_load(p.read_text()) or {}).get("version", 0)) if p.exists() else 0
    version = (
        max(old, int(data.get("version") or 0)) + 1
        if p.exists()
        else max(1, int(data.get("version") or 1))
    )
    _write(p, set_field(text, "version", str(version)))
    return sid, version


def create(text: str) -> tuple[str, int]:
    data = yaml.safe_load(text) or {}
    if exists(data.get("id", "")):
        raise StoreError(f"a scenario {data['id']!r} exists already")
    return save(text)


def delete(scenario_id: str) -> None:
    p = path(scenario_id)
    if not p.exists():
        raise FileNotFoundError(scenario_id)
    p.unlink()


def free_id(base: str) -> str:
    """``base``, else ``base-2``, ``base-3``, …"""
    base = base[:58]
    if not exists(base):
        return base
    n = 2
    while exists(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


def duplicate(scenario_id: str, new_id: str | None = None) -> tuple[str, int]:
    text = read(scenario_id)
    data = yaml.safe_load(text) or {}
    nid = new_id or free_id(f"{scenario_id}-copy")
    if exists(nid):
        raise StoreError(f"a scenario {nid!r} exists already")
    text = set_field(text, "id", nid)
    text = set_field(text, "name", yaml.safe_dump(f"{data.get('name', scenario_id)} (copy)").strip()
                     .removesuffix("...").strip())  # fmt: skip
    text = set_field(text, "version", "1")
    _write(path(nid), text)
    return nid, 1
