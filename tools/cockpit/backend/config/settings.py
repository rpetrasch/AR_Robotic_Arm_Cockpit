"""Django settings of the AR3 Cockpit (docs/specs/spec_cockpit.md §4, §11, §13).

Every value comes from the environment after ``envfiles.load()``: process env > <repo>/.env >
defaults.env. cockpit.sh generates the secrets into <repo>/.env on the first run.
"""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from . import envfiles

envfiles.load()

env = os.environ.get
TESTING = env("COCKPIT_ENV") == "test"


def _required(key: str) -> str:
    value = env(key, "")
    if value:
        return value
    if TESTING:
        return secrets.token_urlsafe(16)
    raise ImproperlyConfigured(
        f"{key} is not set. Run ./cockpit.sh once: it generates it into .env."
    )


BASE_DIR = envfiles.BACKEND
REPO_DIR = envfiles.REPO
FRONTEND_DIR = Path(env("COCKPIT_FRONTEND_DIR", str(REPO_DIR / "tools/cockpit/frontend/out")))
DATA_DIR = Path(os.path.expanduser(env("COCKPIT_DATA_DIR", "~/.ar3cockpit")))

SECRET_KEY = _required("DJANGO_SECRET_KEY")
DEBUG = envfiles.flag("DJANGO_DEBUG")
FAKE = envfiles.flag("COCKPIT_FAKE")
FAKE_PLATFORM = env("COCKPIT_FAKE_PLATFORM", "macos")

# The cockpit is reached by its bind address or, with COCKPIT_BIND=0.0.0.0, by any host name
# on the LAN (§11.1). Auth is always on, so the Host header check adds little there.
BIND = env("COCKPIT_BIND", "127.0.0.1")
PORT = int(env("COCKPIT_PORT", "8400"))
ALLOWED_HOSTS = ["*"] if BIND == "0.0.0.0" else ["127.0.0.1", "localhost", "[::1]", "testserver"]
CSRF_TRUSTED_ORIGINS = [
    f"http://{h}:{p}" for h in ("127.0.0.1", "localhost") for p in (PORT, 3000)
] + [o for o in env("COCKPIT_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "drf_spectacular",
    "channels",
    "core",
    "accounts",
    "environment",
    "monitoring",
    "setup",
    "telemetry",
    "scenarios",
    "assistant",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
TEMPLATES = []

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("COCKPIT_DB_HOST", "127.0.0.1"),
        "PORT": env("COCKPIT_DB_PORT", "5445"),
        "NAME": env("COCKPIT_DB_NAME", "ar3cockpit"),
        "USER": env("COCKPIT_DB_USER", "ar3cockpit"),
        "PASSWORD": _required("COCKPIT_DB_PASSWORD"),
        "CONN_MAX_AGE": 0,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6380/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}
}
if TESTING:  # unit tests need neither Redis nor a second process
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Users and sessions (§7.4, §11.1).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
SESSION_COOKIE_AGE = 12 * 3600  # 12 h idle: every request renews the expiry
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = envfiles.flag("COCKPIT_SECURE_COOKIES")
CSRF_COOKIE_SAMESITE = "Lax"
LOGIN_THROTTLE_FAILURES = 5
LOGIN_THROTTLE_WINDOW_S = 300

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["core.auth.SessionAuth"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.problems.exception_handler",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "AR3 Cockpit API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

AUDIT_RETENTION_DAYS = 365
ALERT_HISTORY_DAYS = 90  # resolved alerts (§10.5)

# Monitoring and data capture (§10, §12a).
TELEMETRY_RATE_HZ = int(env("TELEMETRY_RATE_HZ", "50"))
TELEMETRY_RECORD_SIM = envfiles.flag("TELEMETRY_RECORD_SIM", "1")
# table=days (0 or "off": keep); tables not listed keep everything.
TELEMETRY_RETENTION = env("TELEMETRY_RETENTION", "metrics=30,log_lines=7")
LOG_KEEP_PER_SOURCE = int(env("LOG_KEEP_PER_SOURCE", "50000"))
SCENARIOS_DIR = REPO_DIR / os.path.expanduser(env("COCKPIT_SCENARIOS_DIR", "config/scenarios"))
EXPORT_DIR = REPO_DIR / os.path.expanduser(env("COCKPIT_EXPORT_DIR", "data/exports"))
JOB_RETENTION_DAYS = 30  # job logs: 30 days or the last JOB_KEEP_LAST, whichever is more
JOB_KEEP_LAST = 200
AI_LOG_DAYS = int(env("AI_LOG_DAYS", "180"))  # assistant threads (§14.1)

LANGUAGE_CODE = "en"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "filters": {"quiet_session_probe": {"()": "config.logfilters.QuietSessionProbe"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {"django.request": {"filters": ["quiet_session_probe"]}},
    "root": {"handlers": ["console"], "level": env("COCKPIT_LOG_LEVEL", "INFO")},
}
