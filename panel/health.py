"""Is the web server serving the files this version of the site ships with?

On the SRCF, Apache serves /static/ from a folder that ``collectstatic``
fills. Skip that step after a deploy and every page gets new HTML with old
stylesheets: a stray menu control on the desktop, an unstyled poster
studio, text set in the wrong font. The Super admin tab can ask the public
site for a few files and compare them with the source, so the mismatch is
seen in the panel rather than guessed at from a strange-looking page.
"""

import hashlib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

from django.contrib.staticfiles import finders
from django.http.request import split_domain_port
from django.templatetags.static import static

CHECKED = ["css/base.css", "css/panel.css", "css/posters.css", "js/poster-studio.js"]
TIMEOUT = 4


def _digest(data):
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _origin(request):
    """The site's own origin, from the request's host name. Only the name is
    checked against ALLOWED_HOSTS, so the port is never taken from it."""
    domain, _port = split_domain_port(request.get_host())
    return f"{request.scheme}://{domain}"


def _check(name, url):
    try:
        path = finders.find(name)
        if not path:
            return {"name": name, "status": "not in this version", "url": url}
        with open(path, "rb") as handle:
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


def static_health(request):
    """One row per checked file: ``ok``, ``stale`` (served but different),
    ``missing`` (404), ``HTTP <code>``, ``unreachable``, ``unreadable here``
    or ``not in this version``. Fetches every time it is called (only the
    "Check now" button calls it, and nothing is remembered: each gunicorn
    worker has its own memory). Never raises."""
    origin = _origin(request)
    urls = {name: urljoin(origin, static(name)) for name in CHECKED}
    try:
        with ThreadPoolExecutor(max_workers=len(CHECKED)) as pool:
            return list(pool.map(lambda name: _check(name, urls[name]), CHECKED))
    except Exception:
        return [{"name": name, "status": "unreachable", "url": urls[name]} for name in CHECKED]
