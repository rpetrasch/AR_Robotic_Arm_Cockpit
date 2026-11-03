"""Login, users and passwords (docs/specs/spec_cockpit.md §7.4, §11.1). No roles: every user can do
everything; the audit log records who did it."""

from __future__ import annotations

import math
from datetime import timedelta

from django.conf import settings
from django.contrib import auth as django_auth
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.auth import client_ip

from . import audit


class LoginFailed(Exception):
    pass


class LoginThrottled(Exception):
    def __init__(self, wait_s: int):
        super().__init__(f"too many failed logins; wait {wait_s} s")
        self.wait_s = wait_s


class UserError(Exception):
    pass


def _obj(username: str) -> str:
    return f"user:{username}"


def throttle_wait(username: str, ip: str) -> int:
    """Seconds until the next attempt is allowed (0 = allowed): 5 failures per user+IP in
    5 minutes; a successful login resets the count."""
    window = timedelta(seconds=settings.LOGIN_THROTTLE_WINDOW_S)
    now = timezone.now()
    since = now - window
    last_ok = audit.last_ts("auth.login", _obj(username), ip)
    if last_ok and last_ok > since:
        since = last_ok
    failures = list(
        audit.AuditEntry.objects.filter(
            action="auth.login_failed", obj=_obj(username), ip=ip, ts__gt=since
        )
        .order_by("-ts")
        .values_list("ts", flat=True)[: settings.LOGIN_THROTTLE_FAILURES]
    )
    if len(failures) < settings.LOGIN_THROTTLE_FAILURES:
        return 0
    oldest = failures[-1]  # the window opens again when the oldest counted failure expires
    return max(1, math.ceil((oldest + window - now).total_seconds()))


def password_problems(password: str, user: User | None = None) -> list[str]:
    try:
        validate_password(password or "", user)
    except ValidationError as exc:
        return list(exc.messages)
    return []


def login(request, username: str, password: str) -> User:
    username = (username or "").strip()
    ip = client_ip(request)
    wait = throttle_wait(username, ip)
    if wait:
        audit.record(username, "auth.login_throttled", _obj(username), {"wait_s": wait}, ip)
        raise LoginThrottled(wait)
    user = django_auth.authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        audit.record(username, "auth.login_failed", _obj(username), {}, ip)
        raise LoginFailed("invalid username or password")
    django_auth.login(request, user)
    audit.record(username, "auth.login", _obj(username), {}, ip)
    return user


def logout(request) -> None:
    if request.user.is_authenticated:
        audit.record_request(request, "auth.logout", _obj(request.user.username))
    django_auth.logout(request)


def change_password(request, old: str, new: str) -> None:
    user: User = request.user
    if not user.check_password(old or ""):
        raise LoginFailed("current password is wrong")
    problems = password_problems(new, user)
    if problems:
        raise UserError(" ".join(problems))
    user.set_password(new)
    user.save(update_fields=["password"])
    django_auth.update_session_auth_hash(request, user)
    audit.record_request(request, "auth.password_changed", _obj(user.username))


def create_user(username: str, password: str, *, actor: str, ip: str = "", via: str = "api"):
    username = (username or "").strip()
    if not username:
        raise UserError("username is required")
    if User.objects.filter(username=username).exists():
        raise UserError(f"user {username!r} already exists")
    problems = password_problems(password, User(username=username))
    if problems:
        raise UserError(" ".join(problems))
    user = User.objects.create_user(username=username, password=password)
    audit.record(actor, "user.create", _obj(username), {"via": via}, ip)
    return user


@transaction.atomic
def create_first_user(username: str, password: str, ip: str) -> User:
    """Only while no user exists; the lock keeps two first-run requests from both winning."""
    User.objects.select_for_update().all().exists()
    if User.objects.exists():
        raise UserError("a user already exists; sign in instead")
    return create_user(username, password, actor=username, ip=ip, via="setup-account")


@transaction.atomic
def delete_user(request, user_id: int) -> None:
    target = User.objects.select_for_update().filter(pk=user_id).first()
    if target is None:
        raise LookupError("no such user")
    if target.pk == request.user.pk:
        raise UserError("you cannot delete yourself")
    if User.objects.count() <= 1:
        raise UserError("the last user cannot be deleted")
    username = target.username
    target.delete()
    audit.record_request(request, "user.delete", _obj(username))


def reset_password(request, user_id: int, password: str) -> None:
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        raise LookupError("no such user")
    problems = password_problems(password, target)
    if problems:
        raise UserError(" ".join(problems))
    target.set_password(password)
    target.save(update_fields=["password"])
    audit.record_request(request, "user.password_reset", _obj(target.username))
