"""The site health checks, in the order the Site health page shows them.

Each check returns a Result: a status, a sentence for the committee, an
optional next step and who takes it, and details for the webmaster. Quick
checks read only the database, the settings and a few files; slow ones
(``slow=True``) read what their probe found (panel/healthchecks/probes.py).
"""

import datetime
import functools
import os
import stat
from email.utils import parseaddr
from urllib.parse import urlsplit

from django.conf import settings
from django.core.signals import setting_changed
from django.db import connection
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.timesince import timesince

from . import deploy
from .core import Result, check

UPGRADING = "UPGRADING.md"
# Pages whose errors matter most, with the names the committee knows them by.
CRITICAL_VIEWS = {
    "core:home": "The home page",
    "events:calendar": "The event calendar",
    "accounts:raven": "Logging in with Raven",
    "accounts:login": "The login page",
    "dashboard:home": "The members' home page",
    "panel:mailer": "The Mailer tab",
}
PAGE_KINDS = {  # the rest, by the part of the site they're in
    "events": "an event page", "panel": "an admin page", "accounts": "an account page",
    "posters": "a poster page", "polls": "a poll page", "surveys": "a survey page", "inbox": "a messages page",
    "core": "a site page", "dashboard": "the members' area", "guide": "the Guide", "faq": "a help page",
    "supper": "the Supper Club page", "testimonials": "the testimonials page",
    "notifications": "the notifications page", "members": "the members' directory",
}
STRICT_DOMAINS = {  # mail claiming to come from these, sent by the SRCF, is likely to be junked
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "live.co.uk",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "cam.ac.uk", "cantab.net", "cambridgesu.co.uk",
}
KNOWN_DEPLOY_WARNINGS = {
    "security.W004",  # HSTS: off until the custom domain's HTTPS is settled (settings.py)
    "security.W005",
    "security.W008",  # Apache, not Django, moves visitors to https://
    "security.W021",
}


def ago(when, now):
    if when is None:
        return "never"
    if now - when < datetime.timedelta(minutes=1):
        return "just now"
    return f"{timesince(when, now).split(',')[0]} ago"


def size(count):
    count = float(count or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f}\u00a0{unit}" if unit == "bytes" else f"{count:.1f}\u00a0{unit}"
        count /= 1024
    return f"{count:.1f}\u00a0TB"


def sentence(text):
    """Upper-case the first letter only (str.capitalize lowers the rest)."""
    return text[:1].upper() + text[1:]


def plural(count, word, many=None):
    return f"{count} {word if count == 1 else (many or word + 's')}"


def _timed_out(data):
    return Result("unknown", "The check ran out of time, so this says nothing either way. Try again in a minute.",
                  details=[("Detail", data.get("error", "timed out"))])


# --- Visitors & pages ------------------------------------------------------------------------

@check("site_reachable", "The website answers", "visitors", slow=True)
def site_reachable(ctx):
    data = ctx.probe("self")
    if data.get("skipped"):
        return Result("na", "There's no public address to check from here.", who="webmaster",
                      webmaster="Set DJANGO_SITE_URL in .env, or press “Run all checks” on the live site.")
    if data.get("timed_out") or data.get("error"):
        return _timed_out(data)
    healthz, home = data.get("healthz", {}), data.get("home", {})
    details = [("Address", data.get("origin", "")), ("/healthz", healthz.get("status") or healthz.get("error", "")),
               ("Home page", f"{home.get('status') or home.get('error', '')}, {home.get('ms', 0)} ms")]
    if healthz.get("error"):
        return Result(
            "unknown", "The server couldn't reach its own website, so this says nothing either way. "
                       "If the site opens in a browser, all is well.",
            who="webmaster", details=details,
            webmaster="A request from the server to its own public address failed. That usually means "
                      "outgoing requests are blocked, or the name resolves differently inside the SRCF.")
    body = (healthz.get("body") or b"").strip()
    if healthz.get("status") == 503 and body == b"unavailable":  # the portal's own answer, not Apache's
        return Result("fail", "The website is up but can't reach its database, so most pages show an error.",
                      fix="Tell the webmaster now.", who="webmaster", details=details,
                      webmaster="/healthz answered 503: SELECT 1 failed. Check the MySQL service and DB_* in .env.")
    if healthz.get("status") != 200 or body != b"ok":
        return Result("fail", f"The website isn't answering properly (it said {healthz.get('status')}).",
                      fix="Tell the webmaster now.", who="webmaster", details=details,
                      webmaster="/healthz should answer 200 “ok”. A 502 or 503 from Apache means gunicorn "
                                "isn't running or Apache can't reach its socket (UPGRADING.md, “Service Unavailable”).")
    status, ms = home.get("status"), home.get("ms", 0)
    if home.get("error"):
        return Result("warn", "The website answers, but the home page didn't load in time.",
                      fix="Open the home page yourself; if it's slow, tell the webmaster.", who="webmaster",
                      details=details)
    if status and status >= 500:
        return Result("fail", f"The home page shows an error page (HTTP {status}).", fix="Tell the webmaster now.",
                      who="webmaster", details=details, webmaster="See “Pages aren't crashing” for the error.")
    seconds = f"{ms / 1000:.1f}\u00a0s"
    if ms > 3000:
        return Result("warn", f"The home page took {seconds} to load. Visitors notice anything over two seconds.",
                      metric=seconds, who="webmaster", details=details,
                      webmaster="See “Pages load quickly” for the slowest pages.")
    return Result("ok", f"The website answers, and the home page loaded in {seconds}.", metric=seconds,
                  details=details)


@check("static_copy", "This version's stylesheets are in place", "visitors")
def static_copy(ctx):
    from panel import health

    configured = getattr(settings, "STATIC_VERSION_FROM_ROOT", False)
    if not ctx.production and not configured:
        return Result("na", "Not applicable here: the development server serves these files straight from the code.")
    copy = health.collected_copy()
    details = [("Folder", copy["root"]), ("Files the site ships", copy["total"]), ("Missing", copy["missing"]),
               ("Out of date", copy["differ"])]
    metric = f"{copy['total'] - copy['missing'] - copy['differ']}/{copy['total']} files"
    if ctx.production and not configured:
        return Result(
            "warn", "A restart can't refresh the stylesheets visitors get: the portal doesn't know which "
                    "folder Apache serves them from.",
            metric=metric, fix="Tell the webmaster.", who="webmaster", details=details,
            webmaster=f"Set DJANGO_STATIC_ROOT in .env to the folder Apache serves for /static/ ({UPGRADING}, "
                      "one-time step 2), then restart.")
    if copy["current"]:
        return Result("ok", f"All {copy['total']} files this version ships are in place for Apache to serve.",
                      metric=metric, details=details)
    return Result(
        "warn", f"In the folder Apache serves, {plural(copy['missing'], 'file')} missing and "
                f"{copy['differ']} out of date, so the next visitor may get old styles.",
        metric=metric, fix="Ask the webmaster to restart the portal: it copies them each time it starts.",
        who="webmaster", details=details,
        webmaster=f"Restart ({UPGRADING} step 3). If that doesn't help, run `manage.py sync_static` and read what "
                  "it says; most often the folder's permissions (srcf-soc-permfix mss, as the folder's owner).")


