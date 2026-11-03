"""The knowledge index (docs/specs/spec_cockpit.md §14.2).

Sources: the README, ``docs/**/*.md``, the package READMEs, ``**/ar3_firmware/*.md``,
``config/**/*.yaml`` and curated Markdown in ``tools/cockpit/knowledge/``. Paths listed in
``ASSISTANT_INDEX_EXCLUDE`` are left out (default: the owner's notes and the third-party driver
under ``src/external/``, whose AR4 facts must not answer AR3 questions).

Markdown is cut at headings (a section longer than ~800 tokens is cut again at paragraphs, with
~100 tokens of overlap); YAML at its top-level entries, so each component of the catalogue is
one chunk. Retrieval fuses Postgres full-text and pgvector cosine search with reciprocal-rank
fusion. Without an embedding model the index is full-text only ("degraded").

``refresh()`` re-indexes the files whose hash changed (all of them when the embedding model
changed). It runs when run_jobs starts and as the step ``assistant.index``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path

from asgiref.sync import async_to_sync
from django.db import connection
from django.utils import timezone

from config.envfiles import REPO
from core.redact import redact, secret_values

from .config import task_config
from .models import AppSetting
from .providers import ProviderError, for_embed

log = logging.getLogger("cockpit.assistant")

CHUNKER = "2"  # part of every file hash: a chunker change re-indexes everything
MAX_CHARS = 3200  # ~800 tokens
OVERLAP_CHARS = 400  # ~100 tokens
TOP_K = 8
CANDIDATES = 30
RRF_K = 60
EMBED_BATCH = 16
HNSW_MAX_DIM = 2000  # pgvector's HNSW limit; above it the (small) table is scanned
LOCK = 0x41523349  # "AR3I": one refresh at a time
META_KEY = "assistant.index"
INCLUDE = (
    "README.md",
    "docs/**/*.md",
    "src/*/README.md",
    "**/ar3_firmware/*.md",
    "config/**/*.yaml",
    "tools/cockpit/knowledge/**/*.md",
)
SKIP_DIRS = {".git", "node_modules", ".venv", "build", "install", "log", "out", ".e2e-data"}


def tokens(text: str) -> int:
    """A token estimate (~4 characters per token) for budgets; no tokenizer needed."""
    return max(1, len(text) // 4)


def excluded() -> list[str]:
    raw = os.environ.get("ASSISTANT_INDEX_EXCLUDE", "docs/notes/,src/external/")
    return [p.strip() for p in raw.split(",") if p.strip()]


def discover(repo: Path = REPO) -> dict[str, str]:
    """{repo-relative path: text} of every source file."""
    skip = excluded()
    out: dict[str, str] = {}
    for pattern in INCLUDE:
        for path in sorted(repo.glob(pattern)):
            rel = path.relative_to(repo).as_posix()
            if not path.is_file() or SKIP_DIRS & set(path.relative_to(repo).parts):
                continue
            if any(rel.startswith(p) or fnmatch(rel, p) for p in skip):
                continue
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
    return out


def kind_of(rel: str) -> str:
    if rel.startswith("tools/cockpit/knowledge/"):
        return "knowledge"
    return "config" if rel.endswith((".yaml", ".yml")) else "doc"


# --- chunking ----------------------------------------------------------------------------------

HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")


def _split_long(heading: str, body: str) -> list[tuple[str, str]]:
    """Cuts a long body at paragraph (or line) boundaries, each piece starting with the tail of
    the one before."""
    if len(body) <= MAX_CHARS:
        return [(heading, body)]
    paras = re.split(r"(\n\s*\n)", body)
    units: list[str] = []
    for p in paras:
        if len(p) > MAX_CHARS:  # one huge paragraph or table: by lines
            units += [ln + "\n" for ln in p.split("\n")]
        else:
            units.append(p)
    pieces, cur = [], ""
    for u in units:
        if cur and len(cur) + len(u) > MAX_CHARS:
            pieces.append(cur)
            tail = cur[-OVERLAP_CHARS:]
            cur = tail[tail.find("\n") + 1 :] if "\n" in tail else tail
        cur += u
    if cur.strip():
        pieces.append(cur)
    return [(heading if i == 0 else f"{heading} (cont. {i + 1})", p) for i, p in enumerate(pieces)]


def chunk_markdown(text: str, title: str) -> list[tuple[str, str]]:
    """[(heading path, text)]: one per section, headings ``H1 > H2 > H3``."""
    stack: list[str] = []
    sections: list[tuple[str, list[str]]] = [(title, [])]
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else HEADING.match(line)
        if m:
            level = len(m.group(1))
            stack = stack[: level - 1] + [m.group(2).strip()]
            sections.append((" > ".join(stack), []))
        else:
            sections[-1][1].append(line)
    out = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if body:
            out += _split_long(heading or title, body)
    return out


def chunk_yaml(text: str, title: str) -> list[tuple[str, str]]:
    """Top-level entries (``- id: x`` of a list, ``key:`` of a mapping); leading comments go
    with the entry below them. Small neighbours are packed together up to MAX_CHARS."""
    blocks: list[tuple[str, list[str]]] = []
    pending: list[str] = []  # comments and blank lines, until we know which entry they belong to
    for line in text.splitlines():
        if line[:1] not in ("", " ", "\t", "#") and not line.startswith("---"):
            name = line.lstrip("- ").strip().rstrip(":")
            blocks.append((name[:80], pending + [line]))
            pending = []
        elif line.startswith("#") or not line.strip() or not blocks:
            pending.append(line)
        else:  # an indented line continues the entry (with the comments inside it)
            blocks[-1][1].extend(pending + [line])
            pending = []
    if not blocks:
        return [(title, text.strip())] if text.strip() else []
    if pending and "".join(pending).strip():
        blocks[-1][1].extend(pending)
    out: list[tuple[str, str]] = []
    cur_names: list[str] = []
    cur = ""
    for name, lines in blocks:
        body = "\n".join(lines).strip("\n")
        if cur and len(cur) + len(body) > MAX_CHARS:
            out.append((f"{title}: {', '.join(cur_names)}"[:200], cur))
            cur, cur_names = "", []
        cur += ("\n" if cur else "") + body
        cur_names.append(name)
    if cur.strip():
        out.append((f"{title}: {', '.join(cur_names)}"[:200], cur))
    return [piece for h, b in out for piece in _split_long(h, b)]


def chunks_of(rel: str, text: str) -> list[tuple[str, str]]:
    title = rel
    if rel.endswith(".md"):
        m = re.search(r"(?m)^#\s+(.+)$", text)
        title = m.group(1).strip() if m else rel
        return chunk_markdown(text, title)
    return chunk_yaml(text, rel)


def file_hash(text: str) -> str:
    return hashlib.sha256(f"{CHUNKER}\n{text}".encode()).hexdigest()[:24]


# --- meta + vector column ------------------------------------------------------------------------


def meta() -> dict:
    row = AppSetting.objects.filter(key=META_KEY).first()
    return dict(row.value) if row else {}


def _save_meta(value: dict) -> None:
    AppSetting.objects.update_or_create(key=META_KEY, defaults={"value": value, "updated_by": ""})


def _set_dimension(cur, dim: int | None) -> None:
    """Empties the index and gives ``embedding`` the model's dimension (or none)."""
    cur.execute("DROP INDEX IF EXISTS kb_chunks_hnsw")
    cur.execute("TRUNCATE kb_files CASCADE")
    cur.execute(
        f"ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector{f'({dim})' if dim else ''}"
    )
    if dim and dim <= HNSW_MAX_DIM:
        cur.execute(
            "CREATE INDEX kb_chunks_hnsw ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
        )


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"


