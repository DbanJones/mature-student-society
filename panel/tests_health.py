"""Tests for the site health checks: /healthz and the signed probe, request
timing, error recording, every check's verdicts, the probes, the Site
health page, its pictures, and the health_check command."""

import datetime
import io
import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
from unittest import mock

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.management import call_command
from django.db.models import Sum
from django.test import Client, LiveServerTestCase, TestCase, override_settings
from django.urls import path, reverse
from django.utils import timezone

from accounts.models import User
from panel import middleware
from panel.healthchecks import checks, core, deploy, probes, visuals
from panel.healthchecks.core import Context, Result
from panel.jobs import heartbeat
from panel.models import (
    AuditLog,
    ErrorEvent,
    HealthSnapshot,
    JobHeartbeat,
    MailLog,
    RequestStats,
    SlowRequest,
)


def _boom(request, pk):
    raise KeyError(f"no venue for x.person@example.com, key {settings.SECRET_KEY}")


def _urls():
    from config import urls

    return [path("boom/<int:pk>/", _boom, name="boom")] + list(urls.urlpatterns)


urlpatterns = _urls()


def make_user(username, **extra):
    return User.objects.create_user(
        username=username, password="pw", first_name=username.title(), last_name="Tester",
        college="darwin", mobile="+44 7700 900123", email=f"{username}@cam.ac.uk", **extra,
    )


def ctx(probes_data=None, *, production=False, posix=None, trigger="", origin="https://www.example.org"):
    context = Context(full=True, site_url=origin)
    context.production = production
    if posix is not None:
        context.posix = posix
    context.trigger = trigger
    context.probes = probes_data or {}
    return context


def only_checks(*statuses):
    """Swap the registry for one quick check per status (for the command)."""
    registry = {}
    for index, status in enumerate(statuses):
        key = f"fake{index}"
        registry[key] = core.Check(key, f"Fake check {index}", "visitors", False,
                                   lambda context, s=status: Result(s, f"It is {s}.", fix="Do the thing."), index)
    return mock.patch.dict(core.REGISTRY, registry, clear=True)


# --- /healthz and the probe ----------------------------------------------------------------

class HealthEndpointTests(TestCase):
    def test_healthz_says_ok_without_a_session_or_a_cache(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok\n")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertFalse(response.cookies)
        self.assertEqual(self.client.head("/healthz/").status_code, 200)
        self.assertEqual(self.client.post("/healthz").status_code, 405)

    def test_healthz_refuses_a_host_that_isnt_ours(self):
        self.assertEqual(self.client.get("/healthz", HTTP_HOST="evil.example.com").status_code, 400)

    def test_healthz_says_unavailable_when_the_database_is_down(self):
        with mock.patch.object(middleware.connection, "cursor", side_effect=RuntimeError("down")), \
                self.assertLogs("panel.middleware", "ERROR"), self.assertLogs("django.request", "ERROR"):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable\n")

    def test_the_probe_is_a_plain_404_without_a_valid_fresh_token(self):
        self.assertEqual(self.client.get("/healthz/probe").status_code, 404)
        self.assertEqual(self.client.get("/healthz/probe", HTTP_X_MSS_HEALTH_TOKEN="forged:token").status_code, 404)
        other = signing.TimestampSigner(salt="something else").sign("abc")
        self.assertEqual(self.client.get("/healthz/probe", HTTP_X_MSS_HEALTH_TOKEN=other).status_code, 404)
        with mock.patch("time.time", return_value=time.time() - 120):
            _nonce, stale = probes.probe_token()  # made two minutes ago
        self.assertEqual(self.client.get("/healthz/probe", HTTP_X_MSS_HEALTH_TOKEN=stale).status_code, 404)

    def test_the_probe_reports_what_reached_the_site(self):
        nonce, token = probes.probe_token()
        answer = self.client.get("/healthz/probe", HTTP_X_MSS_HEALTH_TOKEN=token,
                                 HTTP_X_MSS_REMOTE_USER=probes.PROBE_USER, HTTP_X_FORWARDED_PROTO="http",
                                 HTTP_X_FORWARDED_FOR=f"{probes.FORGED_IP}, 203.0.113.9").json()
        self.assertEqual(answer["nonce"], nonce)
        self.assertTrue(answer["remote_user_probe"])
        self.assertEqual(answer["proto"], "http")
        self.assertFalse(answer["xff_forged"])  # the proxy added the real address last
        nonce, token = probes.probe_token()
        answer = self.client.get("/healthz/probe", HTTP_X_MSS_HEALTH_TOKEN=token,
                                 HTTP_X_FORWARDED_FOR=probes.FORGED_IP).json()
        self.assertFalse(answer["remote_user"])
        self.assertTrue(answer["xff_forged"])

    @override_settings(
        MIDDLEWARE=settings.MIDDLEWARE[:7] + ["accounts.auth.NevarRemoteUserMiddleware"] + settings.MIDDLEWARE[7:],
        AUTHENTICATION_BACKENDS=["accounts.auth.CrsidBackend", "django.contrib.auth.backends.ModelBackend"],
    )
    def test_the_probe_never_logs_anyone_in(self):
        self.assertEqual(settings.MIDDLEWARE[6], "django.contrib.auth.middleware.AuthenticationMiddleware")
        _nonce, token = probes.probe_token()
        response = self.client.get("/healthz/probe", HTTP_X_MSS_HEALTH_TOKEN=token, HTTP_X_MSS_REMOTE_USER="zz999")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="zz999").exists())
        self.assertFalse(response.cookies)
        # whereas the same header on an ordinary page would (Apache must strip it)
        self.client.get("/", HTTP_X_MSS_REMOTE_USER="zz999")
        self.assertTrue(User.objects.filter(username="zz999").exists())


# --- request timing ------------------------------------------------------------------------

class RequestTimingTests(TestCase):
    def setUp(self):
        middleware.reset()

    def tearDown(self):
        middleware.reset()

    def test_requests_are_counted_per_hour_by_status_speed_and_monitor(self):
        self.client.get("/healthz", HTTP_USER_AGENT="UptimeRobot/2.0")
        self.client.get("/healthz", HTTP_USER_AGENT=core.USER_AGENT)  # the site checking itself
        self.client.get("/no-such-page/")
        self.assertTrue(middleware.flush(force=True))
        totals = RequestStats.objects.aggregate(r=Sum("requests"), n=Sum("s4xx"), m=Sum("monitor_hits"),
                                                e=Sum("s5xx"), a=Sum("under_half"), b=Sum("under_1"),
                                                c=Sum("under_2"), d=Sum("under_5"))
        self.assertEqual((totals["r"], totals["n"], totals["m"], totals["e"]), (3, 1, 1, 0))
        self.assertEqual(totals["a"] + totals["b"] + totals["c"] + totals["d"], 3)
        stamp = RequestStats.objects.first().hour
        self.assertEqual((stamp.minute, stamp.second, stamp.microsecond), (0, 0, 0))

    def test_slow_requests_are_kept_with_their_route_only(self):
        middleware.record(6200, 200, route="events/<int:pk>/", view_name="events:detail_pk")
        middleware.record(300, 200, route="", view_name="")
        middleware.flush(force=True)
        slow = SlowRequest.objects.get()
        self.assertEqual((slow.route, slow.duration_ms, slow.status), ("events/<int:pk>/", 6200, 200))
        self.assertEqual(RequestStats.objects.get().under_10, 1)

    def test_a_flush_that_fails_never_breaks_a_page(self):
        middleware.record(100, 200)
        with mock.patch.object(RequestStats.objects, "filter", side_effect=RuntimeError("db gone")):
            self.assertFalse(middleware.flush(force=True))
            self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_counts_wait_for_a_minute_or_two_hundred_requests(self):
        middleware.flush(force=True)
        middleware.record(100, 200)
        self.assertFalse(middleware.flush())
        self.assertFalse(RequestStats.objects.exists())
        for _ in range(middleware.FLUSH_REQUESTS):
            middleware.record(100, 200)
        self.assertTrue(middleware.flush())
        self.assertEqual(RequestStats.objects.get().requests, middleware.FLUSH_REQUESTS + 1)

    def test_worker_timeouts_are_read_from_gunicorns_log(self):
        now = timezone.now()
        with tempfile.TemporaryDirectory() as folder:
            log = os.path.join(folder, "worker-timeouts.log")
            with open(log, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"at": (now - datetime.timedelta(days=9)).isoformat(), "path": "/old/"}) + "\n")
                handle.write("not json\n")
                handle.write(json.dumps({"at": (now - datetime.timedelta(hours=2)).isoformat(), "path": "/events/"}) + "\n")
            found = middleware.read_worker_timeouts(log, now - datetime.timedelta(days=7))
        self.assertEqual([part for _, part in found], ["/events/"])
        self.assertEqual(middleware.read_worker_timeouts("/no/such/file", now), [])


# --- error recording -----------------------------------------------------------------------