@check("static_served", "Visitors get this version's stylesheets", "visitors", slow=True)
def static_served(ctx):
    data = ctx.probe("static")
    if data.get("skipped"):
        return Result("na", "There's no public address to check from here.", who="webmaster",
                      webmaster="Set DJANGO_SITE_URL in .env, or press “Run all checks” on the live site.")
    if data.get("timed_out") or data.get("error"):
        return _timed_out(data)
    rows, problems = data.get("served", []), data.get("problems", [])
    total, good = len(rows), data.get("ok", 0)
    metric = f"{good}/{total} files"
    details = [(row["name"], row["status"]) for row in problems[:10]]
    if len(problems) > 10:
        details.append(("…", f"and {len(problems) - 10} more"))
    if not problems:
        return Result("ok", f"Visitors get all {total} checked stylesheets, scripts and fonts exactly as "
                            "this version ships them.", metric=metric)
    cause, copy = data.get("cause"), data.get("copy") or {}
    if cause == "fetch":
        return Result("unknown", "The server couldn't fetch its own site, so this says nothing about the files. "
                                 "Open the site in a browser: if it looks right, all is well.",
                      metric=metric, details=details, who="webmaster")
    if cause == "folder" and copy.get("current"):
        return Result(
            "fail", f"Visitors get old or missing stylesheets ({len(problems)} of {total} files), so pages look "
                    "unstyled or broken.", metric=metric, fix="Tell the webmaster.", who="webmaster",
            details=details,
            webmaster=f"The copy in {copy.get('root')} is right, so Apache serves /static/ from another folder: "
                      f"set DJANGO_STATIC_ROOT in .env to that folder ({UPGRADING}, one-time step 2) and restart.")
    if cause == "folder":
        return Result(
            "fail", f"Visitors get old or missing stylesheets ({len(problems)} of {total} files), so pages look "
                    "unstyled or broken.", metric=metric,
            fix="Ask the webmaster to restart the portal (it copies them as it starts), then run the checks again.",
            who="webmaster", details=details,
            webmaster="The copy isn't current either: see “This version's stylesheets are in place”.")
    if cause == "permissions":
        return Result(
            "fail", "Apache isn't allowed to read some of the site's files, so visitors get errors instead of styles.",
            metric=metric, fix="Tell the webmaster.", who="webmaster", details=details,
            webmaster="HTTP 403: run `srcf-soc-permfix mss` (or `chmod -R a+rX` on the folder) as the folder's owner.")
    return Result("fail", f"{plural(len(problems), 'file')} came back with errors.", metric=metric,
                  fix="Tell the webmaster.", who="webmaster", details=details)


@check("server_errors", "Pages aren't crashing", "visitors")
def server_errors(ctx):
    from panel.models import ErrorEvent, RequestStats

    week = ctx.now - datetime.timedelta(days=7)
    day = ctx.now - datetime.timedelta(days=1)
    recent = ctx.now - datetime.timedelta(hours=1)
    open_errors = list(ErrorEvent.objects.filter(
        kind=ErrorEvent.Kind.EXCEPTION, resolved_at__isnull=True, last_seen__gte=week)[:50])
    hour = ctx.now.replace(minute=0, second=0, microsecond=0)
    this_hour = RequestStats.objects.filter(hour=hour).values_list("s5xx", flat=True).first() or 0
    critical = [error for error in open_errors if error.view_name in CRITICAL_VIEWS and error.last_seen >= day]
    metric = f"{len(open_errors)} open"
    details = [(error.view_name or error.route or "?",
                f"{error.exc_type or 'error'} ×{error.count}, last {ago(error.last_seen, ctx.now)}"
                + (" (came back)" if error.reopened else "")) for error in open_errors[:8]]
    tell = {"who": "webmaster", "details": details, "metric": metric,
            "webmaster": "The table of errors below has each one's place in the code. Fix each one, then press "
                         "“Mark fixed”: one that comes back reopens as “came back”."}
    if this_hour >= 10:
        return Result("fail", f"Visitors are getting error pages now ({this_hour} this hour).",
                      fix="It isn't anything you did. Tell the webmaster now.", **tell)
    if critical:
        error = critical[0]
        page = CRITICAL_VIEWS[error.view_name]
        times = "" if error.count == 1 else f" ({error.count} times)"
        if error.last_seen >= recent:
            return Result("fail", f"{page} crashed for someone {ago(error.last_seen, ctx.now)}{times}.",
                          fix="It isn't anything you did. Tell the webmaster now.", **tell)
        return Result("warn", f"{page} crashed for someone {ago(error.last_seen, ctx.now)}{times}, and may still "
                              "be broken.", fix="It isn't anything you did. Tell the webmaster.", **tell)
    if open_errors:
        latest = open_errors[0]
        return Result(
            "warn", f"{plural(len(open_errors), 'kind')} of error in the last 7 days; the latest "
                    f"{ago(latest.last_seen, ctx.now)} on {page_name(latest.view_name)}.",
            fix="It isn't anything you did. Tell the webmaster.", **tell)
    return Result("ok", "No page has crashed in the last 7 days.", metric="0 open")


def page_name(view_name):
    """A page's name in plain words, for the committee (the code's own name
    for it, like ``events:detail_pk``, stays in the webmaster's details)."""
    if view_name in CRITICAL_VIEWS:
        return CRITICAL_VIEWS[view_name][:1].lower() + CRITICAL_VIEWS[view_name][1:]
    return PAGE_KINDS.get((view_name or "").split(":")[0], "a page")


def worker_timeouts(since):
    from panel.middleware import read_worker_timeouts

    return read_worker_timeouts(os.path.join(str(settings.BASE_DIR), "state", "worker-timeouts.log"), since)


@check("response_times", "Pages load quickly", "visitors")
def response_times(ctx):
    from panel.models import RequestStats, SlowRequest

    day = ctx.now - datetime.timedelta(days=1)
    fields = ("requests", "s4xx", "s5xx", "under_half", "under_1", "under_2", "under_5", "under_10",
              "under_20", "over_20")
    sums = RequestStats.objects.filter(hour__gte=day).aggregate(**{name: Sum(name) for name in fields})
    sums = {name: sums.get(name) or 0 for name in fields}
    timeouts = worker_timeouts(ctx.now - datetime.timedelta(days=7))
    today = [entry for entry in timeouts if entry[0] >= day]
    requests = sums["requests"]
    slow_rows = list(SlowRequest.objects.filter(at__gte=ctx.now - datetime.timedelta(days=7))
                     .order_by("-duration_ms")[:5])
    details = [("Requests (24 h)", requests), ("Not found / refused (24 h)", sums["s4xx"]),
               ("Server errors (24 h)", sums["s5xx"]),
               ("Worker timeouts (7 days)", len(timeouts))]
    details += [(f"Slow: {row.view_name or row.route or '?'}",
                 f"{row.duration_ms / 1000:.1f} s, {ago(row.at, ctx.now)}") for row in slow_rows]
    if not requests and not timeouts:
        return Result("unknown", "No visits recorded in the last day yet.", details=details)
    fast = sums["under_half"] + sums["under_1"]
    slow = sums["under_5"] + sums["under_10"] + sums["under_20"] + sums["over_20"]
    fast_share = round(fast * 100 / requests) if requests else 0
    metric = f"{fast_share}% under 1\u00a0s" if requests else ""
    vital = f"{fast_share}%" if requests else ""
    if len(today) >= 3:
        return Result(
            "fail", f"Pages took so long that the server gave up on them {len(today)} times today; those visitors "
                    "got an error page.", metric=metric, vital=vital, fix="Tell the webmaster.", who="webmaster",
            details=details, webmaster="gunicorn killed workers at its 30-second timeout "
                                       "(state/worker-timeouts.log lists when, and the part of the site).")
    if timeouts:
        return Result("warn", f"The server gave up on a slow page {plural(len(timeouts), 'time')} this week.",
                      metric=metric, vital=vital, fix="Tell the webmaster.", who="webmaster", details=details,
                      webmaster="See state/worker-timeouts.log; the slowest recent pages are listed here.")
    if requests >= 50 and slow * 100 / requests > 5:
        return Result("warn", f"{round(slow * 100 / requests)}% of pages took more than 2 seconds today.",
                      metric=metric, vital=vital, who="webmaster", details=details)
    if sums["over_20"]:
        return Result("warn", f"{plural(sums['over_20'], 'page')} took over 20 seconds today, close to the "
                              "30-second limit.", metric=metric, vital=vital, who="webmaster", details=details)
    return Result("ok", f"{fast_share}% of pages loaded in under a second in the last day "
                        f"({plural(requests, 'request')}).", metric=metric, vital=vital, details=details)