def _embed(texts: list[str], model: str) -> list[list[float]]:
    provider = for_embed()
    timeout = task_config("embed")["timeout_s"]
    return async_to_sync(provider.embed)(model, texts, timeout)


def _embed_text(heading: str, text: str) -> str:
    return f"{heading}\n{text}"[:8000]


# --- refresh -------------------------------------------------------------------------------------


def stale(repo: Path = REPO) -> bool:
    """Does a refresh have work: a file changed, appeared or went, or the embedding model?"""
    files = discover(repo)
    with connection.cursor() as cur:
        cur.execute("SELECT path, hash FROM kb_files")
        have = dict(cur.fetchall())
    m = meta()
    if {p: file_hash(t) for p, t in files.items()} != have:
        return True
    model = task_config("embed")["model"]
    return m.get("embed_model") != model or bool(m.get("degraded"))


def refresh(
    full: bool = False, log_line: Callable[[str], None] | None = None, repo: Path = REPO
) -> dict:
    """Brings the index up to date; returns the new meta. ``full``: re-index every file."""
    say = log_line or log.info
    started = time.monotonic()
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK])
        if not cur.fetchone()[0]:
            say("another index refresh is running")
            return meta()
    try:
        return _refresh(full, say, repo, started)
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK])


def _refresh(full: bool, say, repo: Path, started: float) -> dict:
    model = task_config("embed")["model"]
    old = meta()
    dim, error = None, ""
    try:
        dim = len(_embed(["dimension probe"], model)[0])
    except ProviderError as exc:
        error = str(exc)
        say(f"embeddings unavailable ({error}): full-text only")
    files = discover(repo)
    secrets = secret_values()
    with connection.cursor() as cur:
        if dim and (full or old.get("embed_model") != model or old.get("dim") != dim):
            say(f"embedding model {model}, dimension {dim}: indexing every file")
            _set_dimension(cur, dim)
        elif full:
            cur.execute("TRUNCATE kb_files CASCADE")
        cur.execute("SELECT path, hash FROM kb_files")
        have = dict(cur.fetchall())
        gone = [p for p in have if p not in files]
        if gone:
            cur.execute("DELETE FROM kb_files WHERE path = ANY(%s)", [gone])
            say(f"removed {len(gone)} file(s): {', '.join(gone[:5])}")
        changed = [p for p, t in files.items() if have.get(p) != file_hash(t)]
        for rel in changed:
            text = files[rel]
            pieces = [(h, redact(b, secrets)) for h, b in chunks_of(rel, text)]
            vectors: list[list[float] | None] = [None] * len(pieces)
            if dim:
                try:
                    for i in range(0, len(pieces), EMBED_BATCH):
                        batch = pieces[i : i + EMBED_BATCH]
                        vectors[i : i + len(batch)] = _embed(
                            [_embed_text(h, b) for h, b in batch], model
                        )
                except ProviderError as exc:
                    error, dim = str(exc), None
                    vectors = [None] * len(pieces)
                    say(f"embeddings failed ({error}): continuing full-text only")
            cur.execute("DELETE FROM kb_files WHERE path = %s", [rel])
            cur.execute(
                "INSERT INTO kb_files (path, hash, chunks, indexed) VALUES (%s, %s, %s, now())",
                [rel, file_hash(text), len(pieces)],
            )
            cur.executemany(
                "INSERT INTO kb_chunks (source, kind, heading, position, text, hash, tokens, "
                "embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                [
                    (
                        rel,
                        kind_of(rel),
                        h[:300],
                        i,
                        b,
                        hashlib.sha256(b.encode()).hexdigest()[:16],
                        tokens(b),
                        _vec(v) if v else None,
                    )
                    for i, ((h, b), v) in enumerate(zip(pieces, vectors, strict=True))
                ],
            )
            say(f"indexed {rel}: {len(pieces)} chunk(s)")
        if dim:  # chunks stored while the model was unavailable
            cur.execute("SELECT id, heading, text FROM kb_chunks WHERE embedding IS NULL")
            missing = cur.fetchall()
            for i in range(0, len(missing), EMBED_BATCH):
                batch = missing[i : i + EMBED_BATCH]
                vecs = _embed([_embed_text(h, t) for _id, h, t in batch], model)
                cur.executemany(
                    "UPDATE kb_chunks SET embedding = %s::vector WHERE id = %s",
                    [(_vec(v), row[0]) for v, row in zip(vecs, batch, strict=True)],
                )
            if missing:
                say(f"embedded {len(missing)} chunk(s) indexed without a model before")
        cur.execute("SELECT count(*), count(embedding) FROM kb_chunks")
        n_chunks, n_vec = cur.fetchone()
        cur.execute("SELECT count(*) FROM kb_files")
        n_files = cur.fetchone()[0]
    value = {
        "embed_model": model,
        "dim": dim or old.get("dim"),
        "files": n_files,
        "chunks": n_chunks,
        "embedded": n_vec,
        "degraded": n_vec < n_chunks or not dim,
        "error": error,
        "built": timezone.now().isoformat(),
        "duration_s": round(time.monotonic() - started, 1),
        "changed": len(changed),
    }
    _save_meta(value)
    say(
        f"index: {n_files} files, {n_chunks} chunks, {n_vec} with embeddings"
        f"{' (full-text only)' if value['degraded'] else ''}, {value['duration_s']} s"
    )
    return value


