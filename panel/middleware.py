"""Two middlewares for the site health checks. Both sit at the very top of
MIDDLEWARE, ahead of sessions and logins.

RequestTimingMiddleware counts requests per hour, by status and by how long
they took, and remembers the very slow ones. Counts are kept in the worker
and written at most once a minute (two small queries), so ordinary page
loads pay nothing.

HealthEndpointMiddleware answers two addresses before anything else runs:

- ``/healthz`` for an outside uptime monitor: "ok" when the site and its
  database work, "unavailable" (503) when the database doesn't. No
  cookies, no session, no login, nothing cached.
- ``/healthz/probe``, only for the site's own health check, which signs
  its requests with the secret key: it reports whether a forged Raven
  login header made it through Apache. Without a valid signature it is a
  plain 404, and it never logs anyone in (the login middleware hasn't run).
"""

import datetime
import json
import logging
import threading
import time

from django.db import connection
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 60
FLUSH_REQUESTS = 200
SLOW_MS = 5000
BUCKETS = (  # (field, upper bound in milliseconds)
    ("under_half", 500), ("under_1", 1000), ("under_2", 2000), ("under_5", 5000),
    ("under_10", 10_000), ("under_20", 20_000), ("over_20", None),
)
HEALTH_PATHS = ("/healthz", "/healthz/")
PROBE_PATH = "/healthz/probe"

# What this worker is doing right now, for gunicorn's worker_abort hook
# (gunicorn.conf.py), which logs the page a timed-out worker was stuck on.
IN_FLIGHT = {}

_lock = threading.Lock()
_pending = {}  # {hour: {field: count}}
_slow = []
_last_flush = [time.monotonic()]
_since_flush = [0]


def _bucket(ms):
    for field, bound in BUCKETS:
        if bound is None or ms < bound:
            return field
    return "over_20"


def _hour(now):
    return now.replace(minute=0, second=0, microsecond=0)


def record(ms, status, route="", view_name="", monitor=False, now=None):
    """Count one request (called for every request; no database access
    until the next flush)."""
    now = now or timezone.now()
    with _lock:
        counts = _pending.setdefault(_hour(now), {})
        counts["requests"] = counts.get("requests", 0) + 1
        if 400 <= status < 500:
            counts["s4xx"] = counts.get("s4xx", 0) + 1
        elif status >= 500:
            counts["s5xx"] = counts.get("s5xx", 0) + 1
        field = _bucket(ms)
        counts[field] = counts.get(field, 0) + 1
        if monitor:
            counts["monitor_hits"] = counts.get("monitor_hits", 0) + 1
        if ms >= SLOW_MS:
            _slow.append({"at": now, "route": route[:200], "view_name": view_name[:120],
                          "duration_ms": int(ms), "status": status})
            del _slow[:-50]
        _since_flush[0] += 1


def flush(force=False):
    """Write this worker's counts to the database. Returns True when it
    wrote. Never raises: losing a minute of counts beats a broken page."""
    from django.db import IntegrityError, transaction
    from django.db.models import F

    from panel.models import RequestStats, SlowRequest

    with _lock:
        due = force or _since_flush[0] >= FLUSH_REQUESTS or time.monotonic() - _last_flush[0] >= FLUSH_SECONDS
        if not due or (not _pending and not _slow):
            if due:
                _last_flush[0] = time.monotonic()
            return False
        pending, slow = dict(_pending), list(_slow)
        _pending.clear()
        _slow.clear()
        _since_flush[0] = 0
        _last_flush[0] = time.monotonic()
    try:
        for hour, counts in pending.items():
            updates = {field: F(field) + value for field, value in counts.items()}
            if not RequestStats.objects.filter(hour=hour).update(**updates):
                try:
                    with transaction.atomic():
                        RequestStats.objects.create(hour=hour, **counts)
                except IntegrityError:  # another worker created the hour first
                    RequestStats.objects.filter(hour=hour).update(**updates)
        if slow:
            SlowRequest.objects.bulk_create([SlowRequest(**row) for row in slow])
        return True
    except Exception:
        logger.debug("Couldn't write the request counts", exc_info=True)
        return False


def reset():
    """Forget unwritten counts (tests)."""
    with _lock:
        _pending.clear()
        _slow.clear()
        _since_flush[0] = 0
        _last_flush[0] = time.monotonic()


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .healthchecks.core import USER_AGENT

        started = time.monotonic()
        IN_FLIGHT.update(path_kind=_path_kind(request.path_info), started=time.time())
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
            return response
        finally:
            IN_FLIGHT.clear()
            ms = (time.monotonic() - started) * 1000
            match = getattr(request, "resolver_match", None)
            monitor = (request.path_info in HEALTH_PATHS
                       and request.META.get("HTTP_USER_AGENT", "") != USER_AGENT)
            try:
                record(ms, status, route=getattr(match, "route", "") or "",
                       view_name=getattr(match, "view_name", "") or "", monitor=monitor)
                flush()
            except Exception:
                logger.debug("Couldn't count the request", exc_info=True)


def _path_kind(path):
    """The first part of the address only (never the rest, which can hold
    tokens), for the worker-timeout log."""
    first = path.strip("/").split("/", 1)[0]
    return f"/{first}/" if first else "/"


class HealthEndpointMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path in HEALTH_PATHS:
            return self._healthz(request)
        if path in (PROBE_PATH, PROBE_PATH + "/"):
            return self._probe(request)
        return self.get_response(request)

    @staticmethod
    def _plain(text, status=200):
        response = HttpResponse(text, status=status, content_type="text/plain; charset=utf-8")
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex"
        return response

    def _healthz(self, request):
        if request.method not in ("GET", "HEAD"):
            return HttpResponseNotAllowed(["GET", "HEAD"])
        try:
            request.get_host()
        except Exception:  # DisallowedHost: not one of ours
            return HttpResponseBadRequest("bad host", content_type="text/plain")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            logger.error("/healthz: the database didn't answer", exc_info=True)
            return self._plain("unavailable\n", status=503)
        return self._plain("ok\n")

    def _probe(self, request):
        from .healthchecks.probes import FORGED_IP, PROBE_USER, read_probe_token

        nonce = read_probe_token(request.META.get("HTTP_X_MSS_HEALTH_TOKEN", ""))
        if request.method != "GET" or nonce is None:
            return self._plain("Not Found\n", status=404)
        seen = request.META.get("HTTP_X_MSS_REMOTE_USER")
        forwarded = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
        response = JsonResponse({
            "nonce": nonce,
            "remote_user": seen is not None,
            "remote_user_probe": seen is not None and PROBE_USER in seen,
            "proto": request.META.get("HTTP_X_FORWARDED_PROTO", ""),
            "xff_forged": bool(forwarded) and forwarded[-1] == FORGED_IP,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        })
        response["Cache-Control"] = "no-store"
        return response


def read_worker_timeouts(path, since):
    """[(time, path kind)] from gunicorn's worker_abort log since ``since``."""
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()[-500:]
    except OSError:
        return out
    for line in lines:
        try:
            entry = json.loads(line)
            at = datetime.datetime.fromisoformat(entry["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=datetime.timezone.utc)
        if at >= since:
            out.append((at, str(entry.get("path", ""))[:60]))
    return out