@override_settings(ROOT_URLCONF="panel.tests_health")
class ErrorLogTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)
        # These crashes are on purpose: keep their tracebacks out of the test output.
        for handler in logging.getLogger("django.request").handlers:
            if isinstance(handler, logging.StreamHandler):
                quiet = mock.patch.object(handler, "stream", io.StringIO())
                quiet.start()
                self.addCleanup(quiet.stop)

    def test_a_crash_shows_a_friendly_page_and_is_recorded_without_the_address(self):
        response = self.client.get("/boom/7/?token=s3cret-undo-token")
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "Something went wrong on our side", status_code=500)
        error = ErrorEvent.objects.get()
        self.assertEqual((error.route, error.view_name, error.method, error.exc_type), ("boom/<int:pk>/", "boom", "GET", "KeyError"))
        stored = " ".join([error.message, error.frames, error.route])
        for secret in ("s3cret-undo-token", "/boom/7/", "x.person@example.com", settings.SECRET_KEY):
            self.assertNotIn(secret, stored)
        self.assertIn("[email]", error.message)
        self.assertIn("[redacted]", error.message)
        self.assertIn("panel/tests_health.py", error.frames)
        self.assertNotIn("site-packages", error.frames)

    def test_the_same_crash_is_counted_and_comes_back_after_being_fixed(self):
        self.client.get("/boom/1/")
        self.client.get("/boom/2/")
        error = ErrorEvent.objects.get()
        self.assertEqual(error.count, 2)
        admin = make_user("errfix", is_portal_admin=True, is_super_admin=True)
        self.client.force_login(admin)
        self.client.post(reverse("panel:health_error_resolve", args=[error.pk]))
        error.refresh_from_db()
        self.assertIsNotNone(error.resolved_at)
        self.assertTrue(AuditLog.objects.filter(action="resolve_error").exists())
        self.client.get("/boom/3/")
        error.refresh_from_db()
        self.assertIsNone(error.resolved_at)
        self.assertTrue(error.reopened)
        self.assertEqual(error.count, 3)

    def test_recording_never_makes_things_worse(self):
        with mock.patch.object(ErrorEvent.objects, "filter", side_effect=RuntimeError("db gone")):
            response = self.client.get("/boom/1/")
        self.assertEqual(response.status_code, 500)
        self.assertFalse(ErrorEvent.objects.exists())

    def test_only_super_admins_can_mark_errors_fixed(self):
        error = ErrorEvent.objects.create(fingerprint="f" * 40, exc_type="KeyError", route="x/")
        self.client.force_login(make_user("plainadm", is_portal_admin=True))
        self.assertEqual(self.client.post(reverse("panel:health_error_resolve", args=[error.pk])).status_code, 403)


# --- the framework -------------------------------------------------------------------------

class FrameworkTests(TestCase):
    def test_only_warn_and_fail_colour_the_verdict(self):
        self.assertEqual(core.worst(["ok", "unknown", "off", "na"]), "ok")
        self.assertEqual(core.worst(["ok", "warn", "unknown"]), "warn")
        self.assertEqual(core.worst(["warn", "fail", "ok"]), "fail")
        self.assertEqual(core.worst(["na", "off"]), "off")
        self.assertEqual(core.worst([]), "unknown")
        with self.assertRaises(ValueError):
            Result("purple", "no such status")

    def test_a_check_that_breaks_says_so_instead_of_breaking_the_page(self):
        def broken(context):
            raise RuntimeError("bad check")

        with mock.patch.dict(core.REGISTRY, {"broken": core.Check("broken", "Broken", "visitors", False, broken, 0)},
                             clear=True):
            rows = core.evaluate(Context())
        self.assertEqual(rows[0]["status"], "unknown")
        self.assertIn("RuntimeError: bad check", rows[0]["details"][0][1])

    def test_probes_stay_inside_the_time_budget(self):
        release = threading.Event()

        def stuck(context, deadline):
            release.wait(10)
            return {}

        try:
            with mock.patch.dict(core.PROBES, {"self": stuck}, clear=True):
                started = time.monotonic()
                snapshot = core.run(full=True, save=False, budget=0.5)
                elapsed = time.monotonic() - started
        finally:
            release.set()
        self.assertLess(elapsed, 4)
        rows = {row["key"]: row for row in snapshot.results}
        self.assertEqual(rows["site_reachable"]["status"], "unknown")

    def test_a_probe_that_raises_is_reported(self):
        def bad(context, deadline):
            raise OSError("no route")

        context = Context(full=True)
        with mock.patch.dict(core.PROBES, {"self": bad}, clear=True):
            core.run_probes(context, budget=2)
        self.assertIn("OSError: no route", context.probe("self")["error"])

    def test_a_quick_run_never_runs_a_probe(self):
        def never(context, deadline):
            raise AssertionError("a quick run ran a probe")

        with mock.patch.dict(core.PROBES, {name: never for name in core.PROBES}):
            snapshot = core.run(full=False, trigger="deploy")
        self.assertEqual(snapshot.trigger, "deploy")
        self.assertFalse(snapshot.full)
        self.assertNotIn("site_reachable", snapshot.statuses)
        self.assertIn("database", snapshot.statuses)
        self.assertEqual(snapshot.overall, core.worst(snapshot.statuses.values()))

    def test_there_are_24_checks_in_eight_sections(self):
        self.assertEqual(len(core.REGISTRY), 24)
        self.assertEqual({item.section for item in core.REGISTRY.values()}, {key for key, _ in core.SECTIONS})


# --- the checks ----------------------------------------------------------------------------

GOOD_ANSWER = {"nonce": "n", "remote_user": False, "remote_user_probe": False, "proto": "https", "xff_forged": False}


def self_probe(**changes):
    data = {
        "origin": "https://www.example.org",
        "healthz": {"status": 200, "body": b"ok\n", "ms": 40},
        "home": {"status": 200, "ms": 300},
        "guard X-MSS-Remote-User": {"status": 200, "answer": dict(GOOD_ANSWER)},
        "guard X_MSS_Remote_User": {"status": 200, "answer": dict(GOOD_ANSWER)},
        "raven": {"status": 302, "headers": {"location": "https://nevar.srcf.net/login?x=1"}},
    }
    data.update(changes)
    return data


