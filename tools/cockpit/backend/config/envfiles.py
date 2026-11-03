"""The cockpit's configuration files (docs/specs/spec_cockpit.md §13).

Three layers, the first one that has a key wins:

1. the process environment (the shell, cockpit.sh, CI);
2. ``<repo>/.env``: this machine's values and the generated secrets, never committed;
3. ``tools/cockpit/backend/defaults.env``: committed, every key with a comment.

``load()`` copies the file layers into the environment for keys that are not set yet, so the
rest of the backend only reads ``os.environ``. Under ``COCKPIT_ENV=test`` the ``.env`` layer
is reduced to the database and Redis settings: the test suite needs the developer's database
password, but no other local override may change a test.

The file format is the usual env-file subset: ``KEY=value`` per line, ``export KEY=value``,
``#`` comment lines, a `` #`` comment after an unquoted value, and single or double quotes
around a value (taken literally, no escapes).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[2]
DEFAULTS = BACKEND / "defaults.env"
LOCAL = REPO / ".env"
# What a test run takes from .env (see the module docstring).
TEST_KEYS = ("COCKPIT_DB_", "REDIS_URL")
TRUE = frozenset({"1", "true", "yes", "on"})


def _valid_key(key: str) -> bool:
    return (
        bool(key)
        and (key[0].isalpha() or key[0] == "_")
        and all(c.isalnum() or c == "_" for c in key)
        and key.isascii()
    )


def _value(text: str) -> str:
    """The value part of a line: quoted literally, or up to a `` #`` comment."""
    text = text.strip()
    if text[:1] in ("'", '"'):
        closing = text.find(text[0], 1)
        return text[1:] if closing < 0 else text[1:closing]
    for i, c in enumerate(text):
        if c == "#" and i > 0 and text[i - 1] in " \t":
            return text[:i].rstrip()
    return text


def parse(path: Path) -> dict[str, str]:
    """The ``KEY=value`` pairs of an env file ({} when it does not exist). Lines with an invalid
    key or without ``=`` are ignored."""
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[len("export") :].strip()
        if not line or line.startswith("#"):
            continue
        key, sep, rest = line.partition("=")
        key = key.strip()
        if sep and _valid_key(key):
            values[key] = _value(rest)
    return values


@dataclass
class Layer:
    name: str
    values: dict[str, str] = field(default_factory=dict)


def file_layers(
    local: Path = LOCAL, defaults: Path = DEFAULTS, environ: MutableMapping | None = None
) -> list[Layer]:
    """``.env`` then ``defaults.env`` (the process environment is not a file layer)."""
    environ = os.environ if environ is None else environ
    mine = parse(local)
    if environ.get("COCKPIT_ENV") == "test":
        mine = {k: v for k, v in mine.items() if k.startswith(TEST_KEYS)}
    return [Layer(".env", mine), Layer("defaults.env", parse(defaults))]


_applied: dict[str, str] | None = None  # what the process-wide load() set


def load(
    local: Path = LOCAL, defaults: Path = DEFAULTS, environ: MutableMapping | None = None
) -> dict[str, str]:
    """Fill ``environ`` (default: ``os.environ``) from the file layers without touching keys
    that are set already. Returns the keys it set. Without arguments it works once per process
    and later calls return the first result."""
    global _applied
    process_wide = environ is None and local == LOCAL and defaults == DEFAULTS
    if process_wide and _applied is not None:
        return _applied
    target = os.environ if environ is None else environ
    applied: dict[str, str] = {}
    for layer in file_layers(local, defaults, target):
        for key, value in layer.values.items():
            if key not in target:
                target[key] = applied[key] = value
    if process_wide:
        _applied = applied
    return applied


def source_of(
    key: str, local: Path = LOCAL, defaults: Path = DEFAULTS, environ: MutableMapping | None = None
) -> str:
    """Where a key's value comes from: ``env``, ``.env``, ``defaults.env`` or ``unset``. Reads
    only; for ``os.environ`` the keys the process-wide ``load()`` set count as file values."""
    environ = os.environ if environ is None else environ
    from_files = (_applied or {}) if environ is os.environ else {}
    if key in environ and key not in from_files:
        return "env"
    return next((layer.name for layer in file_layers(local, defaults, environ)
                 if key in layer.values), "unset")  # fmt: skip


def flag(key: str, default: str = "0") -> bool:
    """A yes/no setting: 1, true, yes, on (any case) mean yes."""
    return os.environ.get(key, default).strip().lower() in TRUE
