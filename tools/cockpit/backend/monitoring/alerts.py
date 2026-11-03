"""Alert engine (docs/specs/spec_cockpit.md §10.5): rules from config/cockpit/alerts.yaml over
facts, evaluated by run_monitor every 2 s.

A rule's ``when`` is a small expression language, parsed with Python's ``ast`` and evaluated
by a whitelist (names, attributes, constants, lists, and / or / not, comparisons, ``in``):
nothing in it can call or import. A missing fact is None, and every comparison with None is
false, so a rule never fires on data it does not have.

The root name decides the subjects: ``component`` / ``joint`` / ``camera`` / ``gpu`` iterate
over their lists, ``log.match`` over the error-pattern matches since the last evaluation, a
rule with ``topic:`` has that topic's ``age_s`` and ``rate_hz``. Everything else (``mode``,
``safety``, ``disk``, ``ingest``) is global, with an empty subject.

Lifecycle: firing and no open alert → a new ``active`` one; firing and open → last_seen and
count; not firing → ``resolved``. Matches of log patterns are events: their alert resolves
``LOG_HOLD_S`` after the last match.
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cache
from pathlib import Path

import yaml
from django.conf import settings
from django.utils import timezone

from config.envfiles import REPO

RULES_FILE = REPO / "config/cockpit/alerts.yaml"
SEVERITIES = ("info", "warn", "error", "critical")
LIST_ROOTS = ("component", "joint", "camera", "gpu")
LOG_HOLD_S = 600
GROUP_ALERT = "alerts"
_CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


class RuleError(ValueError):
    pass


def _check(node: ast.AST) -> None:
    allowed = (
        ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Compare,
        ast.Name, ast.Attribute, ast.Constant, ast.List, ast.Tuple, ast.Load, ast.In, ast.NotIn,
        *_CMP,
    )  # fmt: skip
    for n in ast.walk(node):
        if not isinstance(n, allowed):
            raise RuleError(f"not allowed in a rule: {type(n).__name__}")


def _root(node: ast.AST) -> str:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def evaluate(expr: ast.Expression, facts: dict) -> bool:
    def value(n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.List | ast.Tuple):
            return [value(e) for e in n.elts]
        if isinstance(n, ast.Name):
            return facts.get(n.id)
        if isinstance(n, ast.Attribute):
            base = value(n.value)
            return base.get(n.attr) if isinstance(base, dict) else None
        if isinstance(n, ast.UnaryOp):
            return not truth(n.operand)
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                return all(truth(v) for v in n.values)
            return any(truth(v) for v in n.values)
        if isinstance(n, ast.Compare):
            left = value(n.left)
            for op, right_node in zip(n.ops, n.comparators, strict=True):
                right = value(right_node)
                if isinstance(op, ast.In | ast.NotIn):
                    if left is None or not isinstance(right, list):
                        return False
                    ok = (left in right) == isinstance(op, ast.In)
                elif left is None or right is None:
                    ok = isinstance(op, ast.Eq | ast.NotEq) and _CMP[type(op)](left, right)
                else:
                    try:
                        ok = _CMP[type(op)](left, right)
                    except TypeError:
                        return False
                if not ok:
                    return False
                left = right
            return True
        raise RuleError(type(n).__name__)

    def truth(n) -> bool:
        return bool(value(n))

    return truth(expr.body)


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    when: str
    severity: str
    topic: str = ""
    expr: ast.Expression = field(compare=False, repr=False, default=None)
    root: str = ""  # the list the rule iterates over ("" = global)

    def subjects(self, facts: dict) -> list[tuple[str, dict]]:
        """(subject, facts for this subject) pairs to evaluate."""
        if self.topic:
            rates = facts.get("topics")
            if rates is None:  # no agent: nothing to say about topics
                return []
            row = rates.get(self.topic) or {}
            return [(self.topic, {**facts, "age_s": row.get("age_s"), "rate_hz": row.get("hz")})]
        if self.root == "log":
            return [(m.get("id", ""), {**facts, "log": {"match": m}}) for m in facts.get("log", [])]
        if self.root in LIST_ROOTS:
            return [
                (str(item.get("id") or item.get("name") or i), {**facts, self.root: item})
                for i, item in enumerate(facts.get(self.root) or [])
            ]
        return [("", facts)]


def parse(item: dict) -> Rule:
    for key in ("id", "title", "when", "severity"):
        if key not in item:
            raise RuleError(f"rule {item.get('id', '?')}: {key} is missing")
    if item["severity"] not in (*SEVERITIES, "from_match"):
        raise RuleError(f"rule {item['id']}: severity must be one of {SEVERITIES} or from_match")
    try:
        expr = ast.parse(item["when"], mode="eval")
    except SyntaxError as exc:
        raise RuleError(f"rule {item['id']}: {exc.msg}") from None
    _check(expr)
    roots = {_root(n) for n in ast.walk(expr) if isinstance(n, ast.Attribute)}
    root = next((r for r in (*LIST_ROOTS, "log") if r in roots), "")
    return Rule(
        id=item["id"],
        title=item["title"],
        when=item["when"],
        severity=item["severity"],
        topic=item.get("topic", ""),
        expr=expr,
        root=root,
    )


@cache
def load(path: Path = RULES_FILE) -> tuple[Rule, ...]:
    rules = tuple(parse(item) for item in yaml.safe_load(path.read_text()) or [])
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise RuleError("duplicate rule ids in alerts.yaml")
    return rules


@dataclass
class Firing:
    rule: str
    subject: str
    severity: str
    message: str
    details: dict


def _message(rule: Rule, subject: str, sf: dict) -> tuple[str, dict]:
    if rule.topic:
        age = sf.get("age_s")
        return f"{rule.title}: {subject} (last message {age:.1f} s ago)", {"age_s": age}
    if rule.root == "component":
        c = sf["component"]
        return f"{rule.title}: {c['id']} — {c.get('detail', '')}", {"component": c}
    if rule.root == "log":
        m = sf["log"]["match"]
        return f"{m.get('title', rule.title)} ({m.get('source', '')})", {"match": m}
    if rule.root in ("joint", "gpu", "camera"):
        item = sf[rule.root]
        return f"{rule.title}: {subject}", {rule.root: item}
    return rule.title, {k: sf.get(k) for k in ("disk", "ingest", "safety") if sf.get(k)}


def firing(facts: dict, rules: tuple[Rule, ...] | None = None) -> list[Firing]:
    out = []
    for rule in load() if rules is None else rules:
        for subject, sf in rule.subjects(facts):
            if evaluate(rule.expr, sf):
                severity = rule.severity
                if severity == "from_match":
                    severity = sf["log"]["match"].get("severity", "error")
                message, details = _message(rule, subject, sf)
                out.append(Firing(rule.id, subject, severity, message, details))
    return out


# --- facts ---------------------------------------------------------------------------------


def joint_margins(joints: dict | None, limits_deg: dict) -> list[dict]:
    """Distance of each joint to its nearer limit (config/robot.yaml), in degrees."""
    import math

    if not joints:
        return []
    out = []
    for name, pos in zip(joints.get("name", []), joints.get("position", []), strict=False):
        if name not in limits_deg or pos is None:
            continue
        lo, hi = limits_deg[name]
        deg = math.degrees(pos)
        out.append(
            {"id": name, "name": name, "deg": round(deg, 2),
             "margin_deg": round(min(deg - lo, hi - deg), 2)}
        )  # fmt: skip
    return out


def facts(snapshot: dict, extra: dict) -> dict:
    """The rule inputs from a status snapshot plus what run_monitor gathered besides it:
    ``extra``: topics, joints, metrics, log matches, ingest state, dead stack mode."""
    mode = snapshot.get("mode")
    chip = mode["chip"] if mode else (extra.get("dead_mode") or "STOPPED")
    metrics = extra.get("metrics") or {}
    host = metrics.get("host") or {}
    arm = snapshot.get("arm")
    return {
        "mode": chip,
        "component": snapshot.get("components", []),
        "topics": extra.get("topics"),
        "safety": {"estop": arm == "E_STOP"} if arm is not None else {},
        "joint": joint_margins(extra.get("joints"), extra.get("limits_deg", {})),
        "camera": extra.get("cameras", []),
        "gpu": metrics.get("gpus", []),
        "disk": {"free_gb": host.get("disk_free_gb")} if host else {},
        "log": extra.get("log_matches", []),
        "ingest": extra.get("ingest") or {},
    }


# --- lifecycle (DB) --------------------------------------------------------------------------


def apply(fired: list[Firing], session_id=None, now=None) -> dict:
    """Open, update and resolve alerts. Returns {"new": [alert dicts], "resolved": n}."""
    from .models import Alert

    now = now or timezone.now()
    keys = {(f.rule, f.subject): f for f in fired}
    open_alerts = {(a.rule, a.subject): a for a in Alert.objects.filter(status__in=Alert.OPEN)}
    new = []
    for key, f in keys.items():
        alert = open_alerts.get(key)
        if alert is None:
            alert = Alert.objects.create(
                rule=f.rule,
                subject=f.subject,
                severity=f.severity,
                message=f.message,
                details=f.details,
                last_seen=now,
                session_id=session_id,
            )
            new.append(alert)
        else:
            alert.last_seen, alert.count = now, alert.count + 1
            alert.message, alert.details = f.message, f.details
            if _rank(f.severity) > _rank(alert.severity):
                alert.severity = f.severity
            alert.save(update_fields=["last_seen", "count", "message", "details", "severity"])
    resolved = 0
    for key, alert in open_alerts.items():
        if key in keys:
            continue
        if alert.rule == "log_error_pattern" and now - alert.last_seen < timedelta(
            seconds=LOG_HOLD_S
        ):
            continue
        alert.status, alert.resolved_at = Alert.RESOLVED, now
        alert.save(update_fields=["status", "resolved_at"])
        resolved += 1
    from telemetry import events

    for alert in new:
        events.record_sync("alert", {"rule": alert.rule, "subject": alert.subject,
                                     "severity": alert.severity, "message": alert.message},
                           session_id)  # fmt: skip
    return {"new": [to_dict(a) for a in new], "resolved": resolved}


def _rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


def acknowledge(alert_id: int, user: str, comment: str = ""):
    from .models import Alert

    alert = Alert.objects.get(pk=alert_id)
    if alert.status == Alert.ACTIVE:
        alert.status = Alert.ACKNOWLEDGED
        alert.acknowledged_by, alert.acknowledged_at = user, timezone.now()
        alert.comment = comment[:2000]
        alert.save(update_fields=["status", "acknowledged_by", "acknowledged_at", "comment"])
    return alert


def summary() -> dict:
    """For the header (§7.2): active alerts by severity, the worst, the newest few."""
    from .models import Alert

    active = list(Alert.objects.filter(status=Alert.ACTIVE).order_by("-first_seen")[:200])
    acknowledged = Alert.objects.filter(status=Alert.ACKNOWLEDGED).count()
    by = {s: 0 for s in SEVERITIES}
    for a in active:
        by[a.severity] = by.get(a.severity, 0) + 1
    worst = max((a.severity for a in active), key=_rank, default=None)
    return {
        "active": len(active),
        "acknowledged": acknowledged,
        "by_severity": by,
        "worst": worst,
        "recent": [to_dict(a) for a in active[:5]],
    }


def pill(worst: str | None) -> str | None:
    """The overall pill an active alert asks for: critical/error → ERROR, warn → WARN."""
    if worst in ("error", "critical"):
        return "ERROR"
    if worst == "warn":
        return "WARN"
    return None


def prune(now=None) -> int:
    from .models import Alert

    cutoff = (now or timezone.now()) - timedelta(days=settings.ALERT_HISTORY_DAYS)
    deleted, _ = Alert.objects.filter(status=Alert.RESOLVED, resolved_at__lt=cutoff).delete()
    return deleted


def to_dict(a) -> dict:
    return {
        "id": a.id,
        "rule": a.rule,
        "subject": a.subject,
        "severity": a.severity,
        "status": a.status,
        "message": a.message,
        "details": a.details,
        "first_seen": a.first_seen.isoformat() if a.first_seen else None,
        "last_seen": a.last_seen.isoformat() if a.last_seen else None,
        "count": a.count,
        "acknowledged_by": a.acknowledged_by,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "comment": a.comment,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
    }
