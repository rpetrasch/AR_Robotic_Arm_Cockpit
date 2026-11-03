"""LLM providers (docs/specs/spec_cockpit.md §14.3): local Ollama, the Anthropic API, and a
scripted fake for CI and the e2e tests.

``chat()`` yields ``Piece``s: answer text and reasoning separately. Reasoning comes either as
Ollama's ``thinking`` field (gpt-oss, qwen3) or as ``<think>…</think>`` inside the text
(deepseek-r1); ``ThinkSplitter`` takes the tags out across chunk boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx2 as httpx

AI_CHECKS = "/setup/#group-AI"  # the AI checks of the checklist (§8.1)


@dataclass(frozen=True)
class Piece:
    kind: str  # "text" | "reasoning"
    text: str


class ProviderError(Exception):
    def __init__(self, message: str, hint: str = "", unavailable: bool = False):
        super().__init__(message)
        self.hint, self.unavailable = hint, unavailable


class ThinkSplitter:
    """Moves ``<think>…</think>`` out of a streamed text."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.inside, self.buf = False, ""

    def feed(self, text: str) -> list[Piece]:
        self.buf += text
        out: list[Piece] = []
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            kind = "reasoning" if self.inside else "text"
            i = self.buf.find(tag)
            if i >= 0:
                if i:
                    out.append(Piece(kind, self.buf[:i]))
                self.buf, self.inside = self.buf[i + len(tag) :], not self.inside
                continue
            keep = next((k for k in range(len(tag) - 1, 0, -1) if self.buf.endswith(tag[:k])), 0)
            if len(self.buf) > keep:
                out.append(Piece(kind, self.buf[: len(self.buf) - keep]))
            self.buf = self.buf[len(self.buf) - keep :]
            break
        return out

    def flush(self) -> list[Piece]:
        rest, self.buf = self.buf, ""
        return [Piece("reasoning" if self.inside else "text", rest)] if rest else []


# --- Ollama ---------------------------------------------------------------------------------


def ollama_base() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def _unreachable(base: str) -> ProviderError:
    return ProviderError(
        f"Ollama is not reachable at {base}.",
        "Start the Ollama app (or `ollama serve`); the AI checks under Install & Setup show the "
        "details.",
        unavailable=True,
    )


async def _ollama_error(res) -> ProviderError:
    try:
        body = json.loads(await res.aread())
        message = body.get("error") or f"HTTP {res.status_code}"
    except ValueError:
        message = f"HTTP {res.status_code}"
    if res.status_code == 404 or "not found" in message:
        return ProviderError(
            f"Ollama: {message}", "Pull the model in Settings → AI, or choose an installed one."
        )
    return ProviderError(f"Ollama: {message}")


