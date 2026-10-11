"""Where a request came from, and the address emails should link to."""

from django.conf import settings


def client_ip(request):
    """The visitor's address. Behind the SRCF's Apache, REMOTE_ADDR is the
    proxy's own; Apache appends the address it saw to X-Forwarded-For, so
    the last entry is the one that can't be forged (anything before it was
    sent by the client)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def absolute_url(request, path):
    """A full address for a link in an email. With DJANGO_SITE_URL set it is
    built from that, so a visitor's forged Host header can't bend it;
    otherwise from the request."""
    base = settings.SITE_URL
    return f"{base}{path}" if base else request.build_absolute_uri(path)
