"""The Install & Setup checklist (docs/specs/spec_cockpit.md §8.1), shared by the web app and
``ar3ctl doctor``: both call ``run_checks``, so their results are the same."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from config import envfiles
from core import fake
from core.net import FakeNet, Net
from core.platform import detect, fake_host
from core.runner import CommandRunner

from .base import Check, CheckContext, CheckResult, run_all
from .pre import PRE_CHECKS
from .stack import STACK_CHECKS

ALL_CHECKS: tuple[Check, ...] = PRE_CHECKS + STACK_CHECKS
BY_ID = {c.id: c for c in ALL_CHECKS}
GROUPS = list(dict.fromkeys(c.group for c in ALL_CHECKS))  # display order


async def make_context(
    *,
    fake: bool,
    data_dir: Path,
    fake_platform: str = "macos",
    repo: Path = envfiles.REPO,
    env: Mapping[str, str] | None = None,
) -> CheckContext:
    env = os.environ if env is None else env
    if not fake:
        runner = CommandRunner()
        return CheckContext(runner, await detect(runner), repo, Net(), env)
    from core import fake as fake_world

    host = fake_host(fake_platform)
    runner = fake_world.FakeRunner.from_state(data_dir, repo, host.arch)
    state = fake_world.load_state(data_dir)
    net = FakeNet(
        busy=set(state["ports_busy"]),
        ollama_up=state["ollama"]["up"],
        ollama_models=list(state["ollama"]["models"]),
    )
    return CheckContext(runner, host, runner.repo, net, env, disk_free_gb_override=120.0)


async def run_checks(ctx: CheckContext, ids: list[str] | None = None) -> list[CheckResult]:
    checks = ALL_CHECKS if not ids else [BY_ID[i] for i in ids if i in BY_ID]
    return await run_all(checks, ctx)


def context_from_settings():
    """The context for the web app and the workers (Django settings)."""
    from django.conf import settings

    return make_context(
        fake=settings.FAKE,
        data_dir=settings.DATA_DIR,
        fake_platform=fake.platform(settings.DATA_DIR, settings.FAKE_PLATFORM),
        repo=settings.REPO_DIR,
    )
