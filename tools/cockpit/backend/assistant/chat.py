"""One question, start to end (docs/specs/spec_cockpit.md §14): retrieve, build the context pack,
stream the answer, store both turns.

``answer()`` yields the events the SSE view sends:

- ``meta``: provider, model, the numbered sources, notes (fallback, full-text only, truncation);
- ``token`` / ``reasoning``: pieces of the answer and of the hidden reasoning;
- ``done``: the stored assistant message (proposals, scenario diff, latency);
- ``error``: the provider failed (``unavailable``: link the AI checks).
"""

from __future__ import annotations

import asyncio
import difflib
import time
from collections.abc import AsyncIterator

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.redact import redact, secret_values

from . import context, index, prompts
from .config import task_config
from .models import Message, Thread
from .providers import AI_CHECKS, ProviderError, for_chat

HISTORY_MESSAGES = 6
HISTORY_TOKENS = 2000


def prune_threads(now=None) -> int:
    """Threads untouched for AI_LOG_DAYS (§14.1) go, with their messages."""
    from datetime import timedelta

    from django.conf import settings

    cutoff = (now or timezone.now()) - timedelta(days=settings.AI_LOG_DAYS)
    deleted, _ = Thread.objects.filter(updated__lt=cutoff).delete()
    return deleted


def thread_config(thread: Thread, task: str) -> dict:
    cfg = task_config(task)
    if thread.provider:
        cfg = {**cfg, "provider": thread.provider, "model": thread.model or cfg["model"]}
        if thread.provider == "anthropic" and not thread.model:
            from .config import DEFAULT_ANTHROPIC_MODEL

            cfg["model"] = DEFAULT_ANTHROPIC_MODEL
    return cfg


def retrieval_query(task: str, question: str, pack_hint: dict) -> str:
    parts = [question]
    for key in ("job_title", "check", "mode"):
        if pack_hint.get(key):
            parts.append(pack_hint[key])
    parts += pack_hint.get("lines", [])[:12]
    if task == "error_explain":
        parts.append("troubleshooting warning error")
    if task == "scenario_help":
        parts.append("scenario YAML schema steps objects success criteria")
    return "\n".join(parts)


def _hint(task: str, ctx: dict) -> dict:
    """What retrieval needs before the pack exists: the job's title and lines, the check."""
    from setup import service
    from setup.checks import BY_ID
    from setup.models import Job

    hint: dict = {}
    if ctx.get("job_id") and (job := Job.objects.filter(pk=ctx["job_id"]).first()):
        _last, distinct = context.job_lines(job, service.read_log(job))
        hint["job_title"] = f"{service.job_dict(job)['title']} ({job.step_id}): {job.command}"
        hint["lines"] = [d["text"] for d in distinct]
        hint["docs"] = sorted({m["docs"] for m in job.matches if m.get("docs")})
        hint["lines"] += [m["title"] for m in {m["id"]: m for m in job.matches}.values()]
    if ctx.get("check_id") in BY_ID:
        hint["check"] = BY_ID[ctx["check_id"]].title
    if ctx.get("mode_id"):
        hint["mode"] = f"mode {ctx['mode_id']}"
    if ctx.get("line"):
        hint.setdefault("lines", []).insert(0, ctx["line"])
    return hint


def _sources(task: str, question: str, hint: dict) -> tuple[list[dict], str]:
    query = retrieval_query(task, question, hint)
    found, mode = index.search(query)
    # the doc an error pattern points to (e.g. troubleshooting.md): its best section comes first
    named = [hit for d in hint.get("docs", []) for hit in index.search(query, 1, source=d)[0]]
    seen = {h["id"] for h in named}
    found = named + [h for h in found if h["id"] not in seen]
    files = context.file_sources(hint.get("lines", []))
    if task == "scenario_help":
        files.insert(0, _schema_source())
    # code the log names ranks right after the best documentation hits
    return found[:4] + files + found[4:], mode


def _schema_source() -> dict:
    import json

    from scenarios.validate import schema

    return {
        "source": "tools/cockpit/backend/scenarios/scenario.schema.json",
        "heading": "schema",
        "kind": "config",
        "text": json.dumps(schema(), separators=(",", ":")),
    }


def _history(thread: Thread) -> list[dict]:
    """Earlier turns, newest kept: questions and answers, never reasoning (§14.3)."""
    rows = list(thread.messages.exclude(content="").order_by("-created", "-id")[:HISTORY_MESSAGES])
    out, used = [], 0
    for m in rows:
        used += index.tokens(m.content)
        if used > HISTORY_TOKENS:
            break
        out.append({"role": m.role, "content": m.content})
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _scenario_extra(answer: str, pack: dict) -> dict:
    from scenarios.validate import validate

    new = prompts.yaml_block(answer)
    if new is None:
        return {"scenario": {"yaml": None, "error": "the answer has no ```yaml block"}}
    current = (pack.get("scenario") or {}).get("yaml", "")
    result = validate(new)
    diff = "".join(
        difflib.unified_diff(current.splitlines(True), new.splitlines(True), "current", "assistant")
    )
    return {
        "scenario": {
            "yaml": new,
            "diff": diff,
            "ok": result["ok"],
            "errors": result["errors"],
            "warnings": result["warnings"],
        }
    }


