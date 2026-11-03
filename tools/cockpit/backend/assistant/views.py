"""Assistant and AI settings API (docs/specs/spec_cockpit.md §14, §15).

REST views for threads, the index and the per-task model settings; the answer itself streams
as server-sent events from an async view (``POST /api/assistant/threads/{id}/messages``), so it
is not part of the OpenAPI schema (lib/api/assistant.ts is written by hand).
"""

from __future__ import annotations

import json
import os
import time
from contextlib import aclosing

from asgiref.sync import async_to_sync, sync_to_async
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from accounts import audit
from core.problems import Problem

from . import chat, config, index, providers
from .context import clean_context
from .models import Thread

TASKS = ("chat", "error_explain", "scenario_help")
MAX_QUESTION = 8000
TEST_PROMPT = "Reply with one short sentence: you are ready to help with the AR3 robot arm."


def _rate_limit() -> int:
    try:
        return max(int(os.environ.get("ASSISTANT_RATE_PER_MINUTE", "20")), 1)
    except ValueError:
        return 20


def _thread(request, thread_id) -> Thread:
    t = Thread.objects.filter(pk=thread_id, user=request.user.username).first()
    if t is None:
        raise Problem(404, "Not found", "no such thread")
    return t


def _provider_fields(data: dict) -> dict:
    out = {}
    if "provider" in data:
        p = data["provider"] or ""
        if p not in ("", *config.PROVIDERS):
            raise Problem(400, "Invalid request", f"provider must be one of {config.PROVIDERS}")
        out["provider"] = p
    if "model" in data:
        m = (data["model"] or "").strip()
        if len(m) > 200:
            raise Problem(400, "Invalid request", "model name too long")
        out["model"] = m
    return out


@extend_schema(
    methods=["GET"], operation_id="assistant_threads_list", responses={200: OpenApiTypes.OBJECT}
)
@extend_schema(methods=["POST"], request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
@api_view(["GET", "POST"])
def threads_view(request: Request) -> Response:
    if request.method == "POST":
        fields = _provider_fields(request.data)
        t = Thread.objects.create(
            user=request.user.username, title=str(request.data.get("title") or "")[:200], **fields
        )
        return Response(chat.thread_dict(t, messages=True), status=status.HTTP_201_CREATED)
    rows = Thread.objects.filter(user=request.user.username)[:100]
    return Response([chat.thread_dict(t) for t in rows])


@extend_schema(
    methods=["GET"], operation_id="assistant_thread_get", responses={200: OpenApiTypes.OBJECT}
)
@extend_schema(methods=["PATCH"], request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["DELETE"], responses={204: None})
@api_view(["GET", "PATCH", "DELETE"])
def thread_detail(request: Request, thread_id) -> Response:
    t = _thread(request, thread_id)
    if request.method == "DELETE":
        t.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        for k, v in _provider_fields(request.data).items():
            setattr(t, k, v)
        if "title" in request.data:
            t.title = str(request.data["title"] or "")[:200]
        t.save()
    return Response(chat.thread_dict(t, messages=True))


# --- the answer (SSE) ---------------------------------------------------------------------------


def _problem(status_code: int, title: str, detail: str, **extra) -> JsonResponse:
    body = {"type": "about:blank", "title": title, "status": status_code, "detail": detail, **extra}
    return JsonResponse(body, status=status_code, content_type="application/problem+json")


def _sse(event: dict) -> bytes:
    name = event.pop("event")
    return f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _allowed(username: str) -> bool:
    key = f"ai-rate:{username}:{int(time.time() // 60)}"
    cache.add(key, 0, 120)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, 120)
        count = 1
    return count <= _rate_limit()


