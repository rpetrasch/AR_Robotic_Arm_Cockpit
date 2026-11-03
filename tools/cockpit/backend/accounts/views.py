"""Auth, first-user setup, users and audit API (docs/specs/spec_cockpit.md §7.4, §11, §15)."""

from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from core.auth import SessionAuth, client_ip, is_local
from core.problems import DOCS, Problem

from . import users
from .models import AuditEntry
from .serializers import (
    AuditPageSerializer,
    CredentialsSerializer,
    MeSerializer,
    OkSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    SetupStatusSerializer,
    UserSerializer,
)


def _me(user: User) -> dict:
    return {"id": user.pk, "username": user.username, "last_login": user.last_login}


def _valid(serializer_class, request: Request) -> dict:
    s = serializer_class(data=request.data)
    s.is_valid(raise_exception=True)
    return s.validated_data


@extend_schema(request=CredentialsSerializer, responses={200: MeSerializer})
@api_view(["POST"])
@permission_classes([AllowAny])
def login(request: Request) -> Response:
    SessionAuth().check_csrf(request)
    data = _valid(CredentialsSerializer, request)
    try:
        user = users.login(request._request, data["username"], data["password"])
    except users.LoginThrottled as exc:
        raise Problem(
            429,
            "Too many attempts",
            f"Too many failed sign-ins. Wait {exc.wait_s} s.",
            extra={"retry_after_s": exc.wait_s},
        ) from exc
    except users.LoginFailed as exc:
        raise Problem(401, "Sign-in failed", "Invalid username or password.") from exc
    return Response(_me(user))


@extend_schema(request=None, responses={200: OkSerializer})
@api_view(["POST"])
def logout(request: Request) -> Response:
    users.logout(request._request)
    return Response({"ok": True})


@extend_schema(responses={200: MeSerializer})
@api_view(["GET"])
def me(request: Request) -> Response:
    return Response(_me(request.user))


@extend_schema(request=PasswordChangeSerializer, responses={200: OkSerializer})
@api_view(["POST"])
def password(request: Request) -> Response:
    data = _valid(PasswordChangeSerializer, request)
    try:
        users.change_password(request._request, data["old_password"], data["new_password"])
    except users.LoginFailed as exc:
        raise Problem(400, "Wrong password", str(exc)) from exc
    except users.UserError as exc:
        raise Problem(400, "Password rejected", str(exc)) from exc
    return Response({"ok": True})


@extend_schema(methods=["GET"], responses={200: SetupStatusSerializer})
@extend_schema(methods=["POST"], request=CredentialsSerializer, responses={201: MeSerializer})
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def setup_account(request: Request) -> Response:
    """First run: create the first user. Only from localhost and only while no user exists.
    GET also sets the CSRF cookie the login and setup forms need."""
    if request.method == "GET":
        get_token(request._request)  # the middleware sets the csrftoken cookie on the response
        return Response({"needed": not User.objects.exists(), "allowed": is_local(request)})
    SessionAuth().check_csrf(request)
    if not is_local(request):
        raise Problem(
            403,
            "Only from this machine",
            "The first user can only be created from the cockpit host itself.",
            hint="Open http://127.0.0.1:8400/setup-account on the host, or run ar3ctl create-user.",
            docs=DOCS,
        )
    data = _valid(CredentialsSerializer, request)
    try:
        user = users.create_first_user(data["username"], data["password"], client_ip(request))
    except users.UserError as exc:
        status_code = 409 if "already exists" in str(exc) else 400
        raise Problem(status_code, "Cannot create the first user", str(exc)) from exc
    users.login(request._request, data["username"], data["password"])
    return Response(_me(user), status=status.HTTP_201_CREATED)


@extend_schema(methods=["GET"], responses={200: UserSerializer(many=True)})
@extend_schema(methods=["POST"], request=CredentialsSerializer, responses={201: UserSerializer})
@api_view(["GET", "POST"])
def user_list(request: Request) -> Response:
    if request.method == "GET":
        return Response(UserSerializer(User.objects.order_by("username"), many=True).data)
    data = _valid(CredentialsSerializer, request)
    try:
        user = users.create_user(
            data["username"], data["password"], actor=request.user.username, ip=client_ip(request)
        )
    except users.UserError as exc:
        raise Problem(400, "Cannot create user", str(exc)) from exc
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema(responses={200: OkSerializer})
@api_view(["DELETE"])
def user_detail(request: Request, user_id: int) -> Response:
    try:
        users.delete_user(request._request, user_id)
    except LookupError as exc:
        raise Problem(404, "Not found", str(exc)) from exc
    except users.UserError as exc:
        raise Problem(409, "Cannot delete user", str(exc)) from exc
    return Response({"ok": True})


@extend_schema(request=PasswordResetSerializer, responses={200: OkSerializer})
@api_view(["POST"])
def user_reset_password(request: Request, user_id: int) -> Response:
    data = _valid(PasswordResetSerializer, request)
    try:
        users.reset_password(request._request, user_id, data["password"])
    except LookupError as exc:
        raise Problem(404, "Not found", str(exc)) from exc
    except users.UserError as exc:
        raise Problem(400, "Password rejected", str(exc)) from exc
    return Response({"ok": True})


@extend_schema(responses={200: AuditPageSerializer})
@api_view(["GET"])
def audit_list(request: Request) -> Response:
    """Newest first. Query: limit (≤ 500), offset, actor, action (prefix)."""
    qs = AuditEntry.objects.all()
    if actor := request.query_params.get("actor"):
        qs = qs.filter(actor=actor)
    if action := request.query_params.get("action"):
        qs = qs.filter(action__startswith=action)
    try:
        limit = min(max(int(request.query_params.get("limit", 100)), 1), 500)
        offset = max(int(request.query_params.get("offset", 0)), 0)
    except ValueError as exc:
        raise Problem(400, "Invalid request", "limit and offset must be integers") from exc
    page = qs[offset : offset + limit]
    return Response(
        AuditPageSerializer({"count": qs.count(), "results": list(page)}).data,
    )