class VisitorChecksTests(TestCase):
    def test_site_reachable(self):
        self.assertEqual(checks.site_reachable(ctx({"self": self_probe()})).status, "ok")
        slow = self_probe(home={"status": 200, "ms": 4200})
        self.assertEqual(checks.site_reachable(ctx({"self": slow})).status, "warn")
        down = self_probe(healthz={"status": 503, "body": b"unavailable\n", "ms": 10})
        self.assertIn("database", checks.site_reachable(ctx({"self": down})).summary)
        self.assertEqual(checks.site_reachable(ctx({"self": down})).status, "fail")
        gateway = self_probe(healthz={"status": 502, "body": b"", "ms": 10})
        self.assertEqual(checks.site_reachable(ctx({"self": gateway})).status, "fail")
        apache = self_probe(healthz={"status": 503, "body": b"<html><title>503 Service Unavailable</title>", "ms": 5})
        result = checks.site_reachable(ctx({"self": apache}))
        self.assertEqual(result.status, "fail")
        self.assertNotIn("database", result.summary)
        self.assertIn("gunicorn", result.webmaster)
        lost = self_probe(healthz={"error": "URLError: no route", "ms": 5})
        self.assertEqual(checks.site_reachable(ctx({"self": lost})).status, "unknown")
        self.assertEqual(checks.site_reachable(ctx({"self": {"skipped": True}})).status, "na")
        self.assertEqual(checks.site_reachable(ctx({})).status, "unknown")  # ran out of time

    def test_static_served(self):
        rows = [{"name": "css/base.css", "status": "ok", "url": ""}, {"name": "css/panel.css", "status": "stale", "url": ""}]
        base = {"served": rows, "problems": rows[1:], "ok": 1, "copy": {"current": True, "root": "/srv/static"}}
        result = checks.static_served(ctx({"static": dict(base, cause="folder")}))
        self.assertEqual(result.status, "fail")
        self.assertIn("DJANGO_STATIC_ROOT", result.webmaster)
        self.assertEqual(result.metric, "1/2 files")
        result = checks.static_served(ctx({"static": dict(base, cause="folder", copy={"current": False})}))
        self.assertIn("Ask the webmaster to restart the portal", result.fix)
        result = checks.static_served(ctx({"static": dict(base, cause="permissions")}))
        self.assertIn("srcf-soc-permfix", result.webmaster)
        self.assertEqual(checks.static_served(ctx({"static": dict(base, cause="fetch")})).status, "unknown")
        fine = {"served": rows[:1], "problems": [], "ok": 1, "copy": {"current": True}, "cause": ""}
        self.assertEqual(checks.static_served(ctx({"static": fine})).status, "ok")
        self.assertEqual(checks.static_served(ctx({"static": {"skipped": True}})).status, "na")

    def test_static_copy(self):
        self.assertEqual(checks.static_copy(ctx()).status, "na")  # development serves from source
        with tempfile.TemporaryDirectory() as root, override_settings(STATIC_ROOT=root, STATIC_VERSION_FROM_ROOT=True):
            self.assertEqual(checks.static_copy(ctx()).status, "warn")
            call_command("sync_static", stdout=io.StringIO())
            self.assertEqual(checks.static_copy(ctx()).status, "ok")
        with override_settings(STATIC_VERSION_FROM_ROOT=False):
            self.assertIn("DJANGO_STATIC_ROOT", checks.static_copy(ctx(production=True)).webmaster)

    def test_server_errors(self):
        self.assertEqual(checks.server_errors(ctx()).status, "ok")
        old = ErrorEvent.objects.create(fingerprint="a" * 40, exc_type="KeyError", view_name="events:detail_pk",
                                        last_seen=timezone.now() - datetime.timedelta(days=9))
        self.assertEqual(checks.server_errors(ctx()).status, "ok")  # nothing in the last week
        old.last_seen = timezone.now() - datetime.timedelta(days=2)
        old.save()
        self.assertEqual(checks.server_errors(ctx()).status, "warn")
        self.assertIn("an event page", checks.server_errors(ctx()).summary)
        self.assertNotIn("events:detail_pk", checks.server_errors(ctx()).summary)  # code names stay in the details
        home = ErrorEvent.objects.create(fingerprint="b" * 40, exc_type="TypeError", view_name="core:home",
                                         last_seen=timezone.now() - datetime.timedelta(hours=5))
        result = checks.server_errors(ctx())
        self.assertEqual(result.status, "warn")  # hours ago: maybe still broken, but not "now"
        self.assertIn("The home page crashed for someone 5\xa0hours ago", result.summary)
        self.assertNotIn("now", result.summary)
        home.last_seen = timezone.now()
        home.save()
        result = checks.server_errors(ctx())
        self.assertEqual(result.status, "fail")  # the home page, just now
        self.assertIn("Tell the webmaster now", result.fix)
        ErrorEvent.objects.update(resolved_at=timezone.now())
        self.assertEqual(checks.server_errors(ctx()).status, "ok")
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)
        RequestStats.objects.create(hour=hour, requests=40, s5xx=12)
        result = checks.server_errors(ctx())
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.summary, "Visitors are getting error pages now (12 this hour).")

    def test_response_times(self):
        self.assertEqual(checks.response_times(ctx()).status, "unknown")
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)
        stats = RequestStats.objects.create(hour=hour, requests=100, under_half=90, under_1=5, under_2=5)
        result = checks.response_times(ctx())
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.metric, "95% under 1 s")
        stats.under_half, stats.under_5 = 80, 10
        stats.save()
        self.assertEqual(checks.response_times(ctx()).status, "warn")
        stats.under_half, stats.under_5, stats.over_20 = 89, 0, 1
        stats.save()
        self.assertIn("20 seconds", checks.response_times(ctx()).summary)
        now = timezone.now()
        with mock.patch.object(checks, "worker_timeouts", return_value=[(now, "/events/")] * 3):
            self.assertEqual(checks.response_times(ctx()).status, "fail")
        with mock.patch.object(checks, "worker_timeouts",
                               return_value=[(now - datetime.timedelta(days=3), "/events/")]):
            self.assertEqual(checks.response_times(ctx()).status, "warn")

    def test_uptime_monitor(self):
        self.assertEqual(checks.uptime_monitor(ctx()).status, "na")
        result = checks.uptime_monitor(ctx(production=True))
        self.assertEqual(result.status, "off")
        self.assertIn("https://www.example.org/healthz", result.webmaster)
        RequestStats.objects.create(hour=timezone.now().replace(minute=0, second=0, microsecond=0), requests=12,
                                    monitor_hits=12)
        self.assertEqual(checks.uptime_monitor(ctx(production=True)).status, "ok")


@override_settings(RAVEN_MODE="header", ALLOWED_HOSTS=["www.example.org", "mss.soc.srcf.net", "testserver"])
class SecurityChecksTests(TestCase):
    def test_login_guard(self):
        self.assertEqual(checks.login_guard(ctx({"self": self_probe()})).status, "ok")
        leak = self_probe(**{"guard X-MSS-Remote-User": {"status": 200, "answer": dict(GOOD_ANSWER, remote_user_probe=True)}})
        result = checks.login_guard(ctx({"self": leak}))
        self.assertEqual(result.status, "fail")
        self.assertIn("RequestHeader unset X-MSS-Remote-User", result.webmaster)
        look_alike = self_probe(**{"guard X_MSS_Remote_User": {"status": 200, "answer": dict(GOOD_ANSWER, remote_user_probe=True)}})
        result = checks.login_guard(ctx({"self": look_alike}))
        self.assertEqual(result.status, "fail")
        self.assertIn("gunicorn", result.webmaster)
        open_raven = self_probe(raven={"status": 200, "headers": {}})
        self.assertEqual(checks.login_guard(ctx({"self": open_raven})).status, "fail")
        own = self_probe(raven={"status": 302, "headers": {"location": "https://www.example.org/accounts/login/"}})
        self.assertEqual(checks.login_guard(ctx({"self": own})).status, "warn")
        relative = self_probe(raven={"status": 302, "headers": {"location": "/accounts/login/"}})
        self.assertEqual(checks.login_guard(ctx({"self": relative})).status, "warn")
        sibling = self_probe(raven={"status": 301, "headers": {"location": "https://mss.soc.srcf.net/accounts/raven/"}})
        self.assertEqual(checks.login_guard(ctx({"self": sibling})).status, "unknown")
        missing = self_probe(raven={"status": 404, "headers": {}})
        self.assertEqual(checks.login_guard(ctx({"self": missing})).status, "warn")
        proto = self_probe(**{"guard X-MSS-Remote-User": {"status": 200, "answer": dict(GOOD_ANSWER, proto="http")}})
        self.assertIn("X-Forwarded-Proto", checks.login_guard(ctx({"self": proto})).summary)
        unanswered = self_probe(**{"guard X-MSS-Remote-User": {"status": 404, "answer": None}})
        self.assertEqual(checks.login_guard(ctx({"self": unanswered})).status, "unknown")
        unanswered_and_proto = self_probe(**{
            "guard X-MSS-Remote-User": {"status": None, "error": "timed out", "answer": None},
            "guard X_MSS_Remote_User": {"status": 200, "answer": dict(GOOD_ANSWER, proto="http")},
        })
        result = checks.login_guard(ctx({"self": unanswered_and_proto}))
        self.assertEqual(result.status, "warn")
        self.assertTrue(result.summary.startswith("The forged-login test got no answer"))
        self.assertNotIn("refused", result.summary)
        self.assertEqual(checks.login_guard(ctx({})).status, "unknown")
        with override_settings(RAVEN_MODE="dev"):
            self.assertEqual(checks.login_guard(ctx({"self": leak})).status, "na")

    def test_socket_private(self):
        self.assertEqual(checks.socket_private(ctx(posix=False)).status, "na")
        self.assertEqual(checks.socket_private(ctx(posix=True, trigger="deploy")).status, "unknown")
        closed = os.stat_result((0o140770, 0, 0, 0, 0, 1234, 0, 0, 0, 0))
        opened = os.stat_result((0o140777, 0, 0, 0, 0, 1234, 0, 0, 0, 0))
        with mock.patch.object(checks.os, "stat", return_value=closed):
            self.assertEqual(checks.socket_private(ctx(posix=True)).status, "ok")
        with mock.patch.object(checks.os, "stat", return_value=opened):
            result = checks.socket_private(ctx(posix=True))
        self.assertEqual(result.status, "fail")
        self.assertIn("log in as any member", result.summary)
        readable_only = os.stat_result((0o140774, 0, 0, 0, 0, 1234, 0, 0, 0, 0))  # connecting needs write
        with mock.patch.object(checks.os, "stat", return_value=readable_only):
            self.assertEqual(checks.socket_private(ctx(posix=True)).status, "ok")
        with mock.patch.object(checks.os, "stat", side_effect=FileNotFoundError("gone")):
            self.assertEqual(checks.socket_private(ctx(posix=True)).status, "unknown")
        with override_settings(RAVEN_MODE="dev"):
            self.assertEqual(checks.socket_private(ctx(posix=True)).status, "na")

    def test_production_settings(self):
        self.assertEqual(checks.production_settings(ctx()).status, "na")
        with mock.patch.object(checks, "_deploy_warnings", return_value=[]):
            with override_settings(SITE_URL="https://www.example.org"):
                self.assertEqual(checks.production_settings(ctx(production=True)).status, "ok")
            with override_settings(SITE_URL="https://elsewhere.example.net"):
                self.assertEqual(checks.production_settings(ctx(production=True)).status, "fail")
            with override_settings(SITE_URL=""):
                self.assertIn("DJANGO_SITE_URL", checks.production_settings(ctx(production=True)).summary)
            with override_settings(SITE_URL="https://www.example.org", RAVEN_MODE="oidc"):
                self.assertEqual(checks.production_settings(ctx(production=True)).status, "fail")
        with override_settings(SITE_URL="https://www.example.org"), \
                mock.patch.object(checks, "_deploy_warnings", return_value=[("security.W012", "cookie")]):
            result = checks.production_settings(ctx(production=True))
        self.assertEqual(result.status, "warn")
        self.assertIn(["security.W012", "cookie"], [list(item) for item in result.details])
        with override_settings(SITE_URL=""), mock.patch.object(checks, "_deploy_warnings", return_value=[]):
            self.assertIn("(set DJANGO_SITE_URL)", checks.production_settings(ctx(production=True)).summary)

    def test_the_deployment_check_is_cached_but_follows_settings_changes(self):
        checks._deploy_warnings.cache_clear()
        first = checks._deploy_warnings()
        with override_settings(SESSION_COOKIE_SECURE=not settings.SESSION_COOKIE_SECURE):
            self.assertNotEqual(checks._deploy_warnings(), first)
        self.assertEqual(checks._deploy_warnings(), first)

    def test_https_certificate(self):
        def host(**extra):
            return dict({"host": "www.example.org", "days": 60.0, "expires": "2026-12-10", "issuer": "Let's Encrypt",
                         "http_status": 301, "redirects_to_https": True}, **extra)

        self.assertEqual(checks.https_certificate(ctx()).status, "na")
        result = checks.https_certificate(ctx({"tls": {"hosts": [host()]}}, production=True))
        self.assertEqual((result.status, result.metric), ("ok", "60 days left"))
        self.assertEqual(checks.https_certificate(ctx({"tls": {"hosts": [host(days=12.0)]}}, production=True)).status, "warn")
        self.assertEqual(checks.https_certificate(ctx({"tls": {"hosts": [host(days=3.0)]}}, production=True)).status, "fail")
        self.assertEqual(checks.https_certificate(ctx({"tls": {"hosts": [host(http_status=200)]}}, production=True)).status, "warn")
        invalid = {"host": "www.example.org", "invalid": "certificate has expired"}
        self.assertEqual(checks.https_certificate(ctx({"tls": {"hosts": [invalid]}}, production=True)).status, "fail")
        lost = {"host": "www.example.org", "error": "timed out"}
        self.assertEqual(checks.https_certificate(ctx({"tls": {"hosts": [lost]}}, production=True)).status, "unknown")

    def test_private_files(self):
        self.assertEqual(checks.private_files(ctx()).status, "na")
        self.assertEqual(checks.private_files(ctx({"files": {"skipped": True}}, production=True)).status, "na")
        clean = {"env_open": False, "media_open": 0, "media_checked": 40, "loose": []}
        self.assertEqual(checks.private_files(ctx({"files": clean}, production=True)).status, "ok")
        result = checks.private_files(ctx({"files": dict(clean, env_open=True)}, production=True))
        self.assertEqual(result.status, "fail")
        self.assertIn("chmod 660 .env", result.webmaster)
        self.assertEqual(checks.private_files(ctx({"files": dict(clean, media_open=3)}, production=True)).status, "warn")
        self.assertEqual(checks.private_files(ctx({"files": dict(clean, loose=["site.zip"])}, production=True)).status, "warn")
        result = checks.private_files(ctx({"files": dict(clean, loose_secret=[".env.bak", "db.sqlite3"])}, production=True))
        self.assertEqual(result.status, "fail")
        self.assertIn(".env.bak", result.summary)
        self.assertIn("change those as for .env", result.webmaster)
        personal = checks.private_files(ctx({"files": dict(clean, loose_personal=["members.xlsx"])}, production=True))
        self.assertEqual(personal.status, "fail")
        self.assertIn("members' details", personal.summary)

    def test_public_files(self):
        self.assertEqual(checks.public_files(ctx()).status, "na")
        clean = {"public_root": "/srv/public_html", "public_risky": [], "public_data": [], "htaccess_unsets": True,
                 "raven_gated": True}
        self.assertEqual(checks.public_files(ctx({"files": clean}, production=True)).status, "ok")
        self.assertEqual(checks.public_files(ctx({"files": dict(clean, public_risky=[".env"])}, production=True)).status, "fail")
        self.assertEqual(checks.public_files(ctx({"files": dict(clean, htaccess_unsets=False)}, production=True)).status, "fail")
        self.assertEqual(checks.public_files(ctx({"files": dict(clean, raven_gated=False)}, production=True)).status, "fail")
        self.assertEqual(checks.public_files(ctx({"files": dict(clean, public_data=["site.zip"])}, production=True)).status, "warn")
        self.assertEqual(checks.public_files(ctx({"files": dict(clean, public_personal=["list.csv"])}, production=True)).status, "fail")
        unread = checks.public_files(ctx({"files": dict(clean, htaccess_unsets=None)}, production=True))
        self.assertEqual(unread.status, "unknown")  # never claims rules it couldn't read
        self.assertNotIn("in place", unread.summary)
        self.assertEqual(checks.public_files(ctx({"files": dict(clean, public_root="")}, production=True)).status, "unknown")
        missing = {"public_root": "/no/such/public_html", "public_found": False}
        self.assertEqual(checks.public_files(ctx({"files": missing}, production=True)).status, "unknown")


