"""Facts about the repository checkout that several parts of the cockpit need."""

from __future__ import annotations

import hashlib
from pathlib import Path

# Files whose content is baked into the dev/vnc images. The Makefile computes the same hash
# (DOCKERFILE_HASH) and stores it as the image label ar3.dockerfile_hash.
IMAGE_INPUTS = ("docker/Dockerfile", "docker/entrypoint.sh", "docker/vnc-start.sh")


def dockerfile_hash(repo: Path) -> str:
    h = hashlib.sha256()
    for rel in IMAGE_INPUTS:
        h.update((repo / rel).read_bytes())
    return h.hexdigest()[:16]
