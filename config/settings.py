"""
Django settings for the Cambridge Mature Students Society portal.

Configuration is environment-driven so the same codebase runs locally
(SQLite, dev login) and on the SRCF (MySQL/PostgreSQL, Raven OIDC).
Set environment variables in ~/.envrc or the systemd/supervise unit on SRCF.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


# --- Core security -----------------------------------------------------------

# The committed fallback key is for LOCAL DEV ONLY. A production boot
# (RAVEN_MODE != "dev") that still carries it is refused below, so it can never
# silently sign real sessions.
INSECURE_SECRET_KEY = "dev-only-insecure-key-change-me-on-srcf"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", INSECURE_SECRET_KEY)

DEBUG = env_bool("DJANGO_DEBUG", default=True)

# Raven / University Account login mode (see accounts/auth.py and
# DEPLOYMENT_SRCF.md):
#   dev    - local development: impersonation login page for seeded users
#   header - SRCF: Apache mod_ucam_webauth (Nevar) forwards the CRSid in the
#            X-MSS-Remote-User header on the /accounts/raven/ path
#   oidc   - University Entra tenant via a UIS Toolkit OIDC registration
# "dev" is the ONLY mode that enables the impersonation login. Any other value
# is treated as a real deployment for the security hardening below.
RAVEN_MODE = os.environ.get("RAVEN_MODE", "dev")
IS_PRODUCTION_AUTH = RAVEN_MODE != "dev"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "mss.soc.srcf.net,localhost,127.0.0.1").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# Transport security is tied to the auth MODE, not DEBUG, so a production
# instance still gets Secure cookies + the proxy SSL header even if
# DJANGO_DEBUG was mistakenly left on (that combination is refused at boot,
# just below, regardless).
if IS_PRODUCTION_AUTH:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    # HSTS makes browsers refuse plain HTTP to this host on return visits.
    # Left OFF by default (0) — turn it on via DJANGO_HSTS_SECONDS (e.g.
    # 31536000) only AFTER the custom domain's HTTPS is confirmed working, per
    # Django's warning that careless HSTS is hard to undo. See DEPLOYMENT §10.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS")
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD")
    # By default let SRCF's Apache/Let's Encrypt do the HTTP->HTTPS redirect;
    # set DJANGO_SSL_REDIRECT=true to have Django enforce it too.
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT")

    # Fail closed: a real deployment must never run with development defaults.
    if DEBUG:
        raise ImproperlyConfigured(
            f"RAVEN_MODE={RAVEN_MODE!r} is a production auth mode but "
            "DJANGO_DEBUG is true. Set DJANGO_DEBUG=false in the environment."
        )
    if SECRET_KEY == INSECURE_SECRET_KEY:
        raise ImproperlyConfigured(
            f"RAVEN_MODE={RAVEN_MODE!r} is a production auth mode but "
            "DJANGO_SECRET_KEY is unset (the committed dev fallback is "
            "publicly known). Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(50))"'
        )

# --- Apps / middleware --------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Society apps
    "core",
    "accounts",
    "events",
    "guide",
    "supper",
    "dashboard",
    "panel",
    "inbox",
    "faq",
    "testimonials",
    "polls",
    "notifications",
    "posters",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.BannedUserMiddleware",
    "accounts.middleware.TermsAcceptanceMiddleware",
    "accounts.middleware.ProfileCompletionMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_config",
                "core.context_processors.navigation",
                "inbox.context_processors.unread_messages",
                "notifications.context_processors.unread_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database -----------------------------------------------------------------
# Default: SQLite (fine for dev; also workable on SRCF for a society-sized site).
# On SRCF, set DB_ENGINE=mysql plus DB_NAME/DB_USER/DB_PASSWORD to use the
# society MySQL database instead.

DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite")

if DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ["DB_NAME"],
            "USER": os.environ["DB_USER"],
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }
elif DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["DB_NAME"],
            "USER": os.environ["DB_USER"],
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --- Auth ---------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "core:home"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# RAVEN_MODE is defined in the Core security section above (it also drives the
# production hardening). Here we wire the backend + middleware for header mode.
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

if RAVEN_MODE == "header":
    MIDDLEWARE.insert(
        MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1,
        "accounts.auth.NevarRemoteUserMiddleware",
    )
    AUTHENTICATION_BACKENDS.insert(0, "accounts.auth.CrsidBackend")

# --- I18N / static / media ----------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Static URLs carry ?v=<content hash> so a deploy never meets stale CSS.
    "staticfiles": {"BACKEND": "core.storage.VersionedStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email --------------------------------------------------------------------
# On SRCF the local MTA accepts mail on localhost:25. Locally, emails are
# printed to the console so the mailer can be tested without sending anything.

if env_bool("REAL_EMAIL", default=False):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "Cambridge Mature Students Society <soc-maturesoc@srcf.net>"
)

# --- Uploads ------------------------------------------------------------------

# Keep profile photos modest: SRCF society accounts have finite quota.
MAX_UPLOAD_SIZE_MB = 5