@check("uptime_monitor", "Someone outside watches the site", "visitors")
def uptime_monitor(ctx):
    from panel.models import RequestStats

    if not ctx.production:
        return Result("na", "Not applicable here: only the live site needs an outside monitor.")
    hits = RequestStats.objects.filter(hour__gte=ctx.now - datetime.timedelta(days=1)).aggregate(
        total=Sum("monitor_hits"))["total"] or 0
    if hits:
        return Result("ok", f"An outside monitor checked the site {plural(hits, 'time')} in the last day, so an "
                            "outage gets noticed.", metric=f"{hits} today")
    address = (ctx.origin or "https://<site>") + "/healthz"
    return Result(
        "off", "Nothing outside checks the site, so if it goes down nobody is told until a member notices.",
        fix="Ask the webmaster to set up a free uptime monitor.", who="webmaster",
        webmaster=f"Create a free keyword monitor (for example at uptimerobot.com) for {address}, expecting the "
                  "word “ok”, alerting the webmaster and the committee address. It appears here within the hour.")


# --- Logins & security -----------------------------------------------------------------------

@check("login_guard", "Nobody can fake a Raven login", "security", slow=True)
def login_guard(ctx):
    if getattr(settings, "RAVEN_MODE", "dev") != "header":
        return Result("na", "Not applicable here: Raven logins don't come through Apache on this copy.")
    data = ctx.probe("self")
    if data.get("skipped"):
        return Result("unknown", "There's no public address to check from here.", who="webmaster",
                      webmaster="Set DJANGO_SITE_URL in .env, or press “Run all checks” on the live site.")
    if data.get("timed_out") or data.get("error"):
        return _timed_out(data)
    fails, warns, unknowns, details = [], [], [], []
    for spelling in ("X-MSS-Remote-User", "X_MSS_Remote_User"):
        entry = data.get(f"guard {spelling}") or {}
        answer = entry.get("answer")
        if answer is None:
            why = entry.get("error") or f"HTTP {entry.get('status')}"
            details.append((f"Forged {spelling}", f"no answer ({why})"))
            unknowns.append(spelling)
            continue
        details.append((f"Forged {spelling}", "reached Django" if answer.get("remote_user_probe") else "removed"))
        if answer.get("remote_user_probe"):
            fails.append(spelling)
        if answer.get("proto") == "http" and ctx.origin.startswith("https://"):
            warns.append("proto")
        if answer.get("xff_forged"):
            warns.append("xff")
    raven = data.get("raven") or {}
    location = (raven.get("headers") or {}).get("location", "")
    own = urlsplit(ctx.origin).hostname
    ours = {host.lstrip(".").lower() for host in settings.ALLOWED_HOSTS} | {own}
    target = (urlsplit(location).hostname or own) if location else None
    status = raven.get("status")
    details.append(("/accounts/raven/", f"{status or raven.get('error', '')} → {location or '-'}"))
    if status == 200:
        fails.append("raven")  # answered without sending anyone to log in
    elif status in (301, 302, 303, 307, 308):
        if target == own:
            warns.append("raven-own")
        elif target in ours:
            unknowns.append("raven")  # sent on to the site's other address; not followed
    elif raven.get("error") or status is None:
        unknowns.append("raven")
    else:
        warns.append("raven-broken")
    if "X-MSS-Remote-User" in fails:
        return Result(
            "fail", "Anyone could log in as any member, admins included: the server passes on a forged Raven "
                    "login header.", fix="Tell the webmaster now.", who="webmaster", details=details,
            webmaster="The root public_html/.htaccess must say `RequestHeader unset X-MSS-Remote-User` "
                      "(DEPLOYMENT_SRCF.md §6). Until it does, set RAVEN_MODE to something other than header.")
    if "X_MSS_Remote_User" in fails:
        return Result(
            "fail", "Anyone could log in as any member, admins included: the web server turns a look-alike "
                    "header into the Raven login header.", fix="Tell the webmaster now.", who="webmaster",
            details=details,
            webmaster="gunicorn older than 22 reads X_MSS_Remote_User as X-MSS-Remote-User, and Apache only "
                      "removes the second. Run `.venv/bin/pip install -r requirements-srcf.txt` (gunicorn 23; "
                      "gunicorn.conf.py sets header_map = \"drop\") and restart.")
    if "raven" in fails:
        return Result(
            "fail", "The Raven login page answers without asking for a University login.", fix="Tell the webmaster now.",
            who="webmaster", details=details,
            webmaster="public_html/accounts/raven/.htaccess must start `AuthType Ucam-WebAuth` / "
                      "`Require valid-user` (DEPLOYMENT_SRCF.md §7).")
    untested = [spelling for spelling in unknowns if spelling != "raven"]
    if unknowns and not warns:
        return Result("unknown", "Some of the login checks got no answer (the other worker may have been busy). "
                                 "Try again in a minute.", details=details, who="webmaster")
    if warns:
        notes = []
        if "proto" in warns:
            notes.append("a forged X-Forwarded-Proto reaches Django")
        if "xff" in warns:
            notes.append("a forged X-Forwarded-For is taken as the visitor's address, so per-address limits "
                         "can be dodged")
        if "raven-own" in warns:
            notes.append("/accounts/raven/ redirects within the site instead of to the University login")
        if "raven-broken" in warns:
            notes.append(f"/accounts/raven/ answers {status}, so Raven logins may not work")
        # Only say forged logins are refused when both forgeries were tried and removed.
        lead = ("The forged-login test got no answer this time (try again in a minute); also, "
                if untested else "Forged logins are refused, but ")
        return Result("warn", lead + "; ".join(notes) + ".", who="webmaster", details=details,
                      webmaster="The root .htaccess should set X-Forwarded-Proto itself (DEPLOYMENT_SRCF.md §6); "
                                "mod_proxy adds the real address to X-Forwarded-For unless ProxyAddHeaders is off.")
    return Result("ok", "Forged login headers are removed before they reach the site, and the Raven page sends "
                        "people to the University login.", details=details)


