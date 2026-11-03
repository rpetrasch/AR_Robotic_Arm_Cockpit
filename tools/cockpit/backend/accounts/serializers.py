from django.contrib.auth.models import User
from rest_framework import serializers

from .models import AuditEntry


class CredentialsSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(max_length=256, trim_whitespace=False)
    new_password = serializers.CharField(max_length=256, trim_whitespace=False)


class PasswordResetSerializer(serializers.Serializer):
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    last_login = serializers.DateTimeField(allow_null=True)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "date_joined", "last_login"]


class SetupStatusSerializer(serializers.Serializer):
    needed = serializers.BooleanField(help_text="True while no user exists")
    allowed = serializers.BooleanField(help_text="True when the request comes from localhost")


class AuditEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEntry
        fields = ["id", "ts", "actor", "action", "obj", "ip", "payload"]


class AuditPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = AuditEntrySerializer(many=True)


class OkSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