class Ollama:
    name = "ollama"

    def __init__(self, base: str | None = None):
        self.base = base or ollama_base()

    async def chat(
        self, cfg: dict, system: str, messages: list[dict], hint: dict | None = None
    ) -> AsyncIterator[Piece]:
        body = {
            "model": cfg["model"],
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "keep_alive": "10m",
            "options": {
                "temperature": cfg["temperature"],
                "num_predict": cfg["max_tokens"],
                "num_ctx": cfg["num_ctx"],
            },
        }
        split = ThinkSplitter()
        timeout = httpx.Timeout(cfg["timeout_s"], connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.base}/api/chat", json=body) as res:
                    if res.status_code != 200:
                        raise await _ollama_error(res)
                    async for line in res.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise ProviderError(f"Ollama: {data['error']}")
                        msg = data.get("message") or {}
                        if msg.get("thinking"):
                            yield Piece("reasoning", msg["thinking"])
                        for piece in split.feed(msg.get("content") or ""):
                            yield piece
                        if data.get("done"):
                            break
        except httpx.ConnectError as exc:
            raise _unreachable(self.base) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Ollama did not answer within {cfg['timeout_s']} s.",
                "A large model on CPU is slow: raise the timeout or choose a smaller model.",
            ) from exc
        for piece in split.flush():
            yield piece

    async def embed(
        self, model: str, texts: list[str], timeout_s: float = 120
    ) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0)) as client:
                res = await client.post(
                    f"{self.base}/api/embed",
                    json={"model": model, "input": texts, "truncate": True},
                )
                if res.status_code != 200:
                    raise await _ollama_error(res)
                return res.json()["embeddings"]
        except httpx.ConnectError as exc:
            raise _unreachable(self.base) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Ollama embeddings timed out after {timeout_s} s") from exc

    async def models(self) -> list[dict]:
        """The installed models (``/api/tags``)."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                res = await client.get(f"{self.base}/api/tags")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise _unreachable(self.base) from exc
        if res.status_code != 200:
            raise await _ollama_error(res)
        out = []
        for m in res.json().get("models", []):
            details = m.get("details") or {}
            out.append(
                {
                    "name": m["name"],
                    "size": m.get("size", 0),
                    "family": details.get("family", ""),
                    "parameter_size": details.get("parameter_size", ""),
                    "quantization": details.get("quantization_level", ""),
                    "capabilities": m.get("capabilities") or [],
                    "modified": m.get("modified_at", ""),
                }
            )
        return sorted(out, key=lambda m: m["name"])


# --- Anthropic ------------------------------------------------------------------------------

# Models that take the server-side refusal fallback ``fallbacks: "default"``.
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Anthropic:
    """The Messages API, streaming. No ``temperature``: current models reject sampling
    parameters; thinking stays at the model's default (adaptive) and is not displayed."""

    name = "anthropic"

    def _client(self, cfg: dict):
        import anthropic

        return anthropic.AsyncAnthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=float(cfg["timeout_s"])
        )

    async def chat(
        self, cfg: dict, system: str, messages: list[dict], hint: dict | None = None
    ) -> AsyncIterator[Piece]:
        import anthropic

        kwargs: dict = {
            "model": cfg["model"],
            "max_tokens": cfg["max_tokens"],
            "system": system,
            "messages": messages,
        }
        if cfg["model"] in FALLBACK_MODELS:
            kwargs |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        client = self._client(cfg)
        try:
            async with client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield Piece("text", event.delta.text)
                        elif event.delta.type == "thinking_delta" and event.delta.thinking:
                            yield Piece("reasoning", event.delta.thinking)
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                raise ProviderError("The Anthropic API declined to answer this request.")
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                "Anthropic: the API key was rejected.", "Check ANTHROPIC_API_KEY in .env."
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(
                f"Anthropic: unknown model {cfg['model']!r}.",
                "Check the model id in Settings → AI.",
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("Anthropic: rate limited.", "Wait a minute and ask again.") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic: HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("Anthropic: the API is not reachable.", unavailable=True) from exc
        finally:
            await client.close()


# --- fake -----------------------------------------------------------------------------------

FAKE_DIM = 64


def fake_vector(text: str) -> list[float]:
    """A bag-of-words vector: texts that share words are close, so retrieval tests mean
    something without a model."""
    v = [0.0] * FAKE_DIM
    for word in re.findall(r"[a-z0-9_]{3,}", text.lower()):
        v[int(hashlib.md5(word.encode()).hexdigest(), 16) % FAKE_DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class Fake:
    """Scripted answers that exercise the UI: reasoning, citations, a proposed step, the three
    classes of "Explain warnings/errors", a scenario YAML."""

    name = "fake"
    DELAY_S = 0.01

    async def chat(
        self, cfg: dict, system: str, messages: list[dict], hint: dict | None = None
    ) -> AsyncIterator[Piece]:
        hint = hint or {}
        turn = messages[-1]["content"]
        asked = [ln for ln in turn.splitlines() if ln.startswith("Question: ")]
        question = (asked[-1] if asked else turn.splitlines()[-1]).removeprefix("Question: ")[:120]
        n = hint.get("sources", 0)
        cite = " [1]" if n else ""
        parts = [Piece("reasoning", "The user asks: " + question + ". I check the sources.")]
        task = cfg["task"]
        if task == "error_explain":
            lines = hint.get("lines") or []
            text = "### Harmless\n"
            text += (
                "".join(f"- `{ln}`: expected noise of this step{cite}.\n" for ln in lines[::3])
                or "- none\n"
            )
            text += "\n### Worth fixing\n"
            text += (
                "".join(f"- `{ln}`: a defect in this repo{cite}.\n" for ln in lines[1::3])
                or "- none\n"
            )
            text += "\n### Needs action\n"
            text += "".join(f"- `{ln}`: blocks you{cite}.\n" for ln in lines[2::3]) or "- none\n"
        elif task == "scenario_help":
            current = hint.get("yaml") or ""
            body = re.sub(
                r"(?m)^description:.*$", "description: Written with the assistant", current
            )
            if body == current:
                body = current.rstrip("\n") + "\ndescription: Written with the assistant\n"
            text = (
                f"Here is the scenario with a description{cite}:\n\n```yaml\n{body.rstrip()}\n```\n"
            )
        else:
            text = f"This is the fake assistant. You asked: {question}{cite}."
            if "build" in question.lower() or "fail" in question.lower():
                text += " Rebuild the workspace: [[step:ws.build]]"
            text += f"\n\nSources used: {n}."
        parts += [Piece("text", w) for w in re.findall(r"\S+\s*", text, re.S)]
        for p in parts:
            await asyncio.sleep(self.DELAY_S)
            yield p

    async def embed(
        self, model: str, texts: list[str], timeout_s: float = 120
    ) -> list[list[float]]:
        return [fake_vector(t) for t in texts]

    async def models(self) -> list[dict]:
        names = ["bge-m3:latest", "gemma3:4b", "gpt-oss:20b", "llama3.1:8b"]
        sizes = [1_157_000_000, 3_338_000_000, 13_793_000_000, 4_920_000_000]
        caps = [["embedding"], ["completion"], ["completion", "thinking"], ["completion", "tools"]]
        return [
            {
                "name": n,
                "size": s,
                "family": n.split(":")[0],
                "parameter_size": "",
                "quantization": "Q4_K_M",
                "capabilities": c,
                "modified": "",
            }
            for n, s, c in zip(names, sizes, caps, strict=True)
        ]


def for_chat(cfg: dict) -> tuple[object, dict, list[str]]:
    """The provider for a task config, with the config it really uses and notes for the footer.
    Anthropic falls back to Ollama without the flag or the key (§14.3)."""
    from .config import external_allowed, use_fake

    if use_fake():
        return Fake(), cfg, []
    if cfg["provider"] == "anthropic":
        if external_allowed():
            return Anthropic(), cfg, []
        model = os.environ.get("ASSISTANT_CHAT_MODEL") or "llama3.1:8b"
        note = (
            "Anthropic is not used: ASSISTANT_ALLOW_EXTERNAL=1 and ANTHROPIC_API_KEY are both "
            f"needed. Answered by Ollama ({model}) instead."
        )
        return Ollama(), {**cfg, "provider": "ollama", "model": model}, [note]
    return Ollama(), cfg, []


def for_embed():
    from .config import use_fake

    return Fake() if use_fake() else Ollama()
