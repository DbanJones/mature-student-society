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
from functools import lru_cache

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage


@lru_cache(maxsize=512)
def _version(name):
    path = finders.find(name)
    if not path:
        return ""
    digest = hashlib.md5(usedforsecurity=False)
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
