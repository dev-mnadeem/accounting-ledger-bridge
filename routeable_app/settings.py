"""Django settings for the routeable_app ledger bridge.

Every value that differs between a laptop and a deployment is read from the
environment. The three that matter most — ``SECRET_KEY``, ``DEBUG`` and
``ALLOWED_HOSTS`` — were hard-coded here, with ``DEBUG = True`` and
``ALLOWED_HOSTS = ['*']``, which is a production-unsafe default and a committed
signing key. They now default to the *safe* value and are overridden by env.
"""

import sys
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core security
# ---------------------------------------------------------------------------

DEBUG = config("DJANGO_DEBUG", default=False, cast=bool)

# No default outside DEBUG: a deployment that forgets to set a signing key must
# fail loudly at boot rather than silently sign sessions with a public string.
SECRET_KEY = config(
    "DJANGO_SECRET_KEY",
    default="insecure-development-key-do-not-use-in-production" if DEBUG else "",
)
if not SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY is not set. Generate one with:\n"
        "  python -c \"from django.core.management.utils import "
        "get_random_secret_key as k; print(k())\""
    )

ALLOWED_HOSTS = config(
    "DJANGO_ALLOWED_HOSTS",
    default="localhost,127.0.0.1,[::1]" if DEBUG else "",
    cast=Csv(),
)
CSRF_TRUSTED_ORIGINS = config("DJANGO_CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# Cookie hardening. Session cookies carry the ledger connection, so they never
# need to be readable from JavaScript or sent cross-site.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = config("DJANGO_SECURE_SSL_REDIRECT", default=True, cast=bool)
    SECURE_HSTS_SECONDS = config("DJANGO_HSTS_SECONDS", default=31536000, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # Off by default on purpose: submitting a domain to the browser preload
    # list is effectively irreversible, so it is the operator's call.
    SECURE_HSTS_PRELOAD = config("DJANGO_HSTS_PRELOAD", default=False, cast=bool)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "routeable_app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "routeable_app.wsgi.application"
ASGI_APPLICATION = "routeable_app.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": config("DJANGO_DB_PATH", default=str(BASE_DIR / "db.sqlite3")),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Ledger providers
# ---------------------------------------------------------------------------
# One entry per registered provider in `api.ledger.registry`. Adding a third
# ledger means adding a key here and a subclass there; nothing else changes.

LEDGER_DEFAULT_PROVIDER = config("LEDGER_DEFAULT_PROVIDER", default="quickbooks")
LEDGER_HTTP_TIMEOUT = config("LEDGER_HTTP_TIMEOUT", default=15.0, cast=float)

LEDGER_PROVIDERS = {
    "quickbooks": {
        "client_id": config("QBOOK_CLIENT_ID", default=""),
        "client_secret": config("QBOOK_CLIENT_SECRET", default=""),
        "redirect_uri": config("QBOOK_REDIRECT_URI", default=""),
        "environment": config("QBOOK_ENVIRONMENT", default="sandbox"),
        "company_id": config("QBOOK_COMPANY_ID", default=""),
        # Development/demo only: point the client at a local stub or proxy.
        "api_base": config("QBOOK_API_BASE", default=""),
        "token_url": config("QBOOK_TOKEN_URL", default=""),
    },
    "xero": {
        "client_id": config("XERO_CLIENT_ID", default=""),
        "client_secret": config("XERO_CLIENT_SECRET", default=""),
        "redirect_uri": config("XERO_REDIRECT_URI", default=""),
        "tenant_id": config("XERO_TENANT_ID", default=""),
        "api_base": config("XERO_API_BASE", default=""),
        "token_url": config("XERO_TOKEN_URL", default=""),
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Under `manage.py test` the application logs the failures it is asserting on,
# which would bury the test summary. Everywhere else the level is env-driven.
_RUNNING_TESTS = "test" in sys.argv
_LOG_LEVEL = "CRITICAL" if _RUNNING_TESTS else config("DJANGO_LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)-8s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": _LOG_LEVEL,
        },
    },
    # Django pre-configures a `django` logger of its own; name it here so the
    # level above applies to request logging too.
    "loggers": {
        "django": {"handlers": ["console"], "level": _LOG_LEVEL, "propagate": False},
        "api": {"handlers": ["console"], "level": _LOG_LEVEL, "propagate": False},
    },
    "root": {"handlers": ["console"], "level": _LOG_LEVEL},
}