@check("socket_private", "Only Apache can reach the portal", "security")
def socket_private(ctx):
    if getattr(settings, "RAVEN_MODE", "dev") != "header":
        return Result("na", "Not applicable here: Raven logins don't come through Apache on this copy.")
    if not ctx.posix:
        return Result("na", "Not applicable here (Windows has no socket).")
    if ctx.trigger == "deploy":
        return Result("unknown", "Checked once the portal is running (this run was before it started).")
    path = os.environ.get("MSS_SOCKET") or os.path.join(str(settings.BASE_DIR), "sockets", "web.sock")
    try:
        info = os.stat(path)
    except OSError as exc:
        return Result("unknown", "Couldn't find the portal's socket, so this says nothing either way.",
                      who="webmaster", details=[("Path", path), ("Error", str(exc)[:160])],
                      webmaster="run_on_srcf.sh exports MSS_SOCKET; set it in .env if the socket lives elsewhere.")
    mode = stat.S_IMODE(info.st_mode)
    try:
        import grp

        group = grp.getgrgid(info.st_gid).gr_name
    except (ImportError, KeyError):
        group = str(info.st_gid)
    details = [("Path", path), ("Permissions", stat.filemode(info.st_mode)), ("Group", group)]
    opened = os.environ.get("MSS_SOCKET_OPEN") == "1"
    # Connecting to a unix socket takes write permission on it, and a way
    # through every folder above it.
    if mode & stat.S_IWOTH and _reachable_by_others(path):
        return Result(
            "fail", "Every SRCF user can connect to the portal directly, past Apache, and could log in as any "
                    "member, admins included." + (" The settings ask for this (MSS_SOCKET_OPEN=1)." if opened else ""),
            metric=oct(mode)[2:], fix="Tell the webmaster now.", who="webmaster", details=details,
            webmaster="gunicorn.conf.py hands the socket to the society's group and closes it to others; it "
                      "leaves it open when that fails (the service log says why) or when MSS_SOCKET_OPEN=1. "
                      "Until it is closed, DEPLOYMENT_SRCF.md's security invariant 2 is broken and RAVEN_MODE=header "
                      "is unsafe. Remove MSS_SOCKET_OPEN once Apache reaches the closed socket, and restart.")
    return Result("ok", "Only Apache and the society's own account can connect to the portal.",
                  metric=oct(mode)[2:], details=details)


def _reachable_by_others(path):
    """Does every folder above ``path`` let other users through?"""
    folder = os.path.dirname(os.path.abspath(path))
    while True:
        try:
            if not os.stat(folder).st_mode & stat.S_IXOTH:
                return False
        except OSError:
            return False
        parent = os.path.dirname(folder)
        if parent == folder:
            return True
        folder = parent


@functools.lru_cache(maxsize=1)
def _deploy_warnings():
    """Django's own deployment check, run once per process: settings only
    change with a restart."""
    from django.core import checks as django_checks

    found = django_checks.run_checks(include_deployment_checks=True)
    return [(message.id or "?", str(message.msg)[:200]) for message in found
            if message.level >= django_checks.WARNING and message.id not in KNOWN_DEPLOY_WARNINGS
            and not message.is_silenced()]


def _settings_changed(**kwargs):
    _deploy_warnings.cache_clear()


setting_changed.connect(_settings_changed)


@check("production_settings", "Security settings suit a live site", "security")
def production_settings(ctx):
    if not ctx.production:
        return Result("na", "Not applicable here: this copy runs with development settings.")
    fails, warns, details = [], [], []
    if getattr(settings, "RAVEN_MODE", "") == "oidc":
        fails.append("members can't log in: the University (OIDC) login isn't built yet (set RAVEN_MODE=header)")
    site = getattr(settings, "SITE_URL", "")
    if not site:
        warns.append("links in emails use whatever address a visitor happened to use (set DJANGO_SITE_URL)")
    else:
        host = urlsplit(site).hostname or ""
        from django.http.request import validate_host

        if not validate_host(host, settings.ALLOWED_HOSTS):
            fails.append(f"the site's own address ({host}) isn't in DJANGO_ALLOWED_HOSTS, so it answers "
                         "“Bad Request”")
        if site.startswith("http://"):
            warns.append("DJANGO_SITE_URL starts with http://, so emailed links aren't secure")
    try:
        found = _deploy_warnings()
    except Exception as exc:
        found = [("?", f"the check itself failed: {exc}"[:200])]
    if found:
        warns.append(f"Django's own security check lists {plural(len(found), 'problem')}")
        details += [(code, text) for code, text in found[:8]]
    details += [("HSTS", f"{settings.SECURE_HSTS_SECONDS} s" if getattr(settings, "SECURE_HSTS_SECONDS", 0)
                 else "off (turn on once the custom domain's HTTPS is settled)"),
                ("Allowed hosts", ", ".join(settings.ALLOWED_HOSTS))]
    if fails or warns:
        return Result("fail" if fails else "warn", sentence("; ".join(fails + warns)) + ".",
                      fix="Tell the webmaster.", who="webmaster", details=details,
                      webmaster="Each of these is a line in .env; restart after changing it.")
    return Result("ok", "Debug pages are off, cookies only travel over HTTPS, and Django's own security check "
                        "passes.", details=details)


@check("https_certificate", "The secure connection is valid", "security", slow=True)
def https_certificate(ctx):
    if not ctx.production:
        return Result("na", "Not applicable here: only the live site has a certificate.")
    data = ctx.probe("tls")
    if data.get("timed_out") or data.get("error"):
        return _timed_out(data)
    hosts = data.get("hosts") or []
    if data.get("skipped") or not hosts:
        return Result("na", "There's no public address to check from here.", who="webmaster",
                      webmaster="Set DJANGO_SITE_URL in .env.")
    fails, warns, unknown, details, days = [], [], [], [], []
    for entry in hosts:
        host = entry["host"]
        if entry.get("invalid"):
            fails.append(f"browsers refuse {host}: {entry['invalid']}")
            details.append((host, entry["invalid"]))
            continue
        if entry.get("error"):
            unknown.append(host)
            details.append((host, entry["error"]))
            continue
        days.append(entry["days"])
        details.append((host, f"valid until {entry['expires']} ({entry['days']:.0f} days), {entry.get('issuer', '')}"))
        if entry["days"] < 7:
            fails.append(f"the certificate for {host} expires in {entry['days']:.0f} days")
        elif entry["days"] < 21:
            warns.append(f"the certificate for {host} expires in {entry['days']:.0f} days and hasn't renewed")
        if entry.get("http_status") == 200:
            warns.append(f"typing http://{host} doesn't move visitors to the secure site")
    metric = f"{min(days):.0f}\u00a0days left" if days else ""
    if fails or warns:
        return Result("fail" if fails else "warn", sentence("; ".join(fails + warns)) + ".", metric=metric,
                      fix="Tell the webmaster.", who="webmaster", details=details,
                      webmaster="The SRCF renews Let's Encrypt certificates about 30 days before they expire; "
                                "if one hasn't renewed, ask the SRCF sysadmins (support@srcf.net).")
    if not days:
        return Result("unknown", "Couldn't reach the site's secure address to check its certificate.",
                      details=details, who="webmaster")
    return Result("ok", f"The certificate is valid for another {min(days):.0f} days, and http:// moves visitors to "
                        "https://.", metric=metric, details=details)


