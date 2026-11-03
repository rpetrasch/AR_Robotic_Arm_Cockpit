"""The assistant (docs/specs/spec_cockpit.md §14): index, providers, context pack, answers, AI
settings.

The answers come from the scripted provider (``providers.Fake``) and the embeddings from its
bag-of-words vectors, so retrieval, the context pack and the stream run for real without a model.
"""

import json
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncClient
from django.utils import timezone

from accounts.models import AuditEntry
from assistant import chat, config, context, index, prompts, providers
from assistant.models import AppSetting, Message, Thread
from assistant.views import hardware_hint
from core import fake
from setup import executor, service
from setup.models import Job

pytestmark = pytest.mark.django_db

SECRET = "sk-ant-api03-thisisnotarealkeybutlookslikeone"


@pytest.fixture(autouse=True)
async def _fresh_rate_limit_and_closed_connections():
    """The rate limit counts per minute in the cache: every test starts at 0. Afterwards the
    connection of asgiref's thread-sensitive worker thread is closed, or the test database
    cannot be dropped at the end of the run."""
    from django.core.cache import cache
    from django.db import connections

    cache.clear()
    yield
    await sync_to_async(connections.close_all)()


@pytest.fixture
def fake_ai(monkeypatch):
    monkeypatch.setenv("ASSISTANT_FAKE", "1")
    monkeypatch.setattr(providers.Fake, "DELAY_S", 0)