class EmailAndJobChecksTests(TestCase):
    def test_email_setup(self):
        self.assertEqual(checks.email_setup(ctx()).status, "off")
        self.assertEqual(checks.email_setup(ctx(production=True)).status, "warn")
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="localhost",
                               EMAIL_PORT=25, DEFAULT_FROM_EMAIL="MSS <mss-webmaster@srcf.net>"):
            self.assertEqual(checks.email_setup(ctx(production=True)).status, "ok")
            with override_settings(DEFAULT_FROM_EMAIL="MSS <maturesoc@gmail.com>"):
                self.assertIn("gmail.com", checks.email_setup(ctx(production=True)).summary)
            failed = MailLog.objects.create(subject="What's On", body="b", recipients="20 addresses", ok=False,
                                            error="2 addresses not sent: refused", audience="members")
            result = checks.email_setup(ctx(production=True))
            self.assertIn("refused", result.summary)
            self.client.force_login(make_user("mailfix", is_portal_admin=True))
            self.client.post(result.action["url"])
            failed.refresh_from_db()
            self.assertIn("2 addresses not sent: refused (marked handled by Mailfix Tester", failed.error)
            self.assertEqual(checks.email_setup(ctx(production=True)).status, "ok")
            failed.delete()
            from panel.views import STARTED

            stuck = MailLog.objects.create(subject="What's On", body="b", recipients="40 addresses (sending)",
                                           ok=False, error=STARTED, audience="members")
            MailLog.objects.filter(pk=stuck.pk).update(sent_at=timezone.now() - datetime.timedelta(hours=1))
            result = checks.email_setup(ctx(production=True))
            self.assertEqual(result.status, "warn")
            self.assertIn("cut off part-way", result.summary)
            self.assertEqual(result.action["url"], reverse("panel:health_mail_handled", args=[stuck.pk]))
            admin = make_user("mailadm", is_portal_admin=True)
            self.client.force_login(admin)
            self.client.post(result.action["url"])
            stuck.refresh_from_db()
            self.assertIn("marked handled by", stuck.error)
            self.assertEqual(checks.email_setup(ctx(production=True)).status, "ok")
            self.assertEqual(self.client.post(result.action["url"]).status_code, 404)  # only cut-off sends

    def test_email_server(self):
        self.assertEqual(checks.email_server(ctx({"smtp": {"skipped": True}})).status, "off")
        self.assertEqual(checks.email_server(ctx({"smtp": {"host": "localhost", "port": 25, "code": 250, "ms": 9}})).status, "ok")
        self.assertEqual(checks.email_server(ctx({"smtp": {"host": "localhost", "port": 25,
                                                            "error": "ConnectionRefusedError"}})).status, "fail")
        self.assertEqual(checks.email_server(ctx({"smtp": {"host": "localhost", "port": 25, "error": "timed out",
                                                            "timed_out": True}})).status, "warn")
        elsewhere = {"host": "localhost", "port": 25, "error": "ConnectionRefusedError", "elsewhere": True,
                     "from": "sinkhole"}
        result = checks.email_server(ctx({"smtp": elsewhere}))
        self.assertEqual(result.status, "unknown")
        self.assertIn("sinkhole", result.summary)
        answered_elsewhere = {"host": "localhost", "port": 25, "code": 250, "ms": 3, "elsewhere": True, "from": "sinkhole"}
        self.assertEqual(checks.email_server(ctx({"smtp": answered_elsewhere})).status, "unknown")

    def test_scheduled_jobs(self):
        self.assertEqual(checks.scheduled_jobs(ctx()).status, "off")
        self.assertEqual(checks.scheduled_jobs(ctx(production=True)).status, "unknown")  # tables just made
        self.assertTrue(checks._installed_lately(ctx()))
        later = ctx(production=True)
        later.now = timezone.now() + datetime.timedelta(hours=2)
        self.assertFalse(checks._installed_lately(later))
        self.assertEqual(checks.scheduled_jobs(later).status, "warn")
        call_command("close_polls", stdout=io.StringIO())
        beat = JobHeartbeat.objects.get(name="close_polls")
        self.assertTrue(beat.last_ok)
        for name in ("process_formals", "process_notifications"):
            JobHeartbeat.objects.create(name=name, last_finished=timezone.now(), last_ok=True)
        self.assertEqual(checks.scheduled_jobs(ctx(production=True)).status, "ok")
        beat.last_finished = timezone.now() - datetime.timedelta(hours=3)
        beat.save()
        self.assertIn("last ran", checks.scheduled_jobs(ctx(production=True)).summary)
        beat.last_finished, beat.last_ok, beat.last_error = timezone.now(), False, "boom"
        beat.save()
        self.assertEqual(checks.scheduled_jobs(ctx(production=True)).status, "warn")
        beat.last_ok = True
        beat.save()
        JobHeartbeat.objects.create(name="health_check", last_finished=timezone.now() - datetime.timedelta(days=3),
                                    last_ok=True)
        self.assertIn("daily health check", checks.scheduled_jobs(ctx(production=True)).summary)

    def test_a_heartbeat_before_its_table_exists_is_one_quiet_line(self):
        from django.db import DatabaseError

        with mock.patch.object(JobHeartbeat.objects, "update_or_create", side_effect=DatabaseError("no such table")), \
                self.assertLogs("panel.jobs", "INFO") as logged:
            with heartbeat("close_polls"):
                pass
        self.assertTrue(all(record.exc_info is None for record in logged.records))
        self.assertTrue(all(record.levelname == "INFO" for record in logged.records))

    def test_a_heartbeat_records_failure_and_lets_the_error_through(self):
        with self.assertRaises(ValueError):
            with heartbeat("demo"):
                raise ValueError("it broke")
        beat = JobHeartbeat.objects.get(name="demo")
        self.assertFalse(beat.last_ok)
        self.assertIn("ValueError: it broke", beat.last_error)
        with heartbeat("demo") as running:
            running.detail = "fine"
        beat.refresh_from_db()
        self.assertTrue(beat.last_ok)
        self.assertEqual((beat.last_error, beat.detail), ("", "fine"))

    def test_backups(self):
        self.assertEqual(checks.backups(ctx()).status, "na")
        with mock.patch.dict(os.environ, {"MSS_BACKUP_DIR": ""}):
            result = checks.backups(ctx(production=True))
        self.assertEqual(result.status, "off")  # never red until a nightly backup is promised
        self.assertIn("MSS_BACKUP_DIR", result.webmaster)
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"MSS_BACKUP_DIR": os.path.join(folder, "missing")}):
                self.assertEqual(checks.backups(ctx(production=True)).status, "fail")
            with mock.patch.dict(os.environ, {"MSS_BACKUP_DIR": folder}):
                self.assertEqual(checks.backups(ctx(production=True)).status, "warn")  # empty
                with open(os.path.join(folder, "env-2026-10-11"), "w") as handle:
                    handle.write("SECRET=1")
                self.assertEqual(checks.backups(ctx(production=True)).status, "warn")  # a copy of .env isn't a backup
                dump = os.path.join(folder, "db-3.sql.gz")
                with open(dump, "wb") as handle:
                    handle.write(b"x" * 2048)
                self.assertEqual(checks.backups(ctx(production=True)).status, "ok")
                day_and_a_half = time.time() - 36 * 3600
                os.utime(dump, (day_and_a_half, day_and_a_half))
                self.assertEqual(checks.backups(ctx(production=True)).status, "warn")
                four_days = time.time() - 96 * 3600
                os.utime(dump, (four_days, four_days))
                self.assertEqual(checks.backups(ctx(production=True)).status, "fail")
                with open(dump, "wb"):
                    pass
                self.assertIn("empty", checks.backups(ctx(production=True)).summary)
                import gzip

                with open(dump, "wb") as handle:
                    handle.write(gzip.compress(b""))  # what `mysqldump ... | gzip` leaves when the dump fails
                self.assertEqual(checks.backups(ctx(production=True)).status, "fail")
                earlier = os.path.join(folder, "db-2.sql.gz")
                with open(earlier, "wb") as handle:
                    handle.write(b"x" * 10_000)
                with open(dump, "wb") as handle:
                    handle.write(b"x" * 3000)
                yesterday = time.time() - 24 * 3600
                os.utime(earlier, (yesterday, yesterday))
                result = checks.backups(ctx(production=True))
                self.assertEqual(result.status, "warn")
                self.assertIn("cut off", result.summary)
                days_ago = time.time() - 4 * 24 * 3600
                os.utime(dump, (days_ago, days_ago))
                os.utime(earlier, (days_ago - 3600, days_ago - 3600))
                self.assertEqual(checks.backups(ctx(production=True)).status, "fail")  # stopped: age comes first