def message_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "task": m.task,
        "content": m.content,
        "reasoning": m.reasoning,
        "context": m.context,
        "sources": m.sources,
        "proposals": m.proposals,
        "extra": m.extra,
        "provider": m.provider,
        "model": m.model,
        "notes": m.notes,
        "latency_ms": m.latency_ms,
        "error": m.error,
        "created": m.created.isoformat(),
    }


def thread_dict(t: Thread, messages: bool = False) -> dict:
    d = {
        "id": str(t.id),
        "title": t.title,
        "provider": t.provider,
        "model": t.model,
        "created": t.created.isoformat(),
        "updated": t.updated.isoformat(),
    }
    if messages:
        d["messages"] = [message_dict(m) for m in t.messages.all()]
    return d


def _store_question(thread: Thread, task: str, question: str, ctx: dict) -> Message:
    if not thread.title:
        thread.title = question.splitlines()[0][:80] if question else task
    thread.save()  # bumps `updated`
    return Message.objects.create(
        thread=thread, role=Message.USER, task=task, content=question, context=ctx
    )


async def answer(thread: Thread, question: str, task: str, ctx: dict) -> AsyncIterator[dict]:
    secrets = await sync_to_async(secret_values)()
    question = redact(question, secrets)
    ctx = {k: redact(v, secrets) for k, v in ctx.items()}
    cfg = await sync_to_async(thread_config)(thread, task)
    provider, cfg, notes = for_chat(cfg)
    await sync_to_async(_store_question)(thread, task, question, ctx)
    hint = await sync_to_async(_hint)(task, ctx)
    sources, mode = await sync_to_async(_sources)(task, question, hint)
    if mode == "full-text":
        notes.append("Knowledge index: full-text only (no embedding model).")
    elif mode == "empty":
        notes.append("The knowledge index is empty: rebuild it in Settings → AI.")
    pack = await sync_to_async(context.build)(task, ctx, sources)
    notes += pack.get("truncated", [])
    history = await sync_to_async(_history)(thread)
    if history and history[-1]["role"] == "user":
        history.pop()  # the question just stored; it is sent below with its pack
    messages = [*history, {"role": "user", "content": prompts.user_turn(question, pack)}]
    cited = [{k: s[k] for k in ("n", "source", "heading", "kind")} for s in pack["sources"]]
    provider_name = getattr(provider, "name", cfg["provider"])
    yield {
        "event": "meta",
        "provider": provider_name,
        "model": cfg["model"],
        "task": task,
        "sources": cited,
        "notes": notes,
        "retrieval": mode,
    }

    text, reasoning, error, hint_text, unavailable = [], [], "", "", False
    started = time.monotonic()
    first_token_ms = None
    try:
        lines = [d["text"] for d in (pack.get("job") or {}).get("distinct_lines", [])]
        fake_hint = {
            "sources": len(cited),
            "lines": lines,
            "yaml": (pack.get("scenario") or {}).get("yaml", ""),
        }
        async for piece in provider.chat(cfg, prompts.system_prompt(task), messages, fake_hint):
            if piece.kind == "text":
                if first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                text.append(piece.text)
                yield {"event": "token", "text": piece.text}
            else:
                reasoning.append(piece.text)
                yield {"event": "reasoning", "text": piece.text}
    except ProviderError as exc:
        error, hint_text, unavailable = str(exc), exc.hint, exc.unavailable
    except asyncio.CancelledError:
        error = "stopped"
        await asyncio.shield(
            _save(
                thread,
                task,
                text,
                reasoning,
                pack,
                cited,
                provider_name,
                cfg,
                notes,
                started,
                error,
            )
        )
        raise
    msg = await _save(
        thread,
        task,
        text,
        reasoning,
        pack,
        cited,
        provider_name,
        cfg,
        notes,
        started,
        error,
        {"hint": hint_text, "unavailable": unavailable},
    )
    if error:
        yield {
            "event": "error",
            "message": error,
            "hint": hint_text,
            "unavailable": unavailable,
            "docs": AI_CHECKS,
            "id": msg["id"],
        }
        return
    yield {"event": "done", "message": msg, "first_token_ms": first_token_ms}


async def _save(
    thread,
    task,
    text,
    reasoning,
    pack,
    cited,
    provider_name,
    cfg,
    notes,
    started,
    error,
    error_info: dict | None = None,
) -> dict:
    content = "".join(text).strip()

    def save() -> dict:
        extra = (
            _scenario_extra(content, pack)
            if task == "scenario_help" and content and not error
            else {}
        )
        if error and error_info:
            extra["error"] = error_info  # hint and the link to the AI checks, for the drawer
        m = Message.objects.create(
            thread=thread,
            role=Message.ASSISTANT,
            task=task,
            content=content,
            reasoning="".join(reasoning).strip(),
            context_pack=pack,
            sources=cited,
            proposals=prompts.proposals(content),
            extra=extra,
            provider=provider_name,
            model=cfg["model"],
            notes=notes,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=error,
        )
        Thread.objects.filter(pk=thread.pk).update(updated=timezone.now())
        return message_dict(m)

    return await sync_to_async(save)()