@pytest.fixture
def real_ai(monkeypatch):
    monkeypatch.setenv("ASSISTANT_FAKE", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ASSISTANT_ALLOW_EXTERNAL", "0")


@pytest.fixture
def kb(fake_ai):
    """The repo's real docs, indexed with fake embeddings. The kb tables are no Django models,
    so a transactional test's flush leaves them: empty them here."""
    from django.db import connection

    yield index.refresh(full=True, log_line=lambda s: None)
    with connection.cursor() as cur:
        cur.execute("TRUNCATE kb_files CASCADE")


def write_log(job: Job, lines: list[tuple[str, str]]) -> None:
    with open(job.log_path, "w") as fh:
        for i, (stream, text) in enumerate(lines, 1):
            fh.write(f"{i}\t2026-11-03T10:00:{i % 60:02d}\t{stream}\t{text}\n")


# --- chunking ---------------------------------------------------------------------------------


def test_markdown_chunks_follow_headings_not_code():
    text = (
        "# Title\nintro\n## Install\nstep one\n"
        "```bash\n# not a heading\nmake build\n```\n### Mac\nbrew\n"
    )
    chunks = index.chunk_markdown(text, "Title")
    assert [h for h, _ in chunks] == ["Title", "Title > Install", "Title > Install > Mac"]
    assert "# not a heading" in chunks[1][1]


def test_long_sections_are_cut_with_overlap():
    body = "\n\n".join(f"paragraph {i} " + "word " * 120 for i in range(12))
    chunks = index.chunk_markdown(f"# T\n{body}", "T")
    assert len(chunks) > 1 and all(len(b) <= index.MAX_CHARS + 700 for _, b in chunks)
    assert chunks[1][0] == "T (cont. 2)"
    tail = chunks[0][1][-200:].strip().split()[-1]
    assert tail in chunks[1][1][: index.OVERLAP_CHARS + 50]  # the next piece repeats the tail


def test_yaml_chunks_are_top_level_entries():
    text = (
        "# catalogue\n- id: gazebo\n  name: Gazebo\n\n# the planner\n- id: move_group\n  name: x\n"
    )
    chunks = index.chunk_yaml(text * 1, "config/c.yaml")
    assert len(chunks) == 1 and "id: gazebo, id: move_group" in chunks[0][0]
    big = "\n".join(f"- id: c{i}\n  note: {'x' * 900}" for i in range(8))
    assert len(index.chunk_yaml(big, "c.yaml")) > 1


def test_sources_and_exclusions(tmp_path, monkeypatch):
    for rel in (
        "README.md",
        "docs/manual/a.md",
        "docs/notes/private.md",
        "src/pkg/README.md",
        "src/external/drv/README.md",
        "config/robot.yaml",
        "tools/cockpit/knowledge/ros.md",
        "docs/manual/node_modules/x.md",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("# x\ny\n")
    monkeypatch.delenv("ASSISTANT_INDEX_EXCLUDE", raising=False)
    found = set(index.discover(tmp_path))
    assert found == {
        "README.md",
        "docs/manual/a.md",
        "src/pkg/README.md",
        "config/robot.yaml",
        "tools/cockpit/knowledge/ros.md",
    }
    assert index.kind_of("tools/cockpit/knowledge/ros.md") == "knowledge"


# --- providers ----------------------------------------------------------------------------------


def test_think_tags_split_across_chunks():
    s = providers.ThinkSplitter()
    out = []
    for part in ["Hi <th", "ink>secret pl", "an</thi", "nk> answer", " <"]:
        out += s.feed(part)
    out += s.flush()
    text = "".join(p.text for p in out if p.kind == "text")
    reasoning = "".join(p.text for p in out if p.kind == "reasoning")
    assert (text, reasoning) == ("Hi  answer <", "secret plan")


def test_anthropic_only_with_the_flag_and_a_key(real_ai, monkeypatch):
    cfg = {**config.task_config("chat"), "provider": "anthropic", "model": "claude-sonnet-5-5"}
    p, used, notes = providers.for_chat(cfg)
    assert (
        p.name == "ollama"
        and used["provider"] == "ollama"
        and "ASSISTANT_ALLOW_EXTERNAL" in notes[0]
    )
    monkeypatch.setenv("ASSISTANT_ALLOW_EXTERNAL", "1")
    assert providers.for_chat(cfg)[0].name == "ollama"  # still no key
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    p, used, notes = providers.for_chat(cfg)
    assert (p.name, used["model"], notes) == ("anthropic", "claude-sonnet-5-5", [])


# --- settings -----------------------------------------------------------------------------------


def test_task_settings_precedence(monkeypatch):
    from config import envfiles

    monkeypatch.setenv("ASSISTANT_CHAT_MODEL", "llama3.1:8b")
    monkeypatch.setattr(envfiles, "source_of", lambda key, *a, **k: "defaults.env")
    assert config.task_config("chat")["model"] == "llama3.1:8b"
    config.save("chat", {"model": "gemma3:4b", "temperature": 0.5}, "alice")
    cfg = config.task_config("chat")
    assert (cfg["model"], cfg["temperature"], cfg["sources"]["model"]) == (
        "gemma3:4b",
        0.5,
        "settings",
    )
    # error_explain follows the chat model until it has its own
    assert config.task_config("error_explain")["model"] == "gemma3:4b"
    monkeypatch.setattr(envfiles, "source_of", lambda key, *a, **k: ".env")
    assert config.task_config("chat")["sources"]["model"] == ".env"  # .env wins over the UI
    with pytest.raises(config.ConfigError):
        config.clean("chat", {"temperature": 9})
    with pytest.raises(config.ConfigError):
        config.clean("embed", {"provider": "anthropic"})


def test_switching_the_model_applies_without_restart(client_in, monkeypatch):
    from config import envfiles

    monkeypatch.setattr(envfiles, "source_of", lambda key, *a, **k: "defaults.env")
    for model in ("llama3.1:8b", "gemma3:4b", "gpt-oss:20b"):
        res = client_in.put("/api/ai/tasks/chat", {"model": model}, content_type="application/json")
        assert res.status_code == 200
        assert chat.thread_config(Thread(user="alice"), "chat")["model"] == model
    assert AuditEntry.objects.filter(action="settings.ai", obj="task:chat").count() == 3
    bad = client_in.put("/api/ai/tasks/chat", {"num_ctx": 10}, content_type="application/json")
    assert bad.status_code == 400 and "num_ctx" in bad.json()["detail"]
    assert client_in.get("/api/ai/tasks/nope").status_code == 404


def test_models_list_with_hardware_hint(client_in, fake_ai):
    body = client_in.get("/api/ai/models").json()
    names = [m["name"] for m in body["ollama"]["models"]]
    assert "gpt-oss:20b" in names and body["ollama"]["up"] and body["fake"]
    assert set(body["tasks"]) == set(config.TASKS)
    assert body["anthropic"]["key"] in ("set", "not set")


def test_hardware_hint():
    gpt_oss = 13_793_000_000
    small_gpu = {"ram_gb": 64, "gpus": [{"name": "RTX", "mem_gb": 8.0}]}
    hint = hardware_hint(gpt_oss, small_gpu, "linux")
    assert not hint["fits"] and "runs on the CPU" in hint["text"] and 16 <= hint["need_gb"] <= 18
    assert hardware_hint(gpt_oss, {"ram_gb": 68.7, "gpus": []}, "macos")["fits"]
    assert not hardware_hint(gpt_oss, {"ram_gb": 16, "gpus": []}, "macos")["fits"]


# --- index --------------------------------------------------------------------------------------


def test_index_and_hybrid_search(kb):
    assert kb["files"] > 10 and kb["chunks"] > 50 and kb["embedded"] == kb["chunks"]
    assert not kb["degraded"] and kb["dim"] == providers.FAKE_DIM
    hits, mode = index.search("how do I start Gazebo with RViz on the Mac")
    assert mode == "hybrid" and len(hits) == index.TOP_K
    assert any("make vnc" in h["text"] for h in hits)
    assert not index.stale()


def test_refresh_reindexes_changed_files_only(fake_ai, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# Readme\nzenoh router setup\n")
    (tmp_path / "docs/a.md").write_text("# A\nlichtblick panel layout\n")
    first = index.refresh(full=True, log_line=lambda s: None, repo=tmp_path)
    assert (first["files"], first["changed"]) == (2, 2)
    (tmp_path / "docs/a.md").write_text("# A\nlichtblick panel layout, changed\n")
    (tmp_path / "README.md").unlink()
    second = index.refresh(log_line=lambda s: None, repo=tmp_path)
    assert (second["files"], second["changed"]) == (1, 1)
    hits, _ = index.search("lichtblick")
    assert hits[0]["source"] == "docs/a.md" and "changed" in hits[0]["text"]


def test_without_embeddings_the_index_is_full_text_only(fake_ai, tmp_path, monkeypatch):
    async def down(*a, **k):
        raise providers.ProviderError("Ollama is not reachable", unavailable=True)

    monkeypatch.setattr(providers.Fake, "embed", down)
    (tmp_path / "README.md").write_text("# Readme\nthe e-stop wiring\n")
    value = index.refresh(full=True, log_line=lambda s: None, repo=tmp_path)
    assert value["degraded"] and value["embedded"] == 0 and "not reachable" in value["error"]
    hits, mode = index.search("e-stop wiring")
    assert mode == "full-text" and hits[0]["source"] == "README.md"


# --- context pack -------------------------------------------------------------------------------


def _job(step="ws.build", status=Job.FAILED, lines=()):
    job = service.create_job(step, None, "alice", "macos")
    job.status, job.exit_code = status, 1 if status == Job.FAILED else 0
    job.save()
    write_log(job, list(lines))
    return job


def test_distinct_lines_drop_timestamps_and_count(settings):
    settings.FAKE = True
    job = _job(
        lines=[
            ("out", "[move_group-3] [WARN] [1793692507.711281500] [moveit]: no octomap"),
            ("out", "[move_group-7] [WARN] [1793692599.000000001] [moveit]: no octomap"),
            ("out", "plain output"),
            (
                "err",
                "\x1b[31m[ERROR] [1793692510.1] [spawner]: Failed to activate controller\x1b[0m",
            ),
        ]
    )
    last, distinct = context.job_lines(job, service.read_log(job))
    assert [(d["text"], d["count"], d["level"]) for d in distinct] == [
        ("[move_group] [WARN] [moveit]: no octomap", 2, "warn"),
        ("[ERROR] [spawner]: Failed to activate controller", 1, "error"),
    ]
    assert len(last) == 3


def test_context_pack_holds_no_secret(settings, monkeypatch, fake_ai):
    settings.FAKE = True
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("COCKPIT_DB_PASSWORD", "db-password-123456")
    job = _job(
        lines=[
            ("out", f"[ERROR] [1.0] [x]: auth failed with {SECRET}"),
            ("out", "[WARN] password db-password-123456 rejected"),
        ]
    )
    src = [{"source": "docs/x.md", "heading": "h", "kind": "doc", "text": f"key {SECRET}"}]
    for task in ("chat", "error_explain"):
        pack = context.build(task, {"job_id": str(job.id), "line": SECRET}, src)
        dumped = json.dumps(pack, ensure_ascii=False)
        assert (
            SECRET not in dumped and "db-password-123456" not in dumped and "«redacted»" in dumped
        )


def test_context_pack_truncation_order(settings):
    settings.FAKE = True
    job = _job(
        lines=[("out", f"[ERROR] [1.{i}] [n]: failure number {i} " + "x" * 300) for i in range(50)]
    )
    sources = [
        {"source": f"docs/{i}.md", "heading": "h", "kind": "doc", "text": "y" * 3000}
        for i in range(12)
    ]
    pack = context.build("chat", {"job_id": str(job.id), "yaml": "z" * 9000}, sources, budget=3000)
    assert context.size(pack) <= 3000
    lines = pack["job"]["error_lines"]
    notes = pack["truncated"]
    assert notes[0].endswith("older log line(s) left out") and not lines  # old lines first
    assert "long text(s) shortened" in notes[1]
    assert "lower-ranked sources left out" in notes[2]
    assert [s["n"] for s in pack["sources"]] == list(
        range(1, len(pack["sources"]) + 1)
    )  # best kept
    # with room, nothing goes
    roomy = context.build("chat", {"job_id": str(job.id)}, sources[:1], budget=50_000)
    assert "truncated" not in roomy and len(roomy["job"]["error_lines"]) == 50


def test_file_sources_from_log_paths():
    found = context.file_sources(
        [
            "File src/ar3_cockpit_agent/ar3_cockpit_agent/app.py:20, in main",
            "see config/robot.yaml",
            "no such file src/nope/x.py:3",
            "../../.env",
        ]
    )
    assert [f["source"] for f in found] == [
        "src/ar3_cockpit_agent/ar3_cockpit_agent/app.py",
        "config/robot.yaml",
    ]
    assert found[0]["heading"] == "lines 5-35" and "   20 " in found[0]["text"]


def test_proposals_are_whitelisted():
    text = (
        "Run [[step:ws.build]] or [[step:rm.rf]], then [[make:sim-mac]] or [[make:nope]] "
        "[[step:ws.build]]"
    )
    got = prompts.proposals(text)
    assert [(p["kind"], p["id"]) for p in got] == [("step", "ws.build"), ("make", "sim-mac")]
    assert got[0]["step_id"] == "ws.build" and got[1]["command"] == "make sim-mac"
    assert "ws.build" in prompts.system_prompt(
        "chat"
    ) and "### Needs action" in prompts.system_prompt("error_explain")


# --- answers (SSE) ------------------------------------------------------------------------------


def events(raw: bytes) -> list[tuple[str, dict]]:
    out = []
    for block in raw.decode().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            out.append((name, data))
    return out


async def ask(user, body: dict, thread=None):
    c = AsyncClient()
    await sync_to_async(c.force_login)(user)
    if thread is None:
        thread = await sync_to_async(Thread.objects.create)(user=user.username)
    res = await c.post(
        f"/api/assistant/threads/{thread.id}/messages", body, content_type="application/json"
    )
    raw = (
        b"".join([chunk async for chunk in res.streaming_content]) if res.streaming else res.content
    )
    return res, thread, events(raw) if res.streaming else json.loads(raw)


@pytest.mark.django_db(transaction=True)
async def test_why_did_ws_build_fail(user, kb, settings, monkeypatch):
    """Acceptance (§18 C6): the answer to a failed job cites its log lines and a doc section."""
    settings.FAKE = True
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    fake.save_state(
        settings.DATA_DIR,
        {"fail": {"make ws-build": "Failed   <<< ar3_bringup [1.2s, exited with code 1]"}},
    )
    job = await sync_to_async(service.create_job)("ws.build", None, "alice", "macos")
    job = await executor.execute(await sync_to_async(Job.objects.get)(pk=job.pk))
    assert job.status == "failed"
    res, thread, evs = await ask(
        user,
        {
            "content": "why did ws-build fail?",
            "task": "chat",
            "context": {"page": "/setup/job/", "job_id": str(job.id)},
        },
    )
    assert res.status_code == 200 and res["Content-Type"] == "text/event-stream"
    names = [n for n, _ in evs]
    assert names[0] == "meta" and names[-1] == "done" and "reasoning" in names and "token" in names
    meta = evs[0][1]
    assert meta["provider"] == "fake" and meta["retrieval"] == "hybrid" and meta["sources"]
    msg = evs[-1][1]["message"]
    assert "[1]" in msg["content"] and "<think>" not in msg["content"]
    assert msg["reasoning"].startswith("The user asks")
    assert [p["id"] for p in msg["proposals"]] == ["ws.build"]
    stored = await sync_to_async(Message.objects.get)(pk=msg["id"])
    pack = stored.context_pack
    assert any("Failed <<< ar3_bringup" in ln["text"] for ln in pack["job"]["error_lines"])
    assert pack["job"]["matches"][0]["id"] == "colcon_failed"
    # the pattern's doc comes first
    assert pack["sources"][0]["source"] == "docs/manual/troubleshooting.md"
    assert SECRET not in json.dumps(pack)
    # the question is the first turn of the thread, the answer the second
    roles = await sync_to_async(lambda: [m.role for m in thread.messages.all()])()
    assert roles == ["user", "assistant"]


@pytest.mark.django_db(transaction=True)
async def test_explain_warnings_of_a_succeeded_test_job(user, kb, settings, monkeypatch):
    """Acceptance (§18 C6): each distinct warning/error line is classified; the pack holds no
    secret."""
    settings.FAKE = True
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    job = await sync_to_async(_job)(
        "tests.run",
        Job.SUCCEEDED,
        [
            ("out", "[WARN] [1.0] [launch]: shutdown requested"),
            ("out", "[WARN] [2.0] [launch]: shutdown requested"),
            ("out", f"[ERROR] [3.0] [agent]: token {SECRET} rejected"),
            (
                "out",
                "[ERROR] [4.0] [controller_manager]: Switch controller timed out after 5 seconds!",
            ),
            ("out", "91 tests, 0 failures"),
        ],
    )
    _res, _t, evs = await ask(
        user,
        {
            "content": "Explain the warnings and errors of this job.",
            "task": "error_explain",
            "context": {"job_id": str(job.id)},
        },
    )
    msg = evs[-1][1]["message"]
    for heading in ("### Harmless", "### Worth fixing", "### Needs action"):
        assert heading in msg["content"]
    stored = await sync_to_async(Message.objects.get)(pk=msg["id"])
    distinct = stored.context_pack["job"]["distinct_lines"]
    assert [d["count"] for d in distinct] == [2, 1, 1]
    for d in distinct:
        assert d["text"] in msg["content"]  # every distinct line got a class
    assert SECRET not in json.dumps(stored.context_pack) and SECRET not in msg["content"]


@pytest.mark.django_db(transaction=True)
async def test_scenario_help_returns_a_validated_diff(user, kb, settings):
    settings.FAKE = True
    current = (settings.REPO_DIR / "config/scenarios/tour-three-objects.yaml").read_text()
    _res, _t, evs = await ask(
        user,
        {
            "content": "Add a description",
            "task": "scenario_help",
            "context": {"scenario_id": "tour-three-objects", "yaml": current},
        },
    )
    sc = evs[-1][1]["message"]["extra"]["scenario"]
    assert sc["ok"], sc["errors"]
    assert "+description: Written with the assistant" in sc["diff"]
    assert sc["yaml"] != current  # not saved: the file is unchanged
    assert (settings.REPO_DIR / "config/scenarios/tour-three-objects.yaml").read_text() == current


@pytest.mark.django_db(transaction=True)
async def test_provider_unavailable_is_an_error_event(user, kb, real_ai, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")  # nothing listens there
    _res, _t, evs = await ask(user, {"content": "hello"})
    name, data = evs[-1]
    assert name == "error" and data["unavailable"] and "not reachable" in data["message"]
    assert data["docs"] == providers.AI_CHECKS
    stored = await sync_to_async(Message.objects.get)(pk=data["id"])
    assert stored.error and stored.role == "assistant"


@pytest.mark.django_db(transaction=True)
async def test_reasoning_is_not_sent_back_in_later_turns(user, kb):
    _r, thread, _e = await ask(user, {"content": "first question"})
    seen = {}

    async def spy(self, cfg, system, messages, hint=None):
        seen["messages"] = messages
        yield providers.Piece("text", "ok")

    providers.Fake.chat, original = spy, providers.Fake.chat
    try:
        await ask(user, {"content": "second question"}, thread)
    finally:
        providers.Fake.chat = original
    sent = json.dumps(seen["messages"])
    assert "first question" in sent and "The user asks" not in sent  # no reasoning
    assert [m["role"] for m in seen["messages"]] == ["user", "assistant", "user"]


@pytest.mark.django_db(transaction=True)
async def test_bad_requests_and_rate_limit(user, kb, monkeypatch):
    res, _t, body = await ask(user, {"content": ""})
    assert res.status_code == 400
    res, _t, body = await ask(user, {"content": "x", "task": "shell"})
    assert res.status_code == 400
    monkeypatch.setenv("ASSISTANT_RATE_PER_MINUTE", "1")
    res, thread, _e = await ask(user, {"content": "one"})
    assert res.status_code == 200
    res, _t, body = await ask(user, {"content": "two"}, thread)
    assert res.status_code == 429 and body["retry_after_s"] == 60
    anon = AsyncClient()
    res = await anon.post(
        f"/api/assistant/threads/{thread.id}/messages",
        {"content": "x"},
        content_type="application/json",
    )
    assert res.status_code == 401


def test_threads_api_is_per_user(client_in):
    t = client_in.post(
        "/api/assistant/threads",
        {"provider": "ollama", "model": "gemma3:4b"},
        content_type="application/json",
    ).json()
    assert (t["provider"], t["model"], t["messages"]) == ("ollama", "gemma3:4b", [])
    other = Thread.objects.create(user="bob")
    assert client_in.get(f"/api/assistant/threads/{other.id}").status_code == 404
    assert [x["id"] for x in client_in.get("/api/assistant/threads").json()] == [t["id"]]
    patched = client_in.patch(
        f"/api/assistant/threads/{t['id']}",
        {"provider": "anthropic", "model": ""},
        content_type="application/json",
    ).json()
    assert (
        chat.thread_config(Thread.objects.get(pk=t["id"]), "chat")["model"]
        == config.DEFAULT_ANTHROPIC_MODEL
    )
    assert patched["provider"] == "anthropic"
    assert client_in.delete(f"/api/assistant/threads/{t['id']}").status_code == 204


def test_old_threads_are_pruned(settings):
    old = Thread.objects.create(user="alice")
    Thread.objects.filter(pk=old.pk).update(
        updated=timezone.now() - timedelta(days=settings.AI_LOG_DAYS + 1)
    )
    Thread.objects.create(user="alice")
    assert chat.prune_threads() == 1 and Thread.objects.count() == 1


@pytest.mark.django_db(transaction=True)
async def test_rebuild_index_runs_as_a_job(user, fake_ai, settings):
    settings.FAKE = True
    c = AsyncClient()
    await sync_to_async(c.force_login)(user)
    res = await c.post(
        "/api/assistant/index/rebuild", {"full": False}, content_type="application/json"
    )
    job = res.json()
    assert (
        res.status_code == 201
        and job["step_id"] == "assistant.index"
        and job["command"] == "make assistant-index"
    )
    done = await executor.execute(await sync_to_async(Job.objects.get)(pk=job["id"]))
    assert done.status == "succeeded"
    text = "\n".join(ln["text"] for ln in await sync_to_async(service.read_log)(done))
    assert "indexed README.md" in text and "with embeddings" in text
    status = (await c.get("/api/assistant/index")).json()
    assert status["chunks"] > 0 and not status["degraded"] and status["by_kind"]["doc"] > 0
    assert await sync_to_async(AppSetting.objects.filter(key=index.META_KEY).exists)()


def test_lines_before_a_match_go_into_the_pack():
    lines = [
        {"seq": 1, "stream": "cmd", "text": "$ make ws-build"},
        {"seq": 2, "stream": "err", "text": "CMake Error at CMakeLists.txt:4 (find_package):"},
        {"seq": 3, "stream": "err", "text": "  Could not find ar3_missing_dependency"},
        {"seq": 4, "stream": "out", "text": ""},
        {"seq": 5, "stream": "out", "text": "Failed   <<< zz [2.7s, exited with code 1]"},
    ]
    lines.insert(1, {"seq": 0, "stream": "err", "text": "--- stderr: zz"})
    lines.insert(1, {"seq": -1, "stream": "out", "text": "Starting >>> zz"})
    for i, ln in enumerate(lines):
        ln["seq"] = i + 1
    seq = lines[-1]["seq"]
    assert context.match_context(lines, [seq, seq]) == [
        "--- stderr: zz",
        "CMake Error at CMakeLists.txt:4 (find_package):",
        "Could not find ar3_missing_dependency",
    ]
