"""Overview metrics (docs/specs/spec_cockpit.md §10.1): the host, the containers, the GPU.

- host: CPU %, memory, disk free of the repo's file system (psutil; the backend runs on the
  host itself, §4);
- containers: ``docker stats --no-stream --format json`` (takes about 2 s, so run_monitor
  polls it every 5 s in its own task);
- GPU (Linux with NVIDIA): ``nvidia-smi --query-gpu=… --format=csv,noheader,nounits``.

run_monitor caches the latest values (``CACHE_KEY``) for the API and the alert rules and
writes them to the ``metrics`` hypertable at 1 Hz while the session records.
"""

from __future__ import annotations

import json
import re
import shutil
import time

from django.conf import settings

CACHE_KEY = "cockpit:metrics"
GPU_QUERY = "name,utilization.gpu,memory.used,memory.total,temperature.gpu"
_SIZE = re.compile(r"([\d.]+)\s*([KMGT]?i?B)", re.I)
_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024**2,
          "gib": 1024**3, "tib": 1024**4}  # fmt: skip


def host(disk_path=None) -> dict:
    import psutil

    vm = psutil.virtual_memory()
    du = shutil.disk_usage(disk_path or settings.REPO_DIR)
    load = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "load_1m": round(load[0], 2),
        "mem_used_gb": round((vm.total - vm.available) / 1e9, 2),
        "mem_total_gb": round(vm.total / 1e9, 2),
        "mem_percent": vm.percent,
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "disk_path": str(disk_path or settings.REPO_DIR),
        "uptime_s": round(time.time() - psutil.boot_time()),
    }


def _bytes(text: str) -> float | None:
    m = _SIZE.search(text or "")
    if not m:
        return None
    return float(m.group(1)) * _UNITS.get(m.group(2).lower(), 1)


def parse_stats(stdout: str, project_names: set[str] | None = None) -> list[dict]:
    """``docker stats --format json`` lines → per-container CPU % and memory."""
    out = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        name = row.get("Name", "")
        if project_names is not None and name not in project_names:
            continue
        used, _sep, limit = (row.get("MemUsage") or "").partition("/")
        out.append(
            {
                "name": name,
                "cpu_percent": float((row.get("CPUPerc") or "0%").rstrip("%") or 0),
                "mem_used_mb": round((_bytes(used) or 0) / 1e6, 1),
                "mem_limit_mb": round((_bytes(limit) or 0) / 1e6, 1),
                "mem_percent": float((row.get("MemPerc") or "0%").rstrip("%") or 0),
                "pids": int(row.get("PIDs") or 0),
            }
        )
    return sorted(out, key=lambda r: r["name"])


async def containers(runner, names: list[str]) -> list[dict]:
    if not names:
        return []
    res = await runner.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *names], timeout=15
    )
    return parse_stats(res.stdout, set(names)) if res.ok else []


def parse_gpu(stdout: str) -> list[dict]:
    out = []
    for i, line in enumerate(stdout.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue

        def num(v: str):
            try:
                return float(v)
            except ValueError:
                return None  # "[N/A]"

        name, util, used, total, temp = parts
        out.append(
            {
                "index": i,
                "name": name,
                "util_percent": num(util),
                "mem_used_mb": num(used),
                "mem_total_mb": num(total),
                "temp_c": num(temp),
            }
        )
    return out


async def gpus(runner, host_os: str) -> list[dict]:
    if host_os != "linux":
        return []
    res = await runner.run(
        ["nvidia-smi", f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"], timeout=5
    )
    return parse_gpu(res.stdout) if res.ok else []


def rows(snapshot: dict, session_id) -> list[tuple]:
    """``metrics`` rows (time, session_id, name, labels, value) of one snapshot."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    out = []
    h = snapshot.get("host") or {}
    for key in ("cpu_percent", "mem_percent", "mem_used_gb", "disk_free_gb", "load_1m"):
        if h.get(key) is not None:
            out.append((now, session_id, f"host_{key}", "{}", h[key]))
    for c in snapshot.get("containers") or []:
        labels = json.dumps({"container": c["name"]})
        out.append((now, session_id, "container_cpu_percent", labels, c["cpu_percent"]))
        out.append((now, session_id, "container_mem_mb", labels, c["mem_used_mb"]))
    for g in snapshot.get("gpus") or []:
        labels = json.dumps({"gpu": g["index"], "name": g["name"]})
        for key in ("util_percent", "mem_used_mb", "temp_c"):
            if g.get(key) is not None:
                out.append((now, session_id, f"gpu_{key}", labels, g[key]))
    return out  # sim_rtf and topic rates come with the telemetry (telemetry.ingest)
