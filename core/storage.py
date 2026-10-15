"""Static file URLs that change whenever the file does.

Apache serves /static/ straight from disk and browsers cache it, so after a
deploy people could get the new HTML with last term's stylesheet (that is
how the menu's hidden checkbox ended up showing on the live site). Adding
``?v=<content hash>`` to every static URL makes each changed file a new URL.

Unlike ManifestStaticFilesStorage this needs no manifest: a missing file
just gets no version, so a forgotten collectstatic can never take the site
down.
"""

import hashlib
import os
from functools import lru_cache

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage

# Mixed into every version, so changing it changes every static URL at once
# and browsers that cached a stale copy fetch the file again. Changed on
# 11 Oct 2026, after the SRCF had served September's stylesheets under
# October's URLs.
STAMP = b"2026-10-11"
IGNORE = ["CVS", ".*", "*~"]  # as collectstatic


def static_sources():
    """``{url name: source path}`` for every static file the site ships (its
    own and Django's admin), the first finder winning, as collectstatic."""
    sources = {}
    for finder in finders.get_finders():
        for path, storage in finder.list(IGNORE):
            prefix = getattr(storage, "prefix", None)
            name = (f"{prefix}/{path}" if prefix else path).replace("\\", "/")
            sources.setdefault(name, storage.path(path))
    return sources


def _served_copy(name):
    """The copy Apache serves, when stamps follow it and it exists."""
    if not getattr(settings, "STATIC_VERSION_FROM_ROOT", False):
        return None
    path = os.path.join(str(settings.STATIC_ROOT), *name.split("/"))
    return path if os.path.isfile(path) else None


@lru_cache(maxsize=512)
def _version(name):
    path = _served_copy(name) or finders.find(name)
    if not path:
        return ""
    digest = hashlib.md5(STAMP, usedforsecurity=False)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:10]


class VersionedStaticFilesStorage(StaticFilesStorage):
    def url(self, name):
        url = super().url(name)
        try:
            version = _version(name)
        except OSError:
            version = ""
        if not version or "?" in url:
            return url
        return f"{url}?v={version}"
