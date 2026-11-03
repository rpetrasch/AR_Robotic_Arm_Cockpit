"""The assistant's data (docs/specs/spec_cockpit.md §13, §14): UI settings, threads, messages.

The knowledge index (``kb_chunks``, ``kb_files``) is plain SQL, see migrations/0002 and
assistant.index: its vector column changes dimension with the embedding model.
"""

import uuid

from django.db import models


class AppSetting(models.Model):
    """A setting stored by the UI (§13): overrides defaults.env, not .env or the environment."""

    key = models.CharField(max_length=100, primary_key=True)
    value = models.JSONField()
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=150, blank=True)

    class Meta:
        db_table = "app_settings"


class Thread(models.Model):
    """A conversation of one user. ``provider`` / ``model``: the per-thread switch (§14.3),
    empty for the task settings."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.CharField(max_length=150, db_index=True)
    title = models.CharField(max_length=200, blank=True)
    provider = models.CharField(max_length=20, blank=True)
    model = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated"]


class Message(models.Model):
    """One turn. An assistant message keeps what it was built from: the context pack (redacted,
    §11.4), the numbered sources, the proposed steps, provider, model and latency."""

    USER, ASSISTANT = "user", "assistant"

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=[(USER, USER), (ASSISTANT, ASSISTANT)])
    task = models.CharField(max_length=20, default="chat")
    content = models.TextField(blank=True)
    reasoning = models.TextField(blank=True)  # shown collapsed, never sent back (§14.3)
    context = models.JSONField(default=dict, blank=True)  # what the page sent (user messages)
    context_pack = models.JSONField(default=dict, blank=True)
    sources = models.JSONField(default=list, blank=True)
    proposals = models.JSONField(default=list, blank=True)
    extra = models.JSONField(default=dict, blank=True)  # e.g. the scenario YAML + diff
    provider = models.CharField(max_length=20, blank=True)
    model = models.CharField(max_length=200, blank=True)
    notes = models.JSONField(default=list, blank=True)  # fallback, full-text only, truncated
    latency_ms = models.IntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created", "id"]
