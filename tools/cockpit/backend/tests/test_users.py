import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import Client

from accounts import audit
from accounts.models import AuditEntry

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


def post(c: Client, path: str, data: dict, **extra):
    return c.post(path, data, content_type="application/json", **extra)


# --- first user (§7.4) -----------------------------------------------------------------------


def test_setup_status_reports_need_and_localhost(db):
    assert Client().get("/api/setup-account").json() == {"needed": True, "allowed": True}
    remote = Client(REMOTE_ADDR="192.168.1.20").get("/api/setup-account").json()
    assert remote == {"needed": True, "allowed": False}


def test_first_user_from_localhost_is_created_and_signed_in(db):
    c = Client()
    res = post(c, "/api/setup-account", {"username": "owner", "password": PASSWORD})
    assert res.status_code == 201
    assert c.get("/api/auth/me").json()["username"] == "owner"
    assert AuditEntry.objects.filter(action="user.create", payload__via="setup-account").exists()


def test_first_user_only_from_localhost(db):
    c = Client(REMOTE_ADDR="192.168.1.20")
    res = post(c, "/api/setup-account", {"username": "owner", "password": PASSWORD})
    assert res.status_code == 403
    assert "ar3ctl create-user" in res.json()["hint"]
    assert not User.objects.exists()


def test_first_user_only_once(user):
    res = post(Client(), "/api/setup-account", {"username": "mallory", "password": PASSWORD})
    assert res.status_code == 409
    assert Client().get("/api/setup-account").json()["needed"] is False


def test_first_user_password_rules(db):
    res = post(Client(), "/api/setup-account", {"username": "owner", "password": "short"})
    assert res.status_code == 400
    assert "at least 10 characters" in res.json()["detail"]


# --- user management (Settings → Users) ------------------------------------------------------


def test_list_create_and_reset(client_in):
    res = post(client_in, "/api/users", {"username": "bob", "password": PASSWORD})
    assert res.status_code == 201
    names = [u["username"] for u in client_in.get("/api/users").json()]
    assert names == ["alice", "bob"]
    assert "password" not in client_in.get("/api/users").json()[0]
    bob = User.objects.get(username="bob")
    res = post(client_in, f"/api/users/{bob.pk}/reset-password", {"password": "new-long-password"})
    assert res.status_code == 200
    assert (
        Client()
        .post(
            "/api/auth/login",
            {"username": "bob", "password": "new-long-password"},
            content_type="application/json",
        )
        .status_code
        == 200
    )
    actions = set(AuditEntry.objects.values_list("action", flat=True))
    assert {"user.create", "user.password_reset"} <= actions


def test_duplicate_and_weak_users_are_refused(client_in):
    assert (
        post(client_in, "/api/users", {"username": "alice", "password": PASSWORD}).status_code
        == 400
    )
    assert post(client_in, "/api/users", {"username": "bob", "password": "123"}).status_code == 400


def test_delete_rules(client_in, user):
    assert (
        client_in.delete(f"/api/users/{user.pk}").json()["detail"] == "you cannot delete yourself"
    )
    bob = User.objects.create_user("bob", password=PASSWORD)
    assert client_in.delete(f"/api/users/{bob.pk}").status_code == 200
    assert client_in.delete(f"/api/users/{bob.pk}").status_code == 404
    assert AuditEntry.objects.filter(action="user.delete", obj="user:bob").exists()


def test_the_last_user_is_never_deleted(user, rf):
    from accounts import users

    request = rf.delete("/")
    request.user = User(pk=999_999, username="ghost")  # a session of an already deleted user
    with pytest.raises(users.UserError, match="last user"):
        users.delete_user(request, user.pk)


# --- audit (§11.3) ---------------------------------------------------------------------------


def test_audit_list_newest_first_with_filters(client_in):
    audit.record("alice", "test.one", "x:1")
    audit.record("bob", "test.two", "x:2")
    page = client_in.get("/api/audit?action=test.").json()
    assert page["count"] == 2
    assert [e["action"] for e in page["results"]] == ["test.two", "test.one"]
    assert client_in.get("/api/audit?actor=bob").json()["count"] == 1
    assert client_in.get("/api/audit?limit=x").status_code == 400


def test_audit_redacts_secret_payload_keys(db):
    entry = audit.record("a", "x", payload={"password": "p", "api_key": "k", "ok": 1})
    assert entry.payload == {"password": "<redacted>", "api_key": "<redacted>", "ok": 1}


def test_audit_prune_keeps_one_year(db):
    from datetime import timedelta

    from django.utils import timezone

    old = audit.record("a", "old")
    AuditEntry.objects.filter(pk=old.pk).update(ts=timezone.now() - timedelta(days=366))
    audit.record("a", "new")
    assert audit.prune() == 1
    assert list(AuditEntry.objects.values_list("action", flat=True)) == ["new"]


# --- CLI -------------------------------------------------------------------------------------


def test_create_admin_command(db):
    call_command("create_admin", username="cli", password=PASSWORD)
    assert User.objects.get(username="cli").check_password(PASSWORD)
    assert AuditEntry.objects.get(action="user.create").actor == "system"
    with pytest.raises(CommandError, match="already exists"):
        call_command("create_admin", username="cli", password=PASSWORD)
