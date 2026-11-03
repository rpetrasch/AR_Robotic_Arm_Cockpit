"""The context pack (docs/specs/spec_cockpit.md §14.1): what the server adds to a question.

The page sends ids (``job_id``, ``check_id``, ``mode_id``, ``scenario_id``), the selection and,
for the scenario editor, the YAML being edited; everything else is read here: host, mode and
arm state from the last status snapshot, failing checks, active alerts, the job's error lines.

Every string is redacted (§11.4) before the pack is budgeted, sent or stored. The budget is
~6000 tokens, sources included; over it, the pack loses old log lines first, then long texts
are shortened, then the lowest-ranked items go.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from django.core.cache import cache

from config.envfiles import REPO
from core.redact import redact, secret_values
from setup.patterns import ANSI

from .index import tokens

BUDGET = 6000
LONG_TEXT = 1200  # characters a long text is cut to in the second truncation pass
SHORTENED = " … [shortened]"
LOG_LINES = 50
LEVEL = re.compile(r"\[(FATAL|ERROR|WARN(?:ING)?)\]")
CONTEXT_KEYS = {"page", "job_id", "match_id", "line", "check_id", "mode_id", "scenario_id", "yaml"}
FILE_REF = re.compile(
    r"\b((?:src|config|tools|docker|docs)/[\w./-]+\.(?:py|yaml|yml|md|sh|xml|"
    r"xacro|launch\.py|cpp|hpp|txt|toml|json))(?::(\d+))?"
)


def clean_context(raw) -> dict:
    """The page's context, limited to known keys and plain strings."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k in CONTEXT_KEYS and isinstance(v, str | int) and str(v):
            out[k] = str(v)[: 60_000 if k == "yaml" else 4000]
    return out


def level_of(text: str) -> str | None:
    m = LEVEL.search(text)
    if not m:
        return None
    return "warn" if m.group(1).startswith("WARN") else "error"