@check("private_files", "Private files are private", "security", slow=True)
def private_files(ctx):
    if not ctx.production:
        return Result("na", "Not applicable here: only the live server has other users.")
    data = ctx.probe("files")
    if data.get("skipped"):
        return Result("na", "Not applicable here (Windows).")
    if data.get("timed_out") or data.get("error"):
        return _timed_out(data)
    fails, warns = [], []
    details = [("Settings file (.env)", "open to others" if data.get("env_open") else "private"),
               ("Uploads open to others", f"{data.get('media_open', 0)} of {data.get('media_checked', 0)}")]
    webmaster = []
    if data.get("env_open"):
        fails.append("the settings file, with the database password and the secret key, can be read by every "
                     "SRCF user")
        webmaster.append("chgrp mss .env && chmod 660 .env")
    if data.get("sqlite_open"):
        fails.append("the database file can be read by every SRCF user")
        webmaster.append("chmod o-rwx on the database file")
    if data.get("media_open"):
        warns.append(f"{plural(data['media_open'], 'member upload')} (photos) can be read by every SRCF user")
        webmaster.append("chmod -R o-rwx media")
    if data.get("loose_secret"):
        names = data["loose_secret"]
        fails.append("copies of the settings, the database or keys sit beside the code where every SRCF user can "
                     "read them: " + ", ".join(names[:5]))
        details.append(("Secret copies", ", ".join(names)))
        webmaster.append("move them to private space (/societies/mss) or chmod o-rwx them; if any held the secret "
                         "key or the database password, change those as for .env, since the SRCF's snapshots keep "
                         "old copies")
    if data.get("loose_personal"):
        names = data["loose_personal"]
        fails.append("spreadsheets that may hold members' details can be read by every SRCF user: "
                     + ", ".join(names[:5]))
        details.append(("Spreadsheets", ", ".join(names)))
        webmaster.append("move the spreadsheets to private space (/societies/mss) or delete them")
    if data.get("loose"):
        warns.append("archives sit beside the code where every SRCF user can read them: " + ", ".join(data["loose"][:5]))
        details.append(("Archives", ", ".join(data["loose"])))
        webmaster.append("move the archives out of the code folder, or chmod o-r them")
    if fails or warns:
        return Result("fail" if fails else "warn", sentence("; ".join(fails + warns)) + ".",
                      fix="Tell the webmaster.", who="webmaster", details=details,
                      webmaster="As the files' owner: " + "; ".join(webmaster) + f" ({UPGRADING}, one-time steps).")
    return Result("ok", "The settings file, the database and members' uploads are closed to other SRCF users.",
                  details=details)


@check("public_files", "Nothing private is in the public web folder", "security", slow=True)
def public_files(ctx):
    if not ctx.production:
        return Result("na", "Not applicable here: only the live server has a public web folder.")
    data = ctx.probe("files")
    if data.get("skipped"):
        return Result("na", "Not applicable here (Windows).")
    if data.get("timed_out") or data.get("error"):
        return _timed_out(data)
    root = data.get("public_root")
    if not root:
        return Result("unknown", "The portal doesn't know which folder is the public web folder.", who="webmaster",
                      webmaster="Set DJANGO_STATIC_ROOT (…/public_html/static), or MSS_PUBLIC_HTML, in .env.")
    if not data.get("public_found", True):
        return Result("unknown", "The public web folder named in the settings doesn't exist, so it couldn't be checked.",
                      who="webmaster", details=[("Folder", root)],
                      webmaster="Check DJANGO_STATIC_ROOT (…/public_html/static), or MSS_PUBLIC_HTML, in .env.")
    risky, personal, loose = (data.get("public_risky", []), data.get("public_personal", []),
                              data.get("public_data", []))
    read = {True: "yes", False: "NO", None: "couldn't read it"}
    details = [("Folder", root),
               ("Root .htaccess removes forged logins", read[data.get("htaccess_unsets")]),
               ("Raven folder asks for a University login", read[data.get("raven_gated")])]
    fails, warns = [], []
    if risky:
        fails.append("anyone on the internet could download " + ", ".join(risky[:5]))
        details.append(("Secrets or code", ", ".join(risky)))
    if personal:
        fails.append("anyone on the internet could download spreadsheets that may hold members' details: "
                     + ", ".join(personal[:5]))
        details.append(("Spreadsheets", ", ".join(personal)))
    if data.get("htaccess_unsets") is False:
        fails.append("the public folder's .htaccess no longer removes forged Raven login headers")
    if data.get("raven_gated") is False:
        fails.append("the Raven login folder no longer asks for a University login")
    if loose:
        warns.append("archives in the public folder can be downloaded by anyone: " + ", ".join(loose[:5]))
        details.append(("Archives", ", ".join(loose)))
    if fails or warns:
        return Result("fail" if fails else "warn", sentence("; ".join(fails + warns)) + ".",
                      fix="Tell the webmaster now." if fails else "Tell the webmaster.", who="webmaster",
                      details=details,
                      webmaster="Delete the files from public_html (keep copies in private space if needed); "
                                "the .htaccess lines are in DEPLOYMENT_SRCF.md §6 and §7.")
    if data.get("htaccess_unsets") is not True or data.get("raven_gated") is not True:
        return Result("unknown", "Nothing private is in the public web folder, but its two login rules couldn't be "
                                 "read, so they weren't checked here.", who="webmaster", details=details,
                      webmaster="Make sure public_html/.htaccess and public_html/accounts/raven/.htaccess exist and "
                                "can be read by the account running the checks (DEPLOYMENT_SRCF.md §6 and §7). "
                                "“Nobody can fake a Raven login” tests the rules from outside either way.")
    return Result("ok", "The public web folder holds only the site's own files, and both login rules are in place.",
                  details=details)


# --- Email -----------------------------------------------------------------------------------

@check("email_setup", "Emails can be sent", "email")
def email_setup(ctx):
    from django.urls import reverse

    from panel.models import MailLog
    from panel.views import HANDLED, STARTED

    real = settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
    sender = settings.DEFAULT_FROM_EMAIL
    address = parseaddr(sender)[1].lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    last = MailLog.objects.exclude(audience="test").order_by("-sent_at").first()
    details = [("Sends from", sender), ("Mail server", f"{getattr(settings, 'EMAIL_HOST', '-')}:"
                                                      f"{getattr(settings, 'EMAIL_PORT', '-')}" if real else "none (console)"),
               ("Last mailer", f"{ago(last.sent_at, ctx.now)} to {last.recipients}" if last else "never")]
    metric = f"last sent {ago(last.sent_at, ctx.now)}" if last else ""
    if not real:
        if ctx.production:
            return Result("warn", "“Send” on the Mailer tab only writes to the log: no email leaves the server.",
                          metric=metric, fix="Tell the webmaster.", who="webmaster", details=details,
                          webmaster="Set REAL_EMAIL=true in .env and restart.")
        return Result("off", "Emails are printed to the console on this copy, not sent.", details=details)
    stuck = (MailLog.objects.filter(error=STARTED, sent_at__lt=ctx.now - datetime.timedelta(minutes=30))
             .order_by("-sent_at").first())
    if stuck:
        when = timezone.localtime(stuck.sent_at)
        return Result(
            "warn", f"The mailer send “{stuck.subject}” on {when:%a} {when.day} {when:%b at %H:%M} was cut off "
                    "part-way, so some people may not have got it.",
            metric=metric, fix="Check “Recent sends” on the Mailer tab and decide whether to send to the rest; "
                               "then mark this handled.", who="committee", details=details,
            action={"url": reverse("panel:health_mail_handled", args=[stuck.pk]), "label": "Mark as handled"})
    if (last and not last.ok and last.error != STARTED and HANDLED not in last.error
            and last.sent_at >= ctx.now - datetime.timedelta(days=7)):
        return Result("warn", f"The last mailer send had problems: {last.error[:200]}", metric=metric,
                      fix="Read the error on the Mailer tab's “Recent sends”; if it names the mail server, tell "
                          "the webmaster. Then mark this handled.", who="committee", details=details,
                      action={"url": reverse("panel:health_mail_handled", args=[last.pk]),
                              "label": "Mark as handled"})
    if domain in STRICT_DOMAINS:
        return Result(
            "warn", f"Mail claims to come from {domain}, which other mail servers may treat as forged and junk.",
            metric=metric, fix="Tell the webmaster.", who="webmaster", details=details,
            webmaster=f"Set DEFAULT_FROM_EMAIL=\"MSS <mss-admins@srcf.net>\" in .env ({UPGRADING}); the "
                      "society's own address stays the reply-to.")
    return Result("ok", f"The mailer sends through the mail server, from {address or sender}.", metric=metric,
                  details=details)


