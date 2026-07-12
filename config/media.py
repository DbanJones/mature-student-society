from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.static import serve


@login_required
def protected_media(request, path):
    """Serve uploads (member photos) to logged-in members only.

    MEDIA_ROOT lives in the society's PRIVATE space on the SRCF and is never
    exposed via Apache/public_html, so member photos stay members-only. The
    volume is tiny (profile pictures), so serving through Django is fine.
    """
    return serve(request, path, document_root=settings.MEDIA_ROOT)