class DeployAndStorageChecksTests(TestCase):
    def tearDown(self):
        deploy.BOOT.clear()

    def test_deploy_state(self):
        result = checks.deploy_state(ctx())
        self.assertEqual(result.status, "ok")  # the test database is fully migrated
        with mock.patch.object(deploy, "migration_state", return_value={"behind": ["panel.0099_x"], "ahead": []}):
            self.assertEqual(checks.deploy_state(ctx()).status, "fail")
        with mock.patch.object(deploy, "migration_state", return_value={"behind": [], "ahead": ["panel.0099_x"]}):
            self.assertEqual(checks.deploy_state(ctx()).status, "warn")
        with mock.patch.object(deploy, "changed_since_boot", return_value=(["panel/views.py"], [], [])):
            result = checks.deploy_state(ctx())
        self.assertEqual(result.status, "warn")
        self.assertIn("hasn't restarted", result.summary)

    def test_the_boot_fingerprint_sees_real_changes_only(self):
        with tempfile.TemporaryDirectory() as root:
            code = os.path.join(root, "app.py")
            with open(code, "w", encoding="utf-8") as handle:
                handle.write("A = 1\n")
            os.makedirs(os.path.join(root, ".venv"))
            with open(os.path.join(root, ".venv", "lib.py"), "w", encoding="utf-8") as handle:
                handle.write("ignored\n")
            deploy.record_boot(root)
            self.assertEqual(sorted(deploy.BOOT["files"]), ["app.py"])
            self.assertEqual(deploy.changed_since_boot(), ([], [], []))
            later = time.time() + 5
            os.utime(code, (later, later))  # touched, same content (a ZIP or a copy)
            self.assertEqual(deploy.changed_since_boot(), ([], [], []))
            with open(code, "w", encoding="utf-8") as handle:
                handle.write("A = 2\n")
            os.utime(code, (later + 5, later + 5))
            with open(os.path.join(root, "new.html"), "w", encoding="utf-8") as handle:
                handle.write("<p>")
            self.assertEqual(deploy.changed_since_boot(), (["app.py"], ["new.html"], []))
            os.remove(code)
            self.assertEqual(deploy.changed_since_boot()[2], ["app.py"])

    def test_line_endings_dont_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as unix, tempfile.TemporaryDirectory() as windows:
            with open(os.path.join(unix, "a.py"), "wb") as handle:
                handle.write(b"x = 1\ny = 2\n")
            with open(os.path.join(windows, "a.py"), "wb") as handle:
                handle.write(b"x = 1\r\ny = 2\r\n")
            self.assertEqual(deploy.digest(deploy.fingerprint(unix)), deploy.digest(deploy.fingerprint(windows)))

    def test_git_ref(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(deploy.git_ref(root), "")
            os.makedirs(os.path.join(root, ".git", "refs", "heads"))
            with open(os.path.join(root, ".git", "HEAD"), "w") as handle:
                handle.write("ref: refs/heads/main\n")
            with open(os.path.join(root, ".git", "packed-refs"), "w") as handle:
                handle.write("# pack-refs\n0123456789abcdef refs/heads/main\n")
            self.assertEqual(deploy.git_ref(root), "0123456789")
            with open(os.path.join(root, ".git", "refs", "heads", "main"), "w") as handle:
                handle.write("fedcba9876543210\n")
            self.assertEqual(deploy.git_ref(root), "fedcba9876")

    def test_database(self):
        result = checks.database(ctx())
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.metric.endswith(" ms"))
        past = timezone.now() - datetime.timedelta(days=1)
        Session.objects.bulk_create([Session(session_key=f"k{i:08d}", session_data="x", expire_date=past)
                                     for i in range(10_001)])
        self.assertIn("expired login sessions", checks.database(ctx()).summary)

    def test_storage(self):
        fine = {"media_bytes": 5_000_000, "media_files": 30, "static_bytes": 9_000_000, "disk_free": 50 * 2**30,
                "disk_total": 100 * 2**30, "writable": True}
        self.assertEqual(checks.storage(ctx({"storage": fine})).status, "ok")
        self.assertEqual(checks.storage(ctx({"storage": dict(fine, writable=False, write_error="EACCES")})).status, "fail")
        unjudged = checks.storage(ctx({"storage": dict(fine, writable=None)}))
        self.assertEqual(unjudged.status, "unknown")  # another account can't say whether uploads save
        self.assertIn("checked only by", str(unjudged.details))
        self.assertEqual(checks.storage(ctx({"storage": dict(fine, writable=None, disk_free=100 * 2**20)})).status,
                         "fail")  # but running out of room is the same from anywhere
        self.assertEqual(checks.storage(ctx({"storage": dict(fine, disk_free=100 * 2**20)})).status, "fail")
        self.assertEqual(checks.storage(ctx({"storage": dict(fine, disk_free=600 * 2**20)})).status, "warn")
        with mock.patch.dict(os.environ, {"MSS_QUOTA_MB": "16"}):
            self.assertEqual(checks.storage(ctx({"storage": fine})).status, "warn")  # 14 MB of 16
            self.assertEqual(checks.storage(ctx({"storage": dict(fine, static_bytes=11_000_000)})).status, "fail")