@check("email_server", "The mail server answers", "email", slow=True)
def email_server(ctx):
    data = ctx.probe("smtp")
    if data.get("skipped"):
        return Result("off", "Email isn't switched on here, so there's no mail server to check.")
    where = f"{data.get('host')}:{data.get('port')}"
    if data.get("elsewhere"):  # e.g. the cron on sinkhole, whose localhost isn't the web server's
        answered = "answered" if not (data.get("timed_out") or data.get("error")) else "didn't answer"
        return Result("unknown", f"Checked from {data.get('from') or 'another machine'}, not the web server (where "
                                 f"the mail server {answered}), so this says nothing about the mailer. “Run all "
                                 "checks” on the site checks it from the web server.",
                      details=[("Server", where), ("Error", data.get("error", "") or "none")])
    if data.get("timed_out"):
        return Result("warn", "The mail server is very slow to answer, so mailer sends may fail.", who="webmaster",
                      details=[("Server", where), ("Error", data.get("error", ""))])
    if data.get("error"):
        return Result("fail", "The mail server isn't answering, so mailer sends will fail.", fix="Tell the webmaster.",
                      who="webmaster", details=[("Server", where), ("Error", data["error"])],
                      webmaster="On the SRCF the mail server is localhost:25 (EMAIL_HOST / EMAIL_PORT in .env).")
    if data.get("code") != 250:
        return Result("warn", f"The mail server answered oddly (code {data.get('code')}).", who="webmaster",
                      details=[("Server", where)])
    return Result("ok", f"The mail server answered in {data.get('ms', 0)} ms.", metric=f"{data.get('ms', 0)}\u00a0ms",
                  details=[("Server", where)])


# --- Background jobs & backups ---------------------------------------------------------------

CRON_DOC = "UPGRADING.md (“Polls close on time”)"


@check("scheduled_jobs", "Scheduled jobs are running", "jobs")
def scheduled_jobs(ctx):
    from panel.models import JobHeartbeat
    from polls.models import Poll
    from surveys.models import Survey

    beats = {beat.name: beat for beat in JobHeartbeat.objects.filter(name__in=["close_polls", "health_check"])}
    hour_ago = ctx.now - datetime.timedelta(hours=1)
    overdue = 0
    if ctx.production:  # a development copy has no cron, so its polls wait for a visit
        overdue = (Poll.objects.due().filter(closes_at__lt=hour_ago).count()
                   + Survey.objects.due().filter(closes_at__lt=hour_ago).count())
    statuses, notes, details = [], [], []
    close = beats.get("close_polls")
    details.append(("close_polls", f"last ran {ago(close.last_finished, ctx.now)}"
                                   + ("" if close.last_ok else f", failed: {close.last_error}")
                    if close and close.last_finished else "never"))
    if close is None or close.last_finished is None:
        if not ctx.production:
            statuses.append("off")
        elif _installed_lately(ctx):
            statuses.append("unknown")  # the cron may be fine: it just hasn't run since its table appeared
        else:
            statuses.append("warn")
            notes.append("the job that closes polls has never run, so results wait until someone opens the poll")
    elif not close.last_ok:
        statuses.append("warn")
        notes.append("the job that closes polls failed last time")
    elif close.last_finished < ctx.now - datetime.timedelta(hours=2):
        statuses.append("warn")
        notes.append(f"the job that closes polls last ran {ago(close.last_finished, ctx.now)} (it should run every "
                     "15 minutes)")
    else:
        statuses.append("ok")
    if overdue:
        statuses.append("warn")
        notes.append("a poll or survey is past its closing time but still open" if overdue == 1 else
                     f"{overdue} polls and surveys are past their closing time but still open")
    daily = beats.get("health_check")
    details.append(("Daily health check", f"last ran {ago(daily.last_finished, ctx.now)}"
                    if daily and daily.last_finished else "never"))
    if daily and daily.last_finished and daily.last_finished < ctx.now - datetime.timedelta(days=2):
        statuses.append("warn")
        notes.append(f"the daily health check last ran {ago(daily.last_finished, ctx.now)}")
    from .core import worst

    status = worst(statuses)
    if status in ("warn", "fail"):
        return Result(status, sentence("; ".join(notes)) + ".", fix="Tell the webmaster.", who="webmaster",
                      details=details, webmaster=f"The cron lines are in {CRON_DOC} and DEPLOYMENT_SRCF.md; "
                                                 "crontab -l on sinkhole shows what's scheduled.")
    if status == "off":
        return Result("off", "No scheduled jobs on this copy.", details=details)
    if status == "unknown":
        return Result("unknown", "The job that closes polls hasn't reported in since this version was installed; "
                                 "it should within 15 minutes.", details=details)
    extra = "" if daily else " There's no daily health check yet, so problems are only found when someone opens this page."
    return Result("ok", "Polls and surveys close on time." + extra, details=details,
                  webmaster="" if daily else "Add the daily `manage.py health_check --trigger cron --quiet --prune` "
                                             "cron line (UPGRADING.md); cron then emails you only when something "
                                             "is wrong.")


def _installed_lately(ctx, within=datetime.timedelta(hours=1)):
    """Was this release's health tables' migration applied in the last hour?
    Jobs can't have reported in before their table existed."""
    from django.db.migrations.recorder import MigrationRecorder

    try:
        applied = MigrationRecorder.Migration.objects.filter(app="panel", name="0004_site_health").values_list(
            "applied", flat=True).first()
    except Exception:
        return False
    return applied is not None and ctx.now - applied < within


BACKUP_HOW = ("Add the nightly dump from DEPLOYMENT_SRCF.md (“Backups”), then set MSS_BACKUP_DIR in .env to its "
              "folder and restart: from then on this checks that a fresh backup arrives every night.")
SMALLEST_DUMP = 1024  # bytes: a compressed dump of nothing is 13 to 32 bytes; a real one is many KB


DUMP_SUFFIXES = (".sql", ".gz", ".bz2", ".xz", ".zst", ".dump")


def _dumps(folder):
    """[(name, stat)] of the database dumps directly in ``folder``, newest
    first (a copy of .env kept there before an upgrade isn't one)."""
    found = [(entry.name, entry.stat()) for entry in os.scandir(folder)
             if entry.is_file() and entry.name.lower().endswith(DUMP_SUFFIXES)]
    return sorted(found, key=lambda item: item[1].st_mtime, reverse=True)


def _newest_file(folder):
    dumps = _dumps(folder)
    return dumps[0] if dumps else None


