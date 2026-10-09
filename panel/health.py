"""Is the web server serving the files this version of the site ships with?

On the SRCF, Apache serves /static/ from a folder that ``collectstatic``
fills. Skip that step after a deploy and every page gets new HTML with old
stylesheets: a stray menu control on the desktop, an unstyled poster
studio, text set in the wrong font. The Super admin tab can ask the public
site for a few files and compare them with the source, so the mismatch is
seen in the panel rather than guessed at from a strange-looking page.
"""

import hashlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.templatetags.static import static

CHECKED = ["css/base.css", "css/panel.css", "css/posters.css", "js/poster-studio.js"]
CACHE_KEY = "panel.static_health"
TIMEOUT = 4


def _digest(data):
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _check(name, url):
    path = finders.find(name)
    if not path:
        return {"name": name, "status": "not in this version", "url": url}
    with open(path, "rb") as handle:
        expected = _digest(handle.read())
    try:
        served = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "MSS portal health check"}),
            timeout=TIMEOUT,
        )
        status = "ok" if _digest(served.read()) == expected else "stale"
    except Exception as exc:  # a 404 is "missing"; anything else "unreachable"
        status = "missing" if getattr(exc, "code", None) == 404 else "unreachable"
    return {"name": name, "status": status, "url": url}


def static_health(request, fresh=False):
    """One row per checked file -- ``ok``, ``stale`` (served but different),
    ``missing`` (404) or ``unreachable`` -- or None until a check has run.
    Only ``fresh`` fetches; the result is kept for five minutes. Never raises."""
    if not fresh:
        return cache.get(CACHE_KEY)
    urls = {name: request.build_absolute_uri(static(name)) for name in CHECKED}
    with ThreadPoolExecutor(max_workers=len(CHECKED)) as pool:
        rows = list(pool.map(lambda name: _check(name, urls[name]), CHECKED))
    cache.set(CACHE_KEY, rows, 300)
    return rows
