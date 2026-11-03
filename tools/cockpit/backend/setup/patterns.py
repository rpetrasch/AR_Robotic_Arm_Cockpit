"""Error patterns for job logs (docs/specs/spec_cockpit.md §8.3), from
config/cockpit/error_patterns.yaml. The exit code decides success; a match explains it."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

import yaml

from config.envfiles import REPO

PATTERNS_FILE = REPO / "config/cockpit/error_patterns.yaml"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # colour codes (colcon, docker build)
SEVERITIES = ("info", "warn", "error", "critical")


@dataclass(frozen=True)
class Pattern:
    id: str
    regex: re.Pattern
    severity: str
    title: str
    hint: str
    docs: str = ""
    fix_step: str = ""


@cache
def load(path: Path = PATTERNS_FILE) -> tuple[Pattern, ...]:
    out = []
    for item in yaml.safe_load(path.read_text()) or []:
        if item["severity"] not in SEVERITIES:
            raise ValueError(f"{item['id']}: severity must be one of {SEVERITIES}")
        out.append(
            Pattern(
                id=item["id"],
                regex=re.compile(item["regex"], re.IGNORECASE),
                severity=item["severity"],
                title=item["title"],
                hint=item["hint"],
                docs=item.get("docs", ""),
                fix_step=item.get("fix_step", ""),
            )
        )
    return tuple(out)


def match(line: str, patterns: tuple[Pattern, ...] | None = None) -> list[dict]:
    """Patterns see the text without ANSI colour codes, so `^` anchors work."""
    line = ANSI.sub("", line)
    hits = []
    for p in load() if patterns is None else patterns:
        if p.regex.search(line):
            d = asdict(p)
            d.pop("regex")
            hits.append(d)
    return hits