@check("backups", "The database is backed up", "jobs")
def backups(ctx):
    """Only judged once MSS_BACKUP_DIR says where a nightly backup goes: a
    folder of dumps made by hand before an upgrade isn't a nightly backup,
    and mustn't turn the page red three days later."""
    folder = os.environ.get("MSS_BACKUP_DIR", "").strip()
    if not folder:
        if not ctx.production:
            return Result("na", "Not applicable here: only the live site's database needs backing up.")
        usual = f"/societies/{os.environ.get('SRCF_SOCIETY', 'mss')}/backups"
        details = []
        try:
            newest = _newest_file(usual) if os.path.isdir(usual) else None
        except OSError:
            newest = None
        if newest:
            when = datetime.datetime.fromtimestamp(newest[1].st_mtime, tz=datetime.timezone.utc)
            details.append(("Newest file in " + usual, f"{newest[0]}, {ago(when, ctx.now)}"))
        return Result("off", "No nightly backup is set up; the SRCF's own snapshots are best-effort.",
                      fix="Ask the webmaster to set up the nightly backup.", who="webmaster", details=details,
                      webmaster=BACKUP_HOW)
    if not os.path.isdir(folder):
        return Result("fail", "The backups folder named in the settings doesn't exist, so nothing is being backed up.",
                      fix="Tell the webmaster.", who="webmaster", details=[("Folder", folder)], webmaster=BACKUP_HOW)
    try:
        dumps = _dumps(folder)
    except OSError as exc:
        return Result("unknown", "Couldn't look in the backups folder.", who="webmaster",
                      details=[("Folder", folder), ("Error", str(exc)[:160])])
    if not dumps:
        return Result("warn", "The backups folder is empty: the nightly backup isn't running.", fix="Tell the webmaster.",
                      who="webmaster", details=[("Folder", folder)],
                      webmaster="Check the backup line in the crontab (crontab -l on sinkhole).")
    name, info = dumps[0]
    when = datetime.datetime.fromtimestamp(info.st_mtime, tz=datetime.timezone.utc)
    details = [("Folder", folder), ("Newest", f"{name}, {size(info.st_size)}")]
    metric = ago(when, ctx.now)
    # A dump that failed still leaves a file: gzip of nothing is 20 bytes.
    if info.st_size < SMALLEST_DUMP:
        return Result("fail", "The newest backup is empty, so there's nothing to restore from.", metric=metric,
                      fix="Tell the webmaster.", who="webmaster", details=details,
                      webmaster="The dump command fails (often the database password in .my.cnf, or a .my.cnf the "
                                "crontab's owner can't read): run it by hand to see why.")
    age = ctx.now - when
    old = timesince(when, ctx.now).split(",")[0]
    if age > datetime.timedelta(hours=72):
        return Result("fail", f"The newest backup is {old} old.", metric=metric, fix="Tell the webmaster.",
                      who="webmaster", details=details, webmaster="Check the backup line in the crontab.")
    if age > datetime.timedelta(hours=26):
        return Result("warn", f"The newest backup is {old} old; it should be under a day.", metric=metric,
                      who="webmaster", details=details, webmaster="Check the backup line in the crontab.")
    if len(dumps) > 1 and info.st_size * 2 < dumps[1][1].st_size:
        return Result("warn", "The newest backup is less than half the size of the one before, so it may have been cut "
                              "off part-way.", metric=metric, fix="Tell the webmaster.", who="webmaster",
                      details=details + [("The one before", f"{dumps[1][0]}, {size(dumps[1][1].st_size)}")],
                      webmaster="Run the dump by hand and compare; a dump that stops early ends without "
                                "“-- Dump completed”. A big clear-out of old data also shrinks it, once.")
    return Result("ok", f"The latest backup was made {metric} ({size(info.st_size)}).", metric=metric, details=details)


# --- Deployment ------------------------------------------------------------------------------

@check("deploy_state", "The server runs the code that's on it", "deploy")
def deploy_state(ctx):
    from panel.models import HealthSnapshot

    state = deploy.migration_state()
    changes = deploy.changed_since_boot()
    details = [("Version", deploy.version_label() or "unknown")]
    started = deploy.BOOT.get("started")
    if started:
        details.append(("This worker started", f"{timezone.localtime(started):%d %b %Y, %H:%M} ({ago(started, ctx.now)})"))
    last_start = HealthSnapshot.objects.filter(trigger=HealthSnapshot.Trigger.DEPLOY).values_list(
        "created_at", flat=True).first()
    if last_start:
        details.append(("Last restart", ago(last_start, ctx.now)))
    if changes is None:
        details.append(("New files since the start", "only checked on the live site"))
    if state["behind"]:
        details.append(("Database updates waiting", ", ".join(state["behind"][:6])))
        return Result(
            "fail", "The database hasn't been updated for this version, so some pages will show errors.",
            fix="Tell the webmaster now: restarting the portal updates the database as it starts.",
            who="webmaster", details=details,
            webmaster=f"Restart ({UPGRADING} step 3): run_on_srcf.sh runs migrate at every start. If it stays red, "
                      "run `manage.py migrate` to see what fails.")
    notes, status = [], "ok"
    if changes and any(changes):
        changed, added, removed = changes
        status = "warn"
        notes.append("new code is on the server, but the portal hasn't restarted, so visitors still get the old "
                     "version")
        sample = (changed + added + removed)[:5]
        details.append(("Changed since the start", ", ".join(sample)
                        + (f" and {len(changed) + len(added) + len(removed) - 5} more"
                           if len(changed) + len(added) + len(removed) > 5 else "")))
    if state["ahead"]:
        status = "warn"
        notes.append("the database is newer than the code (was the code rolled back?)")
        details.append(("Database ahead by", ", ".join(state["ahead"][:6])))
    metric = f"started {ago(started, ctx.now)}" if started else ""
    if status == "warn":
        return Result("warn", sentence("; ".join(notes)) + ".", metric=metric,
                      fix="Ask the webmaster to restart the portal." if changes and any(changes) else "Tell the webmaster.",
                      who="webmaster", details=details,
                      webmaster=f"Restart ({UPGRADING} step 3). A worker that gunicorn restarts after a timeout loads "
                                "the new files while the other keeps the old, so restart the whole service rather "
                                "than waiting.")
    return Result("ok", "The database matches this version" + (", and nothing has changed since the portal started."
                                                               if changes is not None else "."),
                  metric=metric, details=details)


@check("database", "The database is healthy", "deploy")
def database(ctx):
    import time

    from django.contrib.sessions.models import Session

    started = time.monotonic()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    ms = int((time.monotonic() - started) * 1000)
    details = [("Engine", connection.vendor)]
    warns = []
    if connection.vendor == "mysql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@character_set_database")
            charset = cursor.fetchone()[0]
            cursor.execute("SELECT SUM(data_length + index_length) FROM information_schema.tables "
                           "WHERE table_schema = DATABASE()")
            used = cursor.fetchone()[0] or 0
        details += [("Character set", charset), ("Size", size(used))]
        if charset != "utf8mb4":
            warns.append(f"the database uses {charset}, so names with emoji or some accents can't be saved")
    elif connection.vendor == "sqlite":
        name = str(settings.DATABASES["default"]["NAME"])
        if os.path.exists(name):
            details.append(("Size", size(os.path.getsize(name))))
    expired = Session.objects.filter(expire_date__lt=ctx.now).count()
    details.append(("Expired login sessions", expired))
    if expired > 10_000:
        warns.append(f"{expired} expired login sessions are piling up")
    if ms > 500:
        warns.append(f"the database took {ms} ms to answer")
    if warns:
        return Result("warn", sentence("; ".join(warns)) + ".", metric=f"{ms}\u00a0ms", who="webmaster",
                      details=details, webmaster="`manage.py clearsessions` clears old sessions (the daily "
                                                 "`health_check --prune` does it too).")
    return Result("ok", f"The database answered in {ms} ms.", metric=f"{ms}\u00a0ms", details=details)


# --- Storage ---------------------------------------------------------------------------------