# --- search --------------------------------------------------------------------------------------


def _fulltext(cur, query: str, source: str | None = None) -> list[int]:
    # Any word may match (OR); ts_rank_cd ranks chunks with more and closer matches first.
    cur.execute(
        "WITH q AS (SELECT to_tsquery('english', "
        "replace(plainto_tsquery('english', %s)::text, ' & ', ' | ')) AS q) "
        "SELECT id FROM kb_chunks, q WHERE tsv @@ q.q AND (%s::text IS NULL OR source = %s) "
        "ORDER BY ts_rank_cd(tsv, q.q, 32) DESC, id LIMIT %s",
        [query[:2000], source, source, CANDIDATES],
    )
    return [r[0] for r in cur.fetchall()]


def _vector(cur, query: str, model: str, source: str | None = None) -> list[int]:
    v = _embed([query[:8000]], model)[0]
    cur.execute(
        "SELECT id FROM kb_chunks WHERE embedding IS NOT NULL "
        "AND (%s::text IS NULL OR source = %s) "
        "ORDER BY embedding <=> %s::vector, id LIMIT %s",
        [source, source, _vec(v), CANDIDATES],
    )
    return [r[0] for r in cur.fetchall()]


def search(query: str, k: int = TOP_K, source: str | None = None) -> tuple[list[dict], str]:
    """(top ``k`` chunks by reciprocal-rank fusion, "hybrid" | "full-text" | "empty").
    ``source``: only chunks of that file."""
    m = meta()
    with connection.cursor() as cur:
        fts = _fulltext(cur, query, source)
        vec: list[int] = []
        mode = "full-text"
        if m.get("embedded"):
            try:
                model = m.get("embed_model") or task_config("embed")["model"]
                vec = _vector(cur, query, model, source)
                mode = "hybrid"
            except ProviderError as exc:
                log.warning("vector search unavailable: %s", exc)
            except Exception as exc:  # a dimension mismatch after a model switch
                log.warning("vector search failed: %s", exc)
        score: dict[int, float] = {}
        for ranked in (fts, vec):
            for rank, cid in enumerate(ranked):
                score[cid] = score.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(score, key=lambda c: (-score[c], c))[:k]
        if not best:
            return [], "empty" if not m.get("chunks") else mode
        cur.execute(
            "SELECT id, source, kind, heading, text FROM kb_chunks WHERE id = ANY(%s)", [best]
        )
        rows = {r[0]: r for r in cur.fetchall()}
    out = []
    for cid in best:
        _id, source, kind, heading, text = rows[cid]
        out.append(
            {
                "id": cid,
                "source": source,
                "kind": kind,
                "heading": heading,
                "text": text,
                "score": round(score[cid], 5),
            }
        )
    return out, mode


def status() -> dict:
    m = meta()
    with connection.cursor() as cur:
        cur.execute("SELECT count(*), count(embedding) FROM kb_chunks")
        chunks, embedded = cur.fetchone()
        cur.execute("SELECT kind, count(*) FROM kb_chunks GROUP BY kind ORDER BY kind")
        by_kind = dict(cur.fetchall())
    return {
        **m,
        "chunks": chunks,
        "embedded": embedded,
        "by_kind": by_kind,
        "degraded": chunks == 0 or embedded < chunks,
        "excluded": excluded(),
    }
