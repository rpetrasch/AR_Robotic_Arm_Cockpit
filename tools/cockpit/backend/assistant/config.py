"""Provider, model and parameters per task (docs/specs/spec_cockpit.md §13, §14.3).

Each field resolves in this order, the first one that has a value wins:

1. the process environment or ``<repo>/.env`` (only fields with an env key, e.g. the chat model);
2. what Settings → AI stored in ``app_settings`` (key ``ai.task.<task>``);
3. ``defaults.env``;
4. the built-in default below.

Nothing is cached: a model chosen in the UI applies to the next question, without a restart.
"""

from __future__ import annotations

import os

from config import envfiles

from .models import AppSetting

TASKS = ("chat", "error_explain", "scenario_help", "embed")
PROVIDERS = ("ollama", "anthropic")
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"

# Built-in parameters per task; the UI can change all of them.
BUILTIN = {
    "chat": {"temperature": 0.2, "max_tokens": 2048, "num_ctx": 16384, "timeout_s": 180},
    "error_explain": {"temperature": 0.1, "max_tokens": 3072, "num_ctx": 16384, "timeout_s": 300},
    "scenario_help": {"temperature": 0.2, "max_tokens": 4096, "num_ctx": 16384, "timeout_s": 300},
    "embed": {"temperature": 0.0, "max_tokens": 0, "num_ctx": 8192, "timeout_s": 120},
}
LIMITS = {
    "temperature": (0.0, 2.0),
    "max_tokens": (64, 64000),
    "num_ctx": (1024, 262144),
    "timeout_s": (5, 1800),
}


class ConfigError(ValueError):
    pass


def _stored(task: str) -> dict:
    row = AppSetting.objects.filter(key=f"ai.task.{task}").first()
    return dict(row.value) if row else {}


def _env_field(key: str, stored, builtin) -> tuple[object, str]:
    """(value, source) for a field that also has an env key."""
    source = envfiles.source_of(key)
    if source in ("env", ".env") and os.environ.get(key):
        return os.environ[key], source
    if stored is not None:
        return stored, "settings"
    if os.environ.get(key):
        return os.environ[key], "defaults.env"
    return builtin, "default"


def task_config(task: str) -> dict:
    """The effective settings of a task: ``{provider, model, temperature, max_tokens, num_ctx,
    timeout_s, sources: {field: env|.env|settings|defaults.env|default}}``."""
    if task not in TASKS:
        raise ConfigError(f"unknown task {task!r}")
    stored = _stored(task)
    sources: dict[str, str] = {}
    if task == "embed":  # embeddings are local: Ollama only (§14.2)
        provider, sources["provider"] = "ollama", "default"
    else:
        provider, sources["provider"] = _env_field(
            "ASSISTANT_PROVIDER", stored.get("provider"), "ollama"
        )
    if provider == "anthropic":
        model, sources["model"] = _env_field(
            "ANTHROPIC_MODEL", stored.get("model"), DEFAULT_ANTHROPIC_MODEL
        )
    else:
        key = "ASSISTANT_EMBED_MODEL" if task == "embed" else "ASSISTANT_CHAT_MODEL"
        builtin = "bge-m3" if task == "embed" else "llama3.1:8b"
        # error_explain / scenario_help use the chat model unless they have their own
        if task in ("error_explain", "scenario_help") and stored.get("model") is None:
            model, sources["model"] = _env_field(key, _stored("chat").get("model"), builtin)
        else:
            model, sources["model"] = _env_field(key, stored.get("model"), builtin)
    cfg = {"task": task, "provider": provider, "model": model}
    for field, default in BUILTIN[task].items():
        if field in stored:
            cfg[field], sources[field] = stored[field], "settings"
        else:
            cfg[field], sources[field] = default, "default"
    cfg["sources"] = sources
    return cfg


def clean(task: str, values: dict) -> dict:
    """Validates a PUT body; returns the fields to store (``None`` removes a stored field)."""
    if task not in TASKS:
        raise ConfigError(f"unknown task {task!r}")
    out: dict = {}
    for field, value in values.items():
        if field == "provider":
            if task == "embed" and value not in (None, "ollama"):
                raise ConfigError("embeddings use Ollama")
            if value is not None and value not in PROVIDERS:
                raise ConfigError(f"provider must be one of {', '.join(PROVIDERS)}")
        elif field == "model":
            if value is not None and (
                not isinstance(value, str) or not value.strip() or len(value) > 200
            ):
                raise ConfigError("model must be a non-empty name")
            value = value.strip() if isinstance(value, str) else value
        elif field in LIMITS:
            if value is not None:
                lo, hi = LIMITS[field]
                try:
                    value = float(value) if field == "temperature" else int(value)
                except (TypeError, ValueError) as exc:
                    raise ConfigError(f"{field} must be a number") from exc
                if not lo <= value <= hi:
                    raise ConfigError(f"{field} must be between {lo} and {hi}")
        else:
            raise ConfigError(f"unknown field {field!r}")
        out[field] = value
    return out


def save(task: str, values: dict, user: str) -> dict:
    changes = clean(task, values)
    row, _ = AppSetting.objects.get_or_create(key=f"ai.task.{task}", defaults={"value": {}})
    merged = dict(row.value)
    for field, value in changes.items():
        if value is None:
            merged.pop(field, None)
        else:
            merged[field] = value
    row.value, row.updated_by = merged, user
    row.save()
    return task_config(task)


def external_allowed() -> bool:
    """Anthropic only with ASSISTANT_ALLOW_EXTERNAL=1 and a key (§14.3)."""
    return envfiles.flag("ASSISTANT_ALLOW_EXTERNAL") and bool(os.environ.get("ANTHROPIC_API_KEY"))


def use_fake() -> bool:
    """The scripted provider: in fake mode (CI, e2e), unless ASSISTANT_FAKE=0 asks for the real
    providers (the real-Ollama acceptance test runs the fake stack with a real model)."""
    from django.conf import settings

    value = os.environ.get("ASSISTANT_FAKE", "").strip()
    if value:
        return value in envfiles.TRUE
    return bool(settings.FAKE)