@check("storage", "There's room to store uploads", "storage", slow=True)
def storage(ctx):
    data = ctx.probe("storage")
    if data.get("timed_out") or data.get("error"):
        return _timed_out(data)
    if data.get("skipped"):
        return Result("unknown", "Not checked on a quick run.")
    used = data.get("media_bytes", 0) + data.get("static_bytes", 0) + data.get("sqlite_bytes", 0)
    details = [("Uploads", f"{size(data.get('media_bytes', 0))} in {data.get('media_files', 0)} files"
                + (" (so far)" if data.get("partial") else "")),
               ("Static copy", size(data.get("static_bytes", 0)))]
    if "sqlite_bytes" in data:
        details.append(("Database file", size(data["sqlite_bytes"])))
    free, total = data.get("disk_free"), data.get("disk_total")
    if free is not None:
        details.append(("Disk", f"{size(free)} free of {size(total)}"))
    quota = None
    try:
        quota = int(os.environ.get("MSS_QUOTA_MB", "") or 0) * 1024 * 1024 or None
    except ValueError:
        quota = None
    share = round(used * 100 / quota) if quota else None
    if quota:
        details.append(("Quota", f"{size(used)} of {size(quota)} ({share}%)"))
    metric = f"{share}% of quota" if quota else size(data.get("media_bytes", 0))
    if data.get("writable") is None:
        details.append(("Can save uploads", "checked only by “Run all checks” on the website, which runs as the "
                                            "account that saves them"))
    if data.get("writable") is False:
        return Result("fail", "Members can't upload photos and admins can't add pictures: the server can't save "
                              "files.", metric=metric, fix="Tell the webmaster.", who="webmaster",
                      details=details + [("Folder", data.get("writable_folder", "")),
                                         ("Error", data.get("write_error", ""))],
                      webmaster="The media folder must be writable by the account that runs gunicorn.")
    if (quota and share >= 95) or (free is not None and free < 200 * 1024 * 1024):
        return Result("fail", "The site has almost run out of space, so uploads and emails will start to fail.",
                      metric=metric, fix="Tell the webmaster.", who="webmaster", details=details,
                      webmaster="Remove old uploads or ask the SRCF for more quota.")
    if (quota and share >= 80) or (free is not None and free < 1024 * 1024 * 1024):
        return Result("warn", "Space is getting short.", metric=metric, who="webmaster", details=details,
                      webmaster="Check which uploads are large (Admin → Content → Pictures) or ask the SRCF for "
                                "more quota.")
    if data.get("writable") is None:  # run as another account: room is known, saving uploads isn't
        return Result("unknown", f"Uploads take {size(data.get('media_bytes', 0))} and there's room for more; whether "
                                 "uploads can be saved is only checked by “Run all checks” on the site.",
                      metric=metric, details=details)
    return Result("ok", f"Uploads take {size(data.get('media_bytes', 0))}, and there's room for more.", metric=metric,
                  details=details)


# --- Connected services ----------------------------------------------------------------------

@check("connected_services", "Optional services are connected", "services")
def connected_services(ctx):
    from core.models import SiteConfig

    config = SiteConfig.get()
    ai, maps = bool(config.email_api_key), bool(config.geoapify_api_key)
    details = [("Draft with AI", f"{config.get_email_ai_engine_display()} key saved" if ai else "no key"),
               ("Poster maps", "Geoapify key saved" if maps else "no key")]
    if ai and maps:
        return Result("ok", "“Draft with AI” and poster maps are both set up.", details=details)
    if not ai and not maps:
        return Result("off", "Neither “Draft with AI” nor poster maps is set up. Both are optional.",
                      who="webmaster", details=details,
                      webmaster="Paste the keys on the Super admin tab.")
    missing = "poster maps (posters show a directions code instead)" if ai else "“Draft with AI”"
    return Result("ok", f"Set up, except {missing}, which is optional.", who="webmaster", details=details,
                  webmaster="Paste the key on the Super admin tab.")


# --- Society setup ---------------------------------------------------------------------------

@check("admin_team", "The admin team is in place", "setup")
def admin_team(ctx):
    from accounts.models import User

    admins = User.objects.filter(is_portal_admin=True, is_banned=False)
    count = admins.count()
    supers = admins.filter(is_super_admin=True).count()
    idle = list(admins.filter(Q(last_login__lt=ctx.now - datetime.timedelta(days=365)) | Q(last_login__isnull=True))
                .values_list("first_name", "last_name", "username")[:6])
    details = [("Admins", count), ("Super admins", supers)]
    if idle:
        details.append(("Not logged in for a year", ", ".join(f"{first} {last}".strip() or user
                                                               for first, last, user in idle)))
    if count == 0:
        return Result("fail", "Nobody can run the admin panel.", fix="Tell the webmaster.", who="webmaster",
                      details=details, webmaster="Give someone is_portal_admin in /dj-admin/.")
    notes = []
    if supers == 0:
        notes.append("nobody is a super admin, so nobody can appoint admins or change the email and map keys")
    if count == 1:
        notes.append("there's only one admin: if they leave, nobody can run the site")
    if idle:
        names = ", ".join(f"{first} {last}".strip() or user for first, last, user in idle)
        notes.append((f"{names} hasn't" if len(idle) == 1 else f"{len(idle)} admins ({names}) haven't")
                     + " logged in for a year; take admin rights away from anyone who has left")
    if notes:
        fix = ("Ask the webmaster to make someone a super admin in the Django admin (/dj-admin/)." if supers == 0
               else "A super admin appoints and removes admins on the Super admin tab; ask one if that isn't you.")
        return Result("warn", sentence("; ".join(notes)) + ".", metric=plural(count, "admin"), who="webmaster",
                      fix=fix, details=details)
    supers_text = "all of them super admins" if supers == count else (
        "one of them a super admin" if supers == 1 else f"{supers} of them super admins")
    return Result("ok", f"{plural(count, 'admin')}, {supers_text}.", metric=plural(count, "admin"), details=details)


@check("terms", "Members accept the current terms", "setup")
def terms(ctx):
    from accounts.models import User
    from core.models import TermsVersion

    current = TermsVersion.current_number()
    if current is None:
        return Result("warn", "No terms and conditions are published, so new members aren't asked to accept any.",
                      fix="Publish them on Content → Terms.", who="committee")
    outstanding = User.objects.filter(is_banned=False).exclude(terms_accepted_version__gte=current).count()
    return Result("ok", f"Version {current} is published; {plural(outstanding, 'member')} will be asked to accept "
                        "it on their next visit." if outstanding else f"Version {current} is published and "
                                                                       "everyone has accepted it.",
                  metric=f"v{current}")


@check("society_setup", "The society's details are filled in", "setup")
def society_setup(ctx):
    from core.models import SiteConfig

    config = SiteConfig.get()
    today = timezone.localdate()
    notes, fixes, who = [], [], "committee"
    if not config.contact_email:
        notes.append("there's no contact email for members to write to")
        fixes.append("ask the webmaster to set it (Django admin → Site config)")
        who = "webmaster"
    starts = [day for day in (config.michaelmas_start, config.lent_start, config.easter_start) if day]
    if not starts:
        notes.append("term dates aren't set, so the calendar shows no term weeks")
        fixes.append("enter the Full Term start dates on Content → Navigation")
    elif max(starts) < today - datetime.timedelta(days=70):  # the last term entered has ended (8 weeks)
        notes.append("the next term's dates aren't in yet, so the calendar won't label its weeks")
        fixes.append("enter the coming year's Full Term start dates on Content → Navigation")
    details = [("Contact email", config.contact_email or "none"),
               ("Term starts", ", ".join(f"{day:%d %b %Y}" for day in starts) or "none")]
    if notes:
        return Result("warn", sentence("; ".join(notes)) + ".", who=who, fix=sentence("; ".join(fixes)) + ".",
                      details=details)
    return Result("ok", "Contact details and this year's term dates are in place.", details=details)