async def messages_view(request, thread_id):
    """Asks a question in a thread; the answer streams as SSE (``meta``, ``token``,
    ``reasoning``, then ``done`` or ``error``). Body: ``{content, task?, context?}``."""
    if request.method != "POST":
        return _problem(405, "Method not allowed", "POST a question")
    user = await request.auser()
    if not user.is_authenticated:
        return _problem(401, "Not signed in", "sign in first", docs="docs/manual/cockpit.md")
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _problem(400, "Invalid request", "the body is not JSON")
    content = str(body.get("content") or "").strip()
    task = body.get("task") or "chat"
    if task not in TASKS:
        return _problem(400, "Invalid request", f"task must be one of {', '.join(TASKS)}")
    if not content:
        return _problem(400, "Invalid request", "content is empty")
    if len(content) > MAX_QUESTION:
        return _problem(400, "Invalid request", f"at most {MAX_QUESTION} characters")
    thread = await Thread.objects.filter(pk=thread_id, user=user.username).afirst()
    if thread is None:
        return _problem(404, "Not found", "no such thread")
    if not await sync_to_async(_allowed)(user.username):
        return _problem(
            429,
            "Too many requests",
            f"at most {_rate_limit()} questions per minute (ASSISTANT_RATE_PER_MINUTE)",
            retry_after_s=60,
        )
    ctx = clean_context(body.get("context"))

    async def stream():
        yield b": stream open\n\n"
        async for event in chat.answer(thread, content, task, ctx):
            yield _sse(event)

    resp = StreamingHttpResponse(stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


# --- index ---------------------------------------------------------------------------------------


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def index_status(request: Request) -> Response:
    return Response(index.status())


@extend_schema(request=OpenApiTypes.OBJECT, responses={201: OpenApiTypes.OBJECT})
@api_view(["POST"])
def index_rebuild(request: Request) -> Response:
    """Starts the step ``assistant.index`` (a job with a log); ``{"full": true}`` re-embeds all."""
    from setup import service
    from setup.views import _start

    full = "1" if request.data.get("full", True) else "0"
    job = _start(request, "assistant.index", {"full": full})
    return Response(service.job_dict(job), status=status.HTTP_201_CREATED)


# --- AI settings ---------------------------------------------------------------------------------


def _memory() -> dict:
    import psutil

    from monitoring import metrics

    gpus = (cache.get(metrics.CACHE_KEY) or {}).get("gpus") or []
    return {
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "gpus": [
            {"name": g.get("name"), "mem_gb": round((g.get("mem_total_mb") or 0) / 1000, 1)}
            for g in gpus
        ],
    }


def hardware_hint(size_bytes: int, memory: dict, host_os: str) -> dict:
    """Informational (§14.3): does a model of this size fit the GPU or the memory?"""
    need = round(size_bytes / 1e9 * 1.2 + 0.5, 1)  # weights + context + runtime
    gpu = max((g["mem_gb"] for g in memory["gpus"]), default=0)
    if host_os == "macos":  # unified memory: about 3/4 is available to the GPU
        usable = memory["ram_gb"] * 0.75
        if need <= usable:
            return {"need_gb": need, "fits": True, "text": f"~{need} GB, fits this Mac"}
        return {
            "need_gb": need,
            "fits": False,
            "text": f"~{need} GB: more than this Mac's ~{usable:.0f} GB for models",
        }
    if gpu:
        if need <= gpu:
            return {"need_gb": need, "fits": True, "text": f"~{need} GB, fits the {gpu} GB GPU"}
        return {
            "need_gb": need,
            "fits": False,
            "text": f"~{need} GB: does not fit the {gpu} GB GPU, runs on the CPU (slowly)",
        }
    fits = need <= memory["ram_gb"] * 0.8
    return {
        "need_gb": need,
        "fits": fits,
        "text": f"~{need} GB on the CPU{'' if fits else ': more than the RAM'} (no GPU found)",
    }


def _models_payload() -> dict:
    from core import platform

    memory = _memory()
    host_os = async_to_sync(platform.current_host)().os
    provider = providers.Fake() if config.use_fake() else providers.Ollama()
    error, installed = "", []
    try:
        installed = async_to_sync(provider.models)()
    except providers.ProviderError as exc:
        error = str(exc)
    for m in installed:
        m["hint"] = hardware_hint(m["size"], memory, host_os)
    return {
        "ollama": {
            "base_url": providers.ollama_base(),
            "up": not error,
            "error": error,
            "models": installed,
        },
        "anthropic": {
            "allowed": config.external_allowed(),
            "flag": os.environ.get("ASSISTANT_ALLOW_EXTERNAL", "0"),
            "key": "set" if os.environ.get("ANTHROPIC_API_KEY") else "not set",
            "default_model": os.environ.get("ANTHROPIC_MODEL") or config.DEFAULT_ANTHROPIC_MODEL,
        },
        "memory": memory,
        "fake": config.use_fake(),
        "tasks": {t: config.task_config(t) for t in config.TASKS},
    }


@extend_schema(responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
def ai_models(request: Request) -> Response:
    """Installed Ollama models with a hardware hint, the Anthropic switch, the task settings."""
    return Response(_models_payload())


@extend_schema(methods=["GET"], operation_id="ai_task_get", responses={200: OpenApiTypes.OBJECT})
@extend_schema(methods=["PUT"], request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
@api_view(["GET", "PUT"])
def ai_task(request: Request, task: str) -> Response:
    if task not in config.TASKS:
        raise Problem(404, "Not found", f"no task {task!r}")
    if request.method == "PUT":
        try:
            cfg = config.save(task, dict(request.data), request.user.username)
        except config.ConfigError as exc:
            raise Problem(400, "Invalid settings", str(exc)) from exc
        audit.record_request(
            request, "settings.ai", f"task:{task}", {k: v for k, v in request.data.items()}
        )
        return Response(cfg)
    return Response(config.task_config(task))


async def _test(cfg: dict) -> dict:
    started = time.monotonic()
    if cfg["task"] == "embed":
        provider = providers.for_embed()
        vec = await provider.embed(cfg["model"], ["test"], cfg["timeout_s"])
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "text": f"{len(vec[0])}-dimensional embedding",
            "provider": provider.name,
            "model": cfg["model"],
            "notes": [],
        }
    provider, cfg, notes = providers.for_chat({**cfg, "max_tokens": min(cfg["max_tokens"], 256)})
    text, first = "", None
    pieces = provider.chat(cfg, "Answer briefly.", [{"role": "user", "content": TEST_PROMPT}])
    async with aclosing(pieces):
        async for piece in pieces:
            if piece.kind == "text":
                first = first or int((time.monotonic() - started) * 1000)
                text += piece.text
                if len(text) > 200:
                    break
    return {
        "ok": True,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "first_token_ms": first,
        "text": text.strip()[:200],
        "provider": provider.name,
        "model": cfg["model"],
        "notes": notes,
    }


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
def ai_task_test(request: Request, task: str) -> Response:
    """A short prompt with the task's provider and model: latency and the first tokens."""
    if task not in config.TASKS:
        raise Problem(404, "Not found", f"no task {task!r}")
    cfg = config.task_config(task)
    try:
        return Response(async_to_sync(_test)(cfg))
    except providers.ProviderError as exc:
        return Response(
            {
                "ok": False,
                "error": str(exc),
                "hint": exc.hint,
                "provider": cfg["provider"],
                "model": cfg["model"],
            }
        )
