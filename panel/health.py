"""Is the web server serving the files this version of the site ships with?

On the SRCF, Apache serves /static/ straight from a folder on disk. If that
folder isn't kept up to date, every page gets new HTML with old stylesheets.
The Site health page (panel/healthchecks) checks two things separately, so
the cause is clear:

- the copy in STATIC_ROOT (filled by sync_static on every start) against the
  source, with no web request at all;
- what the public site actually serves, file by file, against the source.

If the copy is current but the site serves something else, Apache is
reading a different folder from STATIC_ROOT (see DJANGO_STATIC_ROOT).
"""

import filecmp
import hashlib
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urljoin

from django.conf import settings
from django.http.request import split_domain_port
from django.templatetags.static import static

from core.storage import static_sources

TIMEOUT = 4
BUDGET = 12  # seconds for the whole check, well inside gunicorn's 30
ADMIN_PROBE = "admin/css/base.css"  # only a real collect contains Django's own files


def _digest(data):
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _origin(request):
    """The site's own origin, from the request's host name. Only the name is
    checked against ALLOWED_HOSTS, so the port is never taken from it."""
    domain, _port = split_domain_port(request.get_host())
    return f"{request.scheme}://{domain}"


def _served(name, source, url):
    try:
        with open(source, "rb") as handle:
            expected = _digest(handle.read())
    except OSError:
        return {"name": name, "status": "unreadable here", "url": url}
    try:
        served = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "MSS portal health check"}),
            timeout=TIMEOUT,
        )
        status = "ok" if _digest(served.read()) == expected else "stale"
    except urllib.error.HTTPError as exc:
        status = "missing" if exc.code == 404 else f"HTTP {exc.code}"
    except Exception:  # refused, timed out, bad TLS: the server could not fetch its own site
        status = "unreachable"
    return {"name": name, "status": status, "url": url}


def collected_copy():
    """How the copy in STATIC_ROOT compares with the source: every file the
    site ships, Django's admin included. No web request."""
    root = str(settings.STATIC_ROOT)
    missing = differ = 0
    sources = static_sources()
    for name, source in sources.items():
        target = os.path.join(root, *name.split("/"))
        try:
            if not os.path.isfile(target):
                missing += 1
            elif not filecmp.cmp(source, target, shallow=False):
                differ += 1
        except OSError:
            differ += 1
    return {"root": root, "total": len(sources), "missing": missing, "differ": differ,
            "current": missing == 0 and differ == 0}


def static_health(request=None, *, origin=None, budget=BUDGET):
    """The two checks. ``served`` has one row per checked file: the portal's
    own files and one of Django's, each ``ok``, ``stale`` (served but
    different), ``missing`` (404), ``HTTP <code>``, ``unreadable here``,
    ``unreachable`` or ``timed out``. The files are fetched from ``origin``
    (else the request's own address) every time this is called, which only
    a full run of the site health checks does. Never raises."""
    own = str(settings.BASE_DIR / "static")
    sources = static_sources()
    checked = {name: path for name, path in sources.items() if path.startswith(own) or name == ADMIN_PROBE}
    origin = origin or _origin(request)
    names = sorted(checked)
    rows = []
    pool = ThreadPoolExecutor(max_workers=max(1, len(names)))
    try:
        futures = {name: pool.submit(_served, name, checked[name], urljoin(origin, static(name))) for name in names}
        wait(futures.values(), timeout=budget)
        for name in names:
            future = futures[name]
            if future.done() and future.exception() is None:
                rows.append(future.result())
            else:
                rows.append({"name": name, "status": "timed out" if not future.done() else "unreachable", "url": ""})
    except Exception:
        rows = [{"name": name, "status": "unreachable", "url": ""} for name in names]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    try:
        copy = collected_copy()
    except Exception:
        copy = None
    problems = [row for row in rows if row["status"] != "ok"]
    statuses = {row["status"] for row in problems}
    if not problems:
        cause = ""
    elif statuses <= {"stale", "missing"}:
        cause = "folder"  # served, but not what was copied: Apache reads another folder, or the copy failed
    elif "HTTP 403" in statuses:
        cause = "permissions"
    elif statuses <= {"unreachable", "timed out"}:
        cause = "fetch"
    else:
        cause = "other"
    return {"served": rows, "problems": problems, "ok": len(rows) - len(problems), "copy": copy, "cause": cause}
