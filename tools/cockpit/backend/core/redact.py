"""Secret redaction for logs (docs/specs/spec_cockpit.md §11.4): values of secret-looking settings
and known key prefixes never reach a job log or the browser."""

from __future__ import annotations

import os
import re

SECRET_KEY_NAMES = re.compile(r"(SECRET|PASSWORD|TOKEN|API_KEY|_KEY$)", re.IGNORECASE)
KNOWN_PREFIXES = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")
MASK = "«redacted»"


def secret_values(environ=None) -> list[str]:
    environ = os.environ if environ is None else environ
    values = {v for k, v in environ.items() if SECRET_KEY_NAMES.search(k) and len(v) >= 8}
    return sorted(values, key=len, reverse=True)


def redact(text: str, values: list[str]) -> str:
    for v in values:
        if v in text:
            text = text.replace(v, MASK)
    return KNOWN_PREFIXES.sub(MASK, text)
