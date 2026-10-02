"""``notify``: the one way anything creates notifications."""

from django.conf import settings
from django.core.mail import EmailMessage

from .models import Notification


def notify(recipients, kind, text, url="", email_subject=None, email_body=None,
           exclude=()):
    """Create one notification per recipient (banned members and anyone in
    ``exclude`` skipped). With ``email_subject`` the same people are also
    emailed, bcc'd, in one message."""
    skip = {u.pk for u in exclude if u is not None}
    seen, people = set(), []
    for user in recipients:
        if user is None or user.pk in skip or user.pk in seen or user.is_banned:
            continue
        seen.add(user.pk)
        people.append(user)
    if not people:
        return []
    rows = Notification.objects.bulk_create([
        Notification(recipient=user, kind=kind, text=text[:200], url=url[:300])
        for user in people
    ])
    if email_subject:
        emails = sorted({u.email for u in people if u.email})
        if emails:
            EmailMessage(
                subject=email_subject,
                body=email_body or f"{text}{chr(10)}{chr(10)}{url}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                bcc=emails,
            ).send(fail_silently=True)
    return rows