_NOISE = (
    (re.compile(r"\[\d+\.\d+\]"), ""),  # ROS time stamps [seconds.nanoseconds]
    (re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?"), ""),
    (re.compile(r"\b0x[0-9a-f]{6,}\b", re.I), "0x…"),
    (re.compile(r"(\[[\w./-]+)-\d+\]"), r"\1]"),  # [move_group-3] → [move_group]
    (re.compile(r"\s+"), " "),
)


def normalize(text: str) -> str:
    text = ANSI.sub("", text)
    for pattern, repl in _NOISE:
        text = pattern.sub(repl, text)
    return text.strip()


def job_lines(job, lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """(the last LOG_LINES warning/error lines, the distinct ones with counts), in log order.
    A line counts when it has a ROS level or matched an error pattern (§8.3)."""
    picked = [ln for ln in lines if ln.get("match") or level_of(ln["text"])]
    last = [
        {"seq": ln["seq"], "text": normalize(ln["text"])[:600], "level": level_of(ln["text"])}
        for ln in picked[-LOG_LINES:]
    ]
    counts: Counter = Counter()
    first: dict[str, dict] = {}
    for ln in picked:
        key = normalize(ln["text"])[:600]
        counts[key] += 1
        first.setdefault(
            key, {"text": key, "level": level_of(ln["text"]) or "error", "match": ln.get("match")}
        )
    distinct = [{**first[k], "count": counts[k]} for k in first]
    return last, distinct


CONTEXT_LINES = 30


def match_context(lines: list[dict], seqs: list[int]) -> list[str]:
    """The non-empty lines before each matched line: back to colcon's ``--- stderr: <pkg>``
    block start, at most CONTEXT_LINES per match."""
    by_seq = {ln["seq"]: ln for ln in lines}
    out: list[str] = []
    for seq in sorted(set(seqs))[:5]:
        block: list[str] = []
        for s in range(seq - 1, max(seq - CONTEXT_LINES, 1) - 1, -1):
            line = by_seq.get(s)
            if line is None or line["stream"] == "cmd":
                continue
            text = normalize(line["text"])
            if text:
                block.insert(0, text[:400])
            if text.startswith("--- stderr:"):
                break
        out += [t for t in block if t not in out]
    return out


def _job(ctx: dict, task: str) -> dict | None:
    from setup import service
    from setup.models import Job

    job = Job.objects.filter(pk=ctx["job_id"]).first() if ctx.get("job_id") else None
    if job is None:
        return None
    d = service.job_dict(job)
    lines = service.read_log(job)
    last, distinct = job_lines(job, lines)
    out = {
        "id": d["id"],
        "step": d["step_id"],
        "title": d["title"],
        "command": d["command"],
        "status": d["status"],
        "exit_code": d["exit_code"],
        "matches": [
            {
                "id": m["id"],
                "title": m["title"],
                "hint": m["hint"],
                "line": normalize(m["line"])[:400],
                "fix_step": m.get("fix_step", ""),
                "docs": m.get("docs", ""),
            }
            for m in {m["id"]: m for m in job.matches}.values()
        ],
    }
    if task == "error_explain":
        out["distinct_lines"] = distinct
    else:
        out["error_lines"] = last
    # what led up to each matched line: colcon's "Failed <<<" names the package, the cause
    # (a CMake or compiler error) is in the lines just before it
    before = match_context(lines, [m["seq"] for m in job.matches if m.get("seq")])
    if before:
        out["before_matches"] = before
    if ctx.get("match_id"):
        out["asked_about"] = next((m for m in out["matches"] if m["id"] == ctx["match_id"]), None)
    return out


def _status() -> dict:
    from environment.status import CACHE_KEY

    snap = cache.get(CACHE_KEY) or {}
    host = snap.get("host") or {}
    mode = snap.get("mode") or {}
    return {
        "host": {k: host.get(k) for k in ("os", "os_name", "os_version", "arch") if host.get(k)},
        "stack": {
            "overall": snap.get("overall"),
            "reasons": (snap.get("reasons") or [])[:5],
            "mode": mode.get("id"),
            "chip": mode.get("chip"),
            "robot": mode.get("robot"),
            "state": mode.get("state"),
            "arm": snap.get("arm"),
        }
        if snap
        else {"overall": "unknown (no status snapshot yet)"},
    }


def _failing_checks() -> list[dict]:
    from setup import service

    return [
        {
            "id": r["id"],
            "title": r.get("title", r["id"]),
            "status": r["status"],
            "detail": r.get("detail", ""),
            "fix_step": r.get("fix_step") or "",
        }
        for r in service.stored_results()
        if r["status"] in ("fail", "warn")
    ]


def _check(check_id: str) -> dict | None:
    from setup import service
    from setup.checks import BY_ID

    if check_id not in BY_ID:
        return None
    result = next((r for r in service.stored_results() if r["id"] == check_id), None)
    check = BY_ID[check_id]
    return {
        "id": check_id,
        "title": check.title,
        "group": check.group,
        "cli": list(check.cli),
        **({"result": result} if result else {}),
    }


def _alerts() -> list[dict]:
    from monitoring.models import Alert

    return [
        {
            "rule": a.rule,
            "severity": a.severity,
            "subject": a.subject,
            "message": a.message,
            "count": a.count,
        }
        for a in Alert.objects.filter(status=Alert.ACTIVE).order_by("-last_seen")[:10]
    ]


def _mode(mode_id: str) -> dict | None:
    from environment.modes import BY_ID

    m = BY_ID.get(mode_id)
    return m.to_dict() if m else None


def _scenario(ctx: dict) -> dict | None:
    if "yaml" in ctx:
        return {"id": ctx.get("scenario_id", ""), "yaml": ctx["yaml"]}
    if ctx.get("scenario_id"):
        from scenarios import store

        try:
            return {"id": ctx["scenario_id"], "yaml": store.read(ctx["scenario_id"])}
        except Exception:  # unknown id: the question goes without it
            return None
    return None


def file_sources(lines: list[str], repo: Path = REPO, limit: int = 3) -> list[dict]:
    """The code or config files a log names (``src/…/x.py:42``): an excerpt around the line,
    as extra sources. Only files inside the repo; never ``.env``."""
    seen, out = set(), []
    for text in lines:
        for m in FILE_REF.finditer(text):
            rel, line = m.group(1), m.group(2)
            if rel in seen or ".env" in Path(rel).name:
                continue
            path = (repo / rel).resolve()
            if not path.is_file() or repo.resolve() not in path.parents:
                continue
            seen.add(rel)
            rows = path.read_text(errors="replace").splitlines()
            at = int(line) if line else 1
            lo, hi = max(at - 15, 1), min(at + 15, len(rows))
            excerpt = "\n".join(f"{n:>5} {rows[n - 1]}" for n in range(lo, hi + 1))
            out.append(
                {"source": rel, "heading": f"lines {lo}-{hi}", "kind": "file", "text": excerpt}
            )
            if len(out) >= limit:
                return out
    return out


def _redact_all(value, secrets: list[str]):
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [_redact_all(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: _redact_all(v, secrets) for k, v in value.items()}
    return value


def size(pack: dict) -> int:
    import json

    return tokens(json.dumps(pack, ensure_ascii=False))


def _longest_text(pack: dict) -> tuple[list, str | int, int] | None:
    """(container, key, length) of the longest string above LONG_TEXT."""
    best = None

    def walk(node):
        nonlocal best
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for k, v in items:
            if isinstance(v, str) and len(v) > LONG_TEXT and (best is None or len(v) > best[2]):
                best = (node, k, len(v))
            elif isinstance(v, dict | list):
                walk(v)

    walk(pack)
    return best


def fit(pack: dict, budget: int = BUDGET) -> dict:
    """Truncates in place in the spec's order; notes what it did in ``pack["truncated"]``."""
    notes: list[str] = []
    job = pack.get("job") or {}
    for key in ("before_matches", "error_lines", "distinct_lines"):
        dropped = 0
        while size(pack) > budget and job.get(key):
            job[key].pop(0)
            dropped += 1
        if dropped:
            notes.append(f"{dropped} older log line(s) left out")
    shortened = 0
    while size(pack) > budget and (hit := _longest_text(pack)):
        node, key, length = hit
        # the result, marker included, is at most LONG_TEXT: it is not picked again
        node[key] = node[key][: max(LONG_TEXT - len(SHORTENED), length // 2)] + SHORTENED
        shortened += 1
    if shortened:
        notes.append(f"{shortened} long text(s) shortened")
    for key in ("sources", "alerts", "failing_checks"):
        dropped = 0
        while size(pack) > budget and pack.get(key):
            pack[key].pop()
            dropped += 1
        if dropped:
            notes.append(f"{dropped} lower-ranked {key.replace('_', ' ')} left out")
    if notes:
        pack["truncated"] = notes
    return pack


def build(task: str, ctx: dict, sources: list[dict], budget: int = BUDGET) -> dict:
    """The redacted, budgeted pack. ``sources``: retrieved chunks, best first."""
    pack: dict = {"task": task, "page": ctx.get("page", ""), **_status()}
    if failing := _failing_checks():
        pack["failing_checks"] = failing
    if alerts := _alerts():
        pack["alerts"] = alerts
    if job := _job(ctx, task):
        pack["job"] = job
    if ctx.get("check_id") and (check := _check(ctx["check_id"])):
        pack["check"] = check
    if ctx.get("mode_id") and (mode := _mode(ctx["mode_id"])):
        pack["mode"] = mode
    if task == "scenario_help" or ctx.get("scenario_id") or "yaml" in ctx:
        if scenario := _scenario(ctx):
            pack["scenario"] = scenario
    if ctx.get("line"):
        pack["selection"] = ctx["line"]
    pack["sources"] = [
        {
            "n": i + 1,
            "source": s["source"],
            "heading": s["heading"],
            "kind": s["kind"],
            "text": s["text"],
        }
        for i, s in enumerate(sources)
    ]
    return fit(_redact_all(pack, secret_values()), budget)
