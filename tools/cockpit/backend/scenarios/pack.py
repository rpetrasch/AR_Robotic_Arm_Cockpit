"""Pack-and-go (docs/specs/spec_cockpit.md §12.3, owner ToDo 2026-11-01): scenarios from one cockpit
to another (Mac ↔ Workstation A) as one zip.

The pack (``ar3-scenarios-<host>-<time>.zip``):

    manifest.json          format, source host, git commit, created, per scenario id /
                           version / sha256 / file, the referenced files with their sha256
    scenarios/<id>.yaml    the scenario files as they are
    config/bins.yaml       when a scenario references bins of it
    config/robot.yaml      table and mount (for the comparison on import; never written)
    meshes/<file>          meshes of ``mesh`` objects

Import is two steps: ``preview`` stores the upload and returns, per scenario, new / same /
differs (with a unified diff against the existing file) and validation problems, plus
warnings where the target's config (bins, table) differs from the pack's; ``apply`` takes a
decision per scenario: keep (skip), replace, copy (import under a free id). Config files are
never overwritten; meshes are added when missing.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import secrets
import socket
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import yaml
from django.conf import settings

from . import store, validate

FORMAT = "ar3-scenario-pack/1"
MAX_PACK_BYTES = 50 * 1024 * 1024
DECISIONS = ("keep", "replace", "copy")


class PackError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_commit() -> str:
    head = Path(settings.REPO_DIR) / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            return (head.parent / ref[5:]).read_text().strip()
        return ref
    except OSError:
        return ""


def _references(data: dict) -> tuple[set[str], set[str]]:
    """(config files, mesh files) a scenario needs."""
    configs, meshes = set(), set()
    scene = data.get("scene") or {}
    for b in scene.get("bins") or []:
        if b.get("ref"):
            configs.add(b["ref"])
    rules = (data.get("task") or {}).get("params", {}).get("rules")
    if isinstance(rules, str):
        configs.add(rules)
    for o in [*(scene.get("objects") or []), *(scene.get("obstacles") or [])]:
        if o.get("shape") == "mesh" and o.get("mesh"):
            meshes.add(o["mesh"])
    return configs, meshes


def pack(ids: list[str]) -> tuple[str, bytes]:
    """(file name, zip bytes)."""
    if not ids:
        raise PackError("no scenarios selected")
    repo = Path(settings.REPO_DIR)
    buf = io.BytesIO()
    manifest = {
        "format": FORMAT,
        "source_host": socket.gethostname(),
        "git_commit": _git_commit(),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "scenarios": [],
        "files": [],
    }
    configs, meshes = {"config/robot.yaml"}, set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sid in ids:
            try:
                text = store.read(sid)
            except FileNotFoundError:
                raise PackError(f"no scenario {sid!r}") from None
            data = yaml.safe_load(text) or {}
            z.writestr(f"scenarios/{sid}.yaml", text)
            manifest["scenarios"].append({"id": sid, "version": data.get("version"),
                                          "name": data.get("name", sid),
                                          "sha256": _sha(text.encode()),
                                          "file": f"scenarios/{sid}.yaml"})  # fmt: skip
            c, m = _references(data)
            configs |= c
            meshes |= m
        for rel in sorted(configs):
            p = repo / rel
            if p.exists():
                z.write(p, rel)
                manifest["files"].append({"path": rel, "sha256": _sha(p.read_bytes())})
        for mesh in sorted(meshes):
            p = store.directory() / "meshes" / mesh
            if p.exists():
                z.write(p, f"meshes/{mesh}")
                manifest["files"].append({"path": f"meshes/{mesh}", "sha256": _sha(p.read_bytes())})
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    host = manifest["source_host"].split(".")[0]
    name = f"ar3-scenarios-{host}-{datetime.now(UTC):%Y%m%d-%H%M%S}.zip"
    return name, buf.getvalue()


def _pending_dir() -> Path:
    d = Path(settings.DATA_DIR) / "packs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _open(data: bytes) -> tuple[zipfile.ZipFile, dict]:
    if len(data) > MAX_PACK_BYTES:
        raise PackError("the pack is larger than 50 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise PackError("not a scenario pack (no manifest.json)") from None
    if manifest.get("format") != FORMAT:
        raise PackError(f"unknown pack format {manifest.get('format')!r}")
    for name in z.namelist():
        if name.startswith("/") or ".." in Path(name).parts:
            raise PackError(f"unsafe path in the pack: {name}")
    return z, manifest


def _config_warnings(z: zipfile.ZipFile, manifest: dict) -> list[str]:
    repo = Path(settings.REPO_DIR)
    out = []
    for f in manifest.get("files", []):
        rel = f["path"]
        if not rel.startswith("config/"):
            continue
        here = repo / rel
        if not here.exists():
            out.append(f"{rel} does not exist here")
            continue
        if _sha(here.read_bytes()) == f["sha256"]:
            continue
        if rel == "config/robot.yaml":
            theirs = yaml.safe_load(z.read(rel)) or {}
            ours = yaml.safe_load(here.read_text()) or {}
            for key in ("table", "mount"):
                if theirs.get(key) != ours.get(key):
                    out.append(f"config/robot.yaml `{key}` differs from the pack's "
                               f"({manifest['source_host']}): poses may not match")  # fmt: skip
        else:
            out.append(f"{rel} differs from the pack's ({manifest['source_host']})")
    return out


def preview(data: bytes) -> dict:
    z, manifest = _open(data)
    token = secrets.token_urlsafe(12)
    (_pending_dir() / f"{token}.zip").write_bytes(data)
    items = []
    for s in manifest["scenarios"]:
        text = z.read(s["file"]).decode()
        checked = validate.validate(text)
        sid = s["id"]
        status, diff, existing_version = "new", "", None
        if store.exists(sid):
            mine = store.read(sid)
            existing_version = (yaml.safe_load(mine) or {}).get("version")
            if mine == text:
                status = "same"
            else:
                status = "differs"
                diff = "".join(
                    difflib.unified_diff(
                        mine.splitlines(True),
                        text.splitlines(True),
                        f"here/{sid}.yaml",
                        f"pack/{sid}.yaml",
                    )
                )
        items.append({"id": sid, "name": s.get("name", sid), "version": s.get("version"),
                      "existing_version": existing_version, "status": status, "diff": diff,
                      "errors": checked["errors"], "warnings": checked["warnings"],
                      "default": "keep" if status == "same" else ("replace" if status == "new"
                                                                  else "copy")})  # fmt: skip
    return {
        "token": token,
        "source_host": manifest.get("source_host"),
        "git_commit": manifest.get("git_commit"),
        "created": manifest.get("created"),
        "scenarios": items,
        "config_warnings": _config_warnings(z, manifest),
    }


def apply(token: str, decisions: dict[str, str]) -> dict:
    path = _pending_dir() / f"{Path(token).name}.zip"
    if not path.exists():
        raise PackError("this import expired; upload the pack again")
    z, manifest = _open(path.read_bytes())
    done = []
    for s in manifest["scenarios"]:
        sid = s["id"]
        decision = decisions.get(sid, "keep")
        if decision not in DECISIONS:
            raise PackError(f"{sid}: decision must be one of {DECISIONS}")
        if decision == "keep":
            done.append({"id": sid, "action": "kept"})
            continue
        text = z.read(s["file"]).decode()
        if not validate.validate(text)["ok"]:
            done.append({"id": sid, "action": "skipped", "reason": "invalid"})
            continue
        if decision == "copy" and store.exists(sid):
            new = store.free_id(f"{sid}-imported")
            text = store.set_field(text, "id", new)
            store._write(store.path(new), store.set_field(text, "version", "1"))
            done.append({"id": new, "action": "copied", "from": sid})
        else:
            store._write(store.path(sid), text)
            done.append({"id": sid, "action": "replaced" if decision == "replace" else "added"})
    mesh_dir = store.directory() / "meshes"
    for f in manifest.get("files", []):
        if f["path"].startswith("meshes/"):
            target = mesh_dir / Path(f["path"]).name
            if not target.exists():
                mesh_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(f["path"]))
                done.append({"id": f["path"], "action": "mesh added"})
    path.unlink()
    return {"done": done}
