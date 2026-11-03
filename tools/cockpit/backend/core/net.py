"""Network probes for checks and health (TCP connect, HTTP JSON), with a fake for fake mode."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class Net:
    async def tcp_open(self, host: str, port: int, timeout: float = 0.5) -> bool:
        try:
            _r, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True

    async def http_json(self, url: str, timeout: float = 3.0) -> tuple[int, Any]:
        """(status, parsed body); status 0 when the host cannot be reached."""

        def get():
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (config URL)
                    return resp.status, json.loads(resp.read() or b"null")
            except urllib.error.HTTPError as exc:
                return exc.code, None
            except (OSError, ValueError):
                return 0, None

        return await asyncio.to_thread(get)


@dataclass
class FakeNet(Net):
    """Ports in ``busy`` are open; ``ollama`` answers /api/tags when ``up``."""

    busy: set[int] = field(default_factory=set)
    ollama_up: bool = True
    ollama_models: list[str] = field(default_factory=list)

    async def tcp_open(self, host: str, port: int, timeout: float = 0.5) -> bool:
        return port in self.busy

    async def http_json(self, url: str, timeout: float = 3.0) -> tuple[int, Any]:
        if url.endswith("/api/tags"):
            if not self.ollama_up:
                return 0, None
            return 200, {"models": [{"name": m, "size": 4_000_000_000} for m in self.ollama_models]}
        return 0, None