class SetupChecksTests(TestCase):
    def test_connected_services(self):
        from core.models import SiteConfig

        config = SiteConfig.get()
        config.email_api_key = config.geoapify_api_key = ""
        config.save()
        self.assertEqual(checks.connected_services(ctx()).status, "off")
        config.geoapify_api_key = "geo-key-1234"
        config.save()
        result = checks.connected_services(ctx())
        self.assertEqual(result.status, "ok")
        self.assertNotIn("geo-key-1234", str(result))  # keys never appear
        config.email_api_key = "sk-secret-5678"
        config.save()
        self.assertEqual(checks.connected_services(ctx()).summary, "“Draft with AI” and poster maps are both set up.")

    def test_admin_team(self):
        self.assertEqual(checks.admin_team(ctx()).status, "fail")
        first = make_user("teamone", is_portal_admin=True, is_super_admin=True)
        User.objects.filter(pk=first.pk).update(last_login=timezone.now())
        self.assertIn("only one admin", checks.admin_team(ctx()).summary)
        second = make_user("teamtwo", is_portal_admin=True)
        User.objects.filter(pk=second.pk).update(last_login=timezone.now())
        self.assertEqual(checks.admin_team(ctx()).status, "ok")
        User.objects.filter(pk=second.pk).update(last_login=timezone.now() - datetime.timedelta(days=400))
        result = checks.admin_team(ctx())
        self.assertIn("Teamtwo Tester hasn't logged in for a year", result.summary)
        self.assertEqual(result.who, "webmaster")  # only a super admin can take the rights away
        self.assertIn("ask one if that isn't you", result.fix)
        User.objects.filter(pk=second.pk).update(last_login=timezone.now())
        User.objects.filter(pk=first.pk).update(is_super_admin=False)
        self.assertIn("nobody is a super admin", checks.admin_team(ctx()).summary.lower())

    def test_terms(self):
        from core.models import TermsVersion

        self.assertEqual(checks.terms(ctx()).status, "warn")
        with mock.patch.object(TermsVersion, "current_number", return_value=3):
            make_user("termsy")
            result = checks.terms(ctx())
        self.assertEqual((result.status, result.metric), ("ok", "v3"))

    def test_society_setup(self):
        from core.models import SiteConfig

        config = SiteConfig.get()
        config.michaelmas_start = config.lent_start = config.easter_start = None
        config.save()
        self.assertIn("Term dates aren't set", checks.society_setup(ctx()).summary)
        config.michaelmas_start = timezone.localdate() - datetime.timedelta(days=400)
        config.save()
        self.assertIn("next term's dates aren't in yet", checks.society_setup(ctx()).summary)
        config.michaelmas_start = timezone.localdate() - datetime.timedelta(days=20)
        config.save()
        self.assertEqual(checks.society_setup(ctx()).status, "ok")


# --- the probes ----------------------------------------------------------------------------

