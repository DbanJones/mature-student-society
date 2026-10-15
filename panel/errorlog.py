"""Record server errors for the Site health page.

Without this, a page that crashes on the live site shows "Server Error
(500)" and leaves no trace: DEBUG is off, ADMINS is empty and gunicorn
keeps no access log. DatabaseErrorHandler is a logging handler (wired up in
settings.LOGGING) that turns each error Django logs into an ErrorEvent,
grouped by kind of error and place in the code, with a count.

What is kept, and what never is:

- the page's route pattern (``events/<int:pk>/``) and view name, never the
  address itself, whose query string can carry sign-in and undo tokens;
- the error's type and message, cut to 300 characters, with the secret
  key, database password, API keys and email addresses blanked out;
- the file, line and function of the portal's own code in the traceback
  (no variables, nothing from libraries);
- never form data, cookies or headers.

It never raises and never recurses: logging from inside the handler is
ignored, and an error about the database itself isn't written to it.
"""

import hashlib
import logging
import os
import re
import threading
import traceback

_guard = threading.local()
EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def _secrets():
    from django.conf import settings

    values = [getattr(settings, "SECRET_KEY", "")]
    for database in getattr(settings, "DATABASES", {}).values():
        values.append(database.get("PASSWORD", ""))
    try:
        from core.models import SiteConfig

        config = SiteConfig.objects.only("email_api_key", "geoapify_api_key").first()
        if config:
            values += [config.email_api_key, config.geoapify_api_key]
    except Exception:
        pass
    return [value for value in values if value and len(value) >= 6]


def redact(text, secrets=()):
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    return EMAIL.sub("[email]", text)


def _frames(exc_info, base):
    """The portal's own frames, innermost last: "path:line in function"."""
    out = []
    for frame in traceback.extract_tb(exc_info[2]):
        path = os.path.abspath(frame.filename)
        if not path.startswith(base) or os.sep + ".venv" + os.sep in path or os.sep + "site-packages" + os.sep in path:
            continue
        out.append(f"{os.path.relpath(path, base).replace(os.sep, '/')}:{frame.lineno} in {frame.name}")
    return out[-8:]


def fingerprint(kind, exc_type, frames, route):
    innermost = frames[-1].split(":")[0] + ":" + frames[-1].rsplit(" in ", 1)[-1] if frames else ""
    return hashlib.sha1(f"{kind}|{exc_type}|{innermost}|{route}".encode(), usedforsecurity=False).hexdigest()


class DatabaseErrorHandler(logging.Handler):
    def emit(self, record):
        if getattr(_guard, "busy", False):
            return
        _guard.busy = True
        try:
            self._record(record)
        except Exception:
            pass  # the stderr handler has the error; never make things worse
        finally:
            _guard.busy = False

    def _record(self, record):
        from django.conf import settings
        from django.db import DatabaseError, IntegrityError, connection, transaction
        from django.db.models import F
        from django.utils import timezone

        from panel.models import ErrorEvent

        exc_info = record.exc_info if record.exc_info and record.exc_info[0] else None
        if exc_info and issubclass(exc_info[0], DatabaseError) and not connection.is_usable():
            return  # the database is the problem: writing to it would only fail again
        request = getattr(record, "request", None)
        match = getattr(request, "resolver_match", None) if request is not None else None
        route = (getattr(match, "route", "") or "")[:200]
        view_name = (getattr(match, "view_name", "") or "")[:120]
        method = (getattr(request, "method", "") or "")[:10]
        secrets = _secrets()
        base = os.path.abspath(str(settings.BASE_DIR)) + os.sep
        if exc_info:
            exc_type = f"{exc_info[0].__module__}.{exc_info[0].__qualname__}".removeprefix("builtins.")
            message = str(exc_info[1])
            frames = _frames(exc_info, base)
        else:
            exc_type = ""
            message = record.getMessage()
            frames = []
        if request is not None and getattr(request, "path", ""):
            for address in (request.get_full_path(), request.path):
                if address and address != "/":
                    message = message.replace(address, route or "(page)")
        message = redact(message, secrets)[:300]
        kind = ErrorEvent.Kind.EXCEPTION if record.levelno >= logging.ERROR else ErrorEvent.Kind.WARNING
        key = fingerprint(kind, exc_type or record.name, frames, route or view_name or record.name)
        now = timezone.now()
        updated = ErrorEvent.objects.filter(fingerprint=key).update(
            count=F("count") + 1, last_seen=now, message=message,
        )
        if updated:
            ErrorEvent.objects.filter(fingerprint=key, resolved_at__isnull=False).update(
                resolved_at=None, resolved_by=None, reopened=True,
            )
            return
        try:
            with transaction.atomic():
                ErrorEvent.objects.create(
                    fingerprint=key, kind=kind, exc_type=exc_type[:120], message=message, route=route,
                    view_name=view_name, method=method, frames=redact("\n".join(frames), secrets)[:4000],
                    first_seen=now, last_seen=now,
                )
        except IntegrityError:  # the other worker recorded it first
            ErrorEvent.objects.filter(fingerprint=key).update(count=F("count") + 1, last_seen=now)
