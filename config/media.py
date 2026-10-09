import posixpath

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.views.static import serve


def protected_media(request, path):
    """Serve uploads from MEDIA_ROOT, which lives in the society's PRIVATE
    space on the SRCF and is never exposed via Apache.

    Pictures for the pages live under ``public/`` and are served to anyone,
    since the pages are. Everything else (member and event photos) is for
    logged-in members only. The volume is tiny, so serving through Django
    is fine. The path is normalised before the ``public/`` test so that
    ``public/../profiles/x.jpg`` is a members-only path, not a public one.
    """
    if "\\" in path:
        raise Http404
    clean = posixpath.normpath(path).lstrip("/")
    if clean.startswith("public/"):
        return serve(request, clean, document_root=settings.MEDIA_ROOT)
    return _members_only(request, clean)


@login_required
def _members_only(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)