class ProbeTests(TestCase):
    @override_settings(ALLOWED_HOSTS=["www.example.org", ".example.org", "localhost", "127.0.0.1", "*", "10.0.0.1",
                                      "mss.soc.srcf.net", "testserver", "[::1]"])
    def test_public_hosts_are_real_domains_only(self):
        context = Context(site_url="https://www.example.org")
        self.assertEqual(probes.public_hosts(context), ["www.example.org", "example.org", "mss.soc.srcf.net"])

    def test_a_certificate_that_cant_be_reached_is_an_answer_not_an_exception(self):
        result = probes.certificate("127.0.0.1", time.monotonic() + 3)
        self.assertEqual(result["host"], "127.0.0.1")
        self.assertTrue("error" in result or "invalid" in result)
        self.assertTrue(probes.certificate("127.0.0.1", time.monotonic() - 1)["timed_out"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="localhost", EMAIL_PORT=25)
    def test_the_mail_server_probe(self):
        class Server:
            def __init__(self, host, port, timeout):
                self.args = (host, port, timeout)

            def noop(self):
                return 250, b"OK"

            def quit(self):
                pass

        with mock.patch.object(probes.smtplib, "SMTP", Server):
            self.assertEqual(probes.mail_server(Context(), time.monotonic() + 5)["code"], 250)
        with mock.patch.object(probes.smtplib, "SMTP", side_effect=ConnectionRefusedError("refused")):
            self.assertIn("refused", probes.mail_server(Context(), time.monotonic() + 5)["error"])
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"):
            self.assertTrue(probes.mail_server(Context(), time.monotonic() + 5)["skipped"])

    def test_the_storage_probe(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with open(os.path.join(media, "photo.jpg"), "wb") as handle:
                handle.write(b"x" * 1000)
            data = probes.storage(Context(), time.monotonic() + 10)
            self.assertEqual((data["media_bytes"], data["media_files"], data["writable"]), (1000, 1, True))
            self.assertEqual(os.listdir(media), ["photo.jpg"])  # the test file is gone again

    def test_htaccess_rules_are_read_outside_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            rules = os.path.join(folder, ".htaccess")
            with open(rules, "w", encoding="utf-8") as handle:
                handle.write("# RequestHeader unset X-MSS-Remote-User\nRewriteEngine On\n")
            pattern = r"^RequestHeader\s+unset\s+X-MSS-Remote-User\b"
            self.assertFalse(probes._htaccess_has(rules, pattern))
            with open(rules, "a", encoding="utf-8") as handle:
                handle.write("  RequestHeader unset X-MSS-Remote-User\n")
            self.assertTrue(probes._htaccess_has(rules, pattern))
            self.assertIsNone(probes._htaccess_has(os.path.join(folder, "missing"), pattern))

    def test_files_are_sorted_into_secrets_personal_data_and_archives(self):
        self.assertEqual([probes._kind(name) for name in (".env.bak", "db.sqlite3", "mss.sql.gz", ".my.cnf",
                                                           "Members.XLSX", "list.csv", "site.zip", "notes.txt")],
                         ["secret", "secret", "secret", "secret", "personal", "personal", "archive", None])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="localhost", EMAIL_PORT=25)
    def test_the_mail_server_probe_knows_when_it_runs_away_from_the_web_server(self):
        with mock.patch.object(probes.smtplib, "SMTP", side_effect=ConnectionRefusedError("refused")):
            with mock.patch.dict(os.environ, {"MSS_WEB_PROCESS": ""}):
                self.assertTrue(probes.mail_server(Context(), time.monotonic() + 5)["elsewhere"])
            with mock.patch.dict(os.environ, {"MSS_WEB_PROCESS": "1"}):
                self.assertFalse(probes.mail_server(Context(), time.monotonic() + 5)["elsewhere"])

    def test_the_file_probe_is_skipped_on_windows(self):
        context = Context()
        context.posix = False
        self.assertEqual(probes.file_permissions(context, time.monotonic() + 5), {"skipped": True})

    def test_the_public_folder_is_found_from_the_static_root(self):
        with override_settings(STATIC_VERSION_FROM_ROOT=False):
            self.assertEqual(probes.public_html(), "")
        root = os.path.join(tempfile.gettempdir(), "public_html", "static")
        with override_settings(STATIC_VERSION_FROM_ROOT=True, STATIC_ROOT=root):
            self.assertEqual(probes.public_html(), os.path.dirname(root))
        with mock.patch.dict(os.environ, {"MSS_PUBLIC_HTML": "/srv/site"}):
            self.assertEqual(probes.public_html(), "/srv/site")

    def test_the_self_probe_is_skipped_without_an_address(self):
        context = Context(site_url=None)
        context.origin = ""
        self.assertEqual(probes.self_requests(context, time.monotonic() + 5), {"skipped": True})


@override_settings(RAVEN_MODE="header")
class SelfProbeRoundTripTests(LiveServerTestCase):
    """The self probe against a real server: the token, the answer and the
    verdict, end to end. No Apache stands in front of this server, so the
    forged header gets through, which is exactly what the check must see."""

    def test_the_self_probe_reaches_the_site_and_reads_its_answers(self):
        context = Context(full=True, site_url=self.live_server_url)
        with self.assertLogs("django.request", "ERROR"):  # /accounts/raven/ answers 500 with no Apache in front
            data = probes.self_requests(context, time.monotonic() + 20)
        self.assertEqual(data["healthz"]["status"], 200)
        self.assertEqual(data["healthz"]["body"].strip(), b"ok")
        self.assertEqual(data["home"]["status"], 200)
        dash = data["guard X-MSS-Remote-User"]["answer"]
        self.assertTrue(dash["remote_user_probe"])
        self.assertTrue(dash["xff_forged"])
        # Django's own server drops headers with underscores, as gunicorn 22+ does
        self.assertFalse(data["guard X_MSS_Remote_User"]["answer"]["remote_user_probe"])
        context.probes["self"] = data
        result = checks.login_guard(context)
        self.assertEqual(result.status, "fail")
        self.assertIn("Anyone could log in", result.summary)
        self.assertEqual(checks.site_reachable(context).status, "ok")


# --- the page ------------------------------------------------------------------------------

class HealthPageTests(TestCase):
    def setUp(self):
        self.super = make_user("hpsuper", is_portal_admin=True, is_super_admin=True)
        self.admin = make_user("hpadmin", is_portal_admin=True)
        self.member = make_user("hpmember")

    def test_health_is_its_own_tab_and_leads_with_what_to_sort_out(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("panel:health"))
        html = page.content.decode()
        tabs = html[html.index('class="panel-tabs"'):]
        self.assertLess(tabs.index(">Overview<"), tabs.index(">Health<"))
        self.assertLess(tabs.index(">Health<"), tabs.index(">People<"))
        self.assertIn('aria-current="page">Health', tabs)
        problems = page.context["problems"]
        self.assertTrue(problems)  # a fresh site has things to set up
        self.assertTrue(all(row["status"] in ("fail", "warn") for row in problems))
        self.assertContains(page, "To sort out")
        self.assertLess(html.index("To sort out"), html.index('<details class="card hs-fold">'))
        self.assertContains(page, "What to do:")
        dashboard = self.client.get(reverse("panel:home")).content.decode()
        overview = dashboard[dashboard.index('class="panel-tabs"'):]
        self.assertNotIn(">Site health<", overview[:overview.index("</nav>")])  # no longer under Overview

    def test_only_admins_see_the_page(self):
        url = reverse("panel:health")
        self.assertIn("/accounts/login/", self.client.get(url)["Location"])
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:health_run")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_opening_the_page_never_reaches_the_network(self):
        self.client.force_login(self.super)
        with mock.patch.object(core, "run_probes") as ran, \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetched")) as fetched:
            page = self.client.get(reverse("panel:health"))
        self.assertEqual(page.status_code, 200)
        ran.assert_not_called()
        fetched.assert_not_called()
        self.assertContains(page, "Runs with “Run all checks”")
        self.assertContains(page, "haven't run yet")
        self.assertEqual(len(page.context["sections"]), 8)

    def test_running_the_checks_keeps_the_result_and_shows_it(self):
        self.client.force_login(self.admin)
        quiet = {name: (lambda context, deadline: {"skipped": True}) for name in core.PROBES}
        with mock.patch.dict(core.PROBES, quiet):
            response = self.client.post(reverse("panel:health_run"))
            self.assertRedirects(response, reverse("panel:health"))
            snapshot = HealthSnapshot.objects.get()
            self.assertEqual((snapshot.trigger, snapshot.full, snapshot.run_by), ("button", True, self.admin))
            self.assertEqual(len(snapshot.results), len(core.REGISTRY))
            self.assertTrue(AuditLog.objects.filter(action="run_health_check", actor=self.admin).exists())
            page = self.client.get(reverse("panel:health"))
            self.assertContains(page, "Checked just now, with “Run all checks”")
            self.assertContains(page, "last ran just now")
            again = self.client.post(reverse("panel:health_run"), follow=True)
        self.assertContains(again, "less than a minute ago")
        self.assertEqual(HealthSnapshot.objects.count(), 1)

    def test_the_run_button_needs_a_post_with_its_csrf_token(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:health_run")).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(reverse("panel:health_run")).status_code, 403)
        self.assertFalse(HealthSnapshot.objects.exists())

    def test_technical_details_are_for_super_admins_only(self):
        ErrorEvent.objects.create(fingerprint="c" * 40, exc_type="KeyError", message="'venue'", view_name="events:detail_pk",
                                  route="events/<int:pk>/", frames="events/views.py:12 in detail")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("panel:health"))
        self.assertNotContains(page, "For the webmaster:")
        self.assertNotContains(page, "Mark fixed")
        self.assertContains(page, "The webmaster: Hpsuper Tester.")
        self.client.force_login(self.super)
        page = self.client.get(reverse("panel:health"))
        self.assertContains(page, "For the webmaster:")
        self.assertContains(page, "Mark fixed")
        self.assertContains(page, "events/views.py:12 in detail")

    def test_the_overview_tab_shows_breakages_to_all_admins_and_warnings_to_super_admins(self):
        snapshot = HealthSnapshot.objects.create(trigger="cron", full=True, overall="fail",
                                                 statuses={"login_guard": "fail"}, results=[])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:audit")), "nav-health")
        snapshot.overall, snapshot.statuses = "warn", {"login_guard": "warn"}
        snapshot.save()
        self.assertNotContains(self.client.get(reverse("panel:audit")), "nav-health")
        self.client.force_login(self.super)
        self.assertContains(self.client.get(reverse("panel:audit")), 'aria-label="Site health: Needs attention"')
        HealthSnapshot.objects.update(created_at=timezone.now() - datetime.timedelta(days=3), overall="fail",
                                      statuses={"login_guard": "fail"})
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:audit")), "nav-health")  # as the page still shows it
        self.assertContains(self.client.get(reverse("panel:home")), "so it's time to run them again")

    def test_the_dashboard_says_how_the_site_is(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:home")), "not checked yet")
        HealthSnapshot.objects.create(trigger="cron", full=True, overall="ok", statuses={"database": "ok"}, results=[])
        page = self.client.get(reverse("panel:home"))
        self.assertContains(page, "<strong>Site health:</strong> working", html=False)
        self.assertContains(page, "all checks last ran just now")
        self.assertContains(page, reverse("panel:health"))

    def test_a_restart_cannot_hide_what_the_last_full_run_found(self):
        from panel.healthchecks import latest_summary

        now = timezone.now()
        HealthSnapshot.objects.create(created_at=now - datetime.timedelta(hours=3), trigger="cron", full=True,
                                      overall="fail", statuses={"login_guard": "fail", "database": "ok"}, results=[])
        HealthSnapshot.objects.create(created_at=now, trigger="deploy", full=False, overall="ok",
                                      statuses={"database": "ok"}, results=[])
        summary = latest_summary()
        self.assertEqual(summary["status"], "fail")
        self.assertTrue(summary["full"])
        self.assertEqual(summary["ago"], "3\xa0hours ago")  # when everything was last checked
        HealthSnapshot.objects.create(created_at=now + datetime.timedelta(seconds=1), trigger="deploy", full=False,
                                      overall="fail", statuses={"database": "fail"}, results=[])
        self.assertEqual(latest_summary()["status"], "fail")
        HealthSnapshot.objects.filter(full=True).update(statuses={"login_guard": "ok", "database": "fail"})
        HealthSnapshot.objects.filter(full=False).update(statuses={"database": "ok"})
        self.assertEqual(latest_summary()["status"], "ok")  # the newer quick run's database result wins

    def test_a_run_that_could_not_check_never_replaces_an_answer(self):
        from panel.healthchecks import latest_summary

        now = timezone.now()
        HealthSnapshot.objects.create(created_at=now - datetime.timedelta(hours=2), trigger="cron", full=True,
                                      overall="fail", statuses={"socket_private": "fail", "database": "ok"}, results=[])
        HealthSnapshot.objects.create(created_at=now, trigger="deploy", full=False, overall="ok",
                                      statuses={"socket_private": "unknown", "database": "ok"}, results=[])
        self.assertEqual(latest_summary()["status"], "fail")  # a restart's "checked once running" hides nothing

    def test_the_cron_on_another_machine_does_not_clear_the_web_servers_answer(self):
        from panel.healthchecks import latest_summary

        now = timezone.now()

        def row(key, status, when):
            return {"key": key, "status": status, "summary": f"{key} {status}", "checked_at": when.isoformat(),
                    "details": [], "metric": "", "fix": "", "who": "", "webmaster": "", "action": {}, "vital": ""}

        button = now - datetime.timedelta(hours=5)
        HealthSnapshot.objects.create(created_at=button, trigger="button", full=True, overall="fail",
                                      statuses={"email_server": "fail", "storage": "ok"},
                                      results=[row("email_server", "fail", button), row("storage", "ok", button)])
        HealthSnapshot.objects.create(created_at=now, trigger="cron", full=True, overall="ok",
                                      statuses={"email_server": "unknown", "storage": "unknown"},
                                      results=[row("email_server", "unknown", now), row("storage", "unknown", now)])
        self.assertEqual(latest_summary()["status"], "fail")
        self.client.force_login(self.super)
        rows = {row["key"]: row for _, _, _, section in self.client.get(reverse("panel:health")).context["sections"]
                for row in section}
        self.assertEqual(rows["email_server"]["status"], "fail")
        self.assertEqual(rows["email_server"]["checked_ago"], "5\xa0hours ago")
        HealthSnapshot.objects.filter(trigger="button").update(created_at=now - datetime.timedelta(days=3))
        rows = {row["key"]: row for _, _, _, section in self.client.get(reverse("panel:health")).context["sections"]
                for row in section}
        self.assertEqual(rows["email_server"]["status"], "unknown")  # too old to stand in for today's run

    def test_a_server_error_failure_fades_in_the_summary_as_it_does_on_the_page(self):
        from panel.healthchecks import latest_summary

        now = timezone.now()
        HealthSnapshot.objects.create(created_at=now - datetime.timedelta(hours=2), trigger="deploy", full=True,
                                      overall="fail", statuses={"server_errors": "fail", "database": "ok"}, results=[])
        self.assertEqual(latest_summary()["status"], "warn")
        HealthSnapshot.objects.update(created_at=now - datetime.timedelta(minutes=20))
        self.assertEqual(latest_summary()["status"], "fail")

    def test_dealing_with_an_error_refreshes_the_summary(self):
        error = ErrorEvent.objects.create(fingerprint="g" * 40, exc_type="KeyError", view_name="core:home")
        self.client.force_login(self.super)
        self.client.post(reverse("panel:health_error_resolve", args=[error.pk]))
        snapshot = HealthSnapshot.objects.get()
        self.assertFalse(snapshot.full)
        self.assertEqual(snapshot.statuses["server_errors"], "ok")

    def test_icons_beside_words_are_hidden_from_screen_readers_and_unrun_checks_say_so(self):
        self.client.force_login(self.super)
        page = self.client.get(reverse("panel:health")).content.decode()
        import re

        self.assertNotIn('aria-label="Visitors', page)  # the section's name never leaks into its icons
        self.assertIsNone(re.search(r'<svg class="hs-icon[^>]*role="img"', page))  # icons beside words stay silent
        self.assertGreater(page.count('<svg class="hs-icon'), 30)
        self.assertIn("Not checked yet</span>", page)
        self.assertIn("Not run yet", page)

    def test_the_super_admin_tab_links_to_the_page_and_checks_nothing_itself(self):
        self.client.force_login(self.super)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetched")):
            page = self.client.get(reverse("panel:superadmin"))
        self.assertContains(page, reverse("panel:health"))
        self.assertNotIn("static_health", page.context)


class VisualsTests(TestCase):
    def test_the_history_strip_groups_runs_by_local_day_and_keeps_the_worst(self):
        london = timezone.get_default_timezone()
        late = datetime.datetime(2026, 7, 10, 23, 30, tzinfo=london)   # 22:30 UTC
        early = datetime.datetime(2026, 7, 11, 0, 30, tzinfo=london)   # 23:30 UTC, the 10th in UTC
        for when, overall in ((late, "ok"), (early, "fail"), (early + datetime.timedelta(hours=2), "ok")):
            HealthSnapshot.objects.create(created_at=when, trigger="cron", full=True, overall=overall,
                                          statuses={}, results=[])
        cells = visuals.history(now=datetime.datetime(2026, 7, 11, 12, 0, tzinfo=london))
        self.assertEqual(len(cells), 30)
        # grouped by UTC day, the 00:30 failure would land on the 10th
        self.assertEqual([(cell["day"].day, cell["status"]) for cell in cells[-3:]], [(9, "none"), (10, "ok"), (11, "fail")])

    def test_the_sparkline_marks_hours_with_errors(self):
        self.assertIsNone(visuals.sparkline())
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)
        RequestStats.objects.create(hour=hour, requests=10)
        RequestStats.objects.create(hour=hour - datetime.timedelta(hours=3), requests=30, s5xx=2)
        spark = visuals.sparkline()
        self.assertEqual((spark["total"], spark["peak"], len(spark["errors"])), (40, 30, 1))
        self.assertTrue(spark["line"].startswith("M0"))

    def test_the_verdict(self):
        rows = [{"status": "ok"}, {"status": "warn"}, {"status": "fail"}, {"status": "na"}]
        self.assertEqual(visuals.verdict(rows)["title"], "1 thing to fix now, and 1 to sort out soon")
        self.assertEqual(visuals.verdict(rows[:2])["title"], "Working, with 1 thing to sort out")
        self.assertEqual(visuals.verdict(rows[:1])["title"], "Everything is working")
        self.assertEqual(visuals.verdict([{"status": "na"}])["status"], "na")


# --- the command ---------------------------------------------------------------------------

class HealthCommandTests(TestCase):
    def call(self, *args):
        out = io.StringIO()
        try:
            call_command("health_check", *args, stdout=out)
            code = 0
        except SystemExit as exit_:
            code = exit_.code
        return code, out.getvalue()

    def test_exit_codes_and_quiet_output(self):
        with only_checks("ok", "na", "off"):
            self.assertEqual(self.call("--quick", "--quiet"), (0, ""))
        with only_checks("ok", "warn"):
            code, out = self.call("--quick", "--quiet")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] Fake check 1: It is warn.", out)
        self.assertIn("What to do: Do the thing.", out)
        self.assertNotIn("Fake check 0", out)
        with only_checks("warn", "fail"):
            self.assertEqual(self.call("--quick")[0], 2)
        self.assertEqual(HealthSnapshot.objects.count(), 3)
        self.assertEqual(HealthSnapshot.objects.first().trigger, "command")

    def test_json_and_no_save(self):
        with only_checks("ok"):
            code, out = self.call("--quick", "--json", "--no-save")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["overall"], "ok")
        self.assertEqual(data["results"][0]["summary"], "It is ok.")
        self.assertFalse(HealthSnapshot.objects.exists())

    def test_a_scheduled_run_leaves_a_heartbeat(self):
        with only_checks("ok"):
            self.call("--quick", "--trigger", "cron")
        beat = JobHeartbeat.objects.get(name="health_check")
        self.assertTrue(beat.last_ok)
        self.assertEqual(beat.detail, "ok")
        self.assertEqual(HealthSnapshot.objects.get().trigger, "cron")

    def test_pruning_keeps_recent_runs_the_worst_of_each_older_day_and_the_newest_full_run(self):
        now = timezone.now()
        noon = timezone.localtime(now).replace(hour=12, minute=0, second=0, microsecond=0)

        def snap(when, overall, full=True):
            return HealthSnapshot.objects.create(created_at=when, trigger="cron", full=full, overall=overall,
                                                 statuses={}, results=[])

        recent = [snap(now - datetime.timedelta(days=2), "ok", full=False),
                  snap(now - datetime.timedelta(days=2, hours=1), "ok", full=False)]
        day = noon - datetime.timedelta(days=20)
        worst = snap(day, "fail")  # also the newest full run
        snap(day + datetime.timedelta(minutes=30), "ok", full=False)
        snap(day + datetime.timedelta(minutes=45), "warn", full=False)
        ancient = snap(now - datetime.timedelta(days=200), "ok")
        resolved_long_ago = ErrorEvent.objects.create(fingerprint="d" * 40, resolved_at=now - datetime.timedelta(days=100))
        resolved_lately = ErrorEvent.objects.create(fingerprint="e" * 40, resolved_at=now - datetime.timedelta(days=10))
        SlowRequest.objects.create(at=now - datetime.timedelta(days=40), duration_ms=6000)
        SlowRequest.objects.create(at=now - datetime.timedelta(days=4), duration_ms=6000)
        RequestStats.objects.create(hour=(now - datetime.timedelta(days=200)).replace(minute=0, second=0, microsecond=0))
        Session.objects.create(session_key="old" + "x" * 29, session_data="x", expire_date=now - datetime.timedelta(days=1))
        with only_checks("ok"):
            code, out = self.call("--quick", "--prune", "--no-save")
        self.assertEqual(code, 0)
        kept = set(HealthSnapshot.objects.values_list("pk", flat=True))
        self.assertEqual(kept, {recent[0].pk, recent[1].pk, worst.pk})
        self.assertNotIn(ancient.pk, kept)
        self.assertEqual(set(ErrorEvent.objects.values_list("pk", flat=True)), {resolved_lately.pk})
        self.assertNotEqual(resolved_long_ago.pk, resolved_lately.pk)
        self.assertEqual(SlowRequest.objects.count(), 1)
        self.assertFalse(RequestStats.objects.exists())
        self.assertFalse(Session.objects.exists())
        self.assertIn("Tidied:", out)

    def test_pruning_never_drops_the_newest_full_run(self):
        now = timezone.now()
        full = HealthSnapshot.objects.create(created_at=now - datetime.timedelta(days=30), trigger="button", full=True,
                                             overall="ok", statuses={}, results=[])
        HealthSnapshot.objects.create(created_at=now - datetime.timedelta(days=30, minutes=-30), trigger="deploy",
                                      full=False, overall="warn", statuses={}, results=[])
        with only_checks("ok"):
            self.call("--quick", "--prune", "--no-save")
        self.assertTrue(HealthSnapshot.objects.filter(pk=full.pk).exists())
