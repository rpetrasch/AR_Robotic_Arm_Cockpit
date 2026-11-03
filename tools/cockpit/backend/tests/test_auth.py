import pytest
from django.contrib.auth.models import User
from django.test import Client

from accounts.models import AuditEntry

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


def login(client: Client, username="alice", password=PASSWORD, **extra):
    return client.post(
        "/api/auth/login",
        {"username": username, "password": password},
        content_type="application/json",
        **extra,
    )


def test_login_sets_an_http_only_lax_session_cookie(user, settings):
    c = Client()
    res = login(c)
    assert res.status_code == 200
    assert res.json()["username"] == "alice"
    cookie = res.cookies[settings.SESSION_COOKIE_NAME]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert cookie["max-age"] == 12 * 3600
    assert AuditEntry.objects.filter(action="auth.login", obj="user:alice").exists()


def test_wrong_password_is_a_generic_401_problem(user):
    res = login(Client(), password="wrong-password")
    assert res.status_code == 401
    assert res["Content-Type"] == "application/problem+json"
    body = res.json()
    assert body["title"] == "Sign-in failed" and body["status"] == 401
    # the same message for an unknown user: no user enumeration
    assert login(Client(), username="nobody").json()["detail"] == body["detail"]


def test_five_failures_throttle_with_retry_after(user):
    c = Client()
    for _ in range(5):
        assert login(c, password="wrong-password").status_code == 401
    res = login(c)  # even the right password is refused now
    assert res.status_code == 429
    assert 1 <= res.json()["retry_after_s"] <= 300
    # another IP is not affected
    assert login(Client(), REMOTE_ADDR="10.0.0.9").status_code == 200


def test_successful_login_resets_the_failure_count(user):
    c = Client()
    for _ in range(4):
        login(c, password="wrong-password")
    assert login(c).status_code == 200
    for _ in range(4):
        login(c, password="wrong-password")
    assert login(c).status_code == 200


def test_login_requires_csrf_token(user):
    c = Client(enforce_csrf_checks=True)
    assert login(c).status_code == 403
    c.get("/api/setup-account")  # sets the csrftoken cookie
    token = c.cookies["csrftoken"].value
    assert login(c, HTTP_X_CSRFTOKEN=token).status_code == 200


def test_api_requires_sign_in_with_401(db):
    for path in ("/api/auth/me", "/api/users", "/api/audit", "/api/env/state", "/api/platform"):
        res = Client().get(path)
        assert res.status_code == 401, path
        assert res["Content-Type"] == "application/problem+json"


def test_me_and_logout(client_in):
    assert client_in.get("/api/auth/me").json()["username"] == "alice"
    assert client_in.post("/api/auth/logout").status_code == 200
    assert client_in.get("/api/auth/me").status_code == 401
    assert AuditEntry.objects.filter(action="auth.logout").exists()


def test_password_change_rules(client_in):
    def change(old, new):
        return client_in.post(
            "/api/auth/password",
            {"old_password": old, "new_password": new},
            content_type="application/json",
        )

    assert change("wrong", "another-long-pass").status_code == 400
    assert change(PASSWORD, "short").status_code == 400
    assert change(PASSWORD, "password123").status_code == 400  # common password
    assert change(PASSWORD, "another-long-pass").status_code == 200
    assert client_in.get("/api/auth/me").status_code == 200  # session kept
    assert User.objects.get(username="alice").check_password("another-long-pass")
    entry = AuditEntry.objects.get(action="auth.password_changed")
    assert "another-long-pass" not in str(entry.payload)


def test_passwords_are_hashed_with_argon2(user):
    assert user.password.startswith("argon2$argon2id$")
