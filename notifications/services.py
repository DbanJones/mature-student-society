"""Commit notifications and queued emails together; never send mail in a request."""

from django.conf import settings
from django.db import transaction

from .models import Notification, NotificationEmail


def _recipients(recipients, exclude=()):
    skip = {u.pk for u in exclude if u is not None}
    seen = set()
    for user in recipients:
        if (user is None or user.pk in skip or user.pk in seen
                or user.is_banned or not user.is_active):
            continue
        seen.add(user.pk)
        yield user


def queue_email(recipients, subject, body):
    """Queue one private message per eligible member in the caller's transaction.

    A failed email can be retried without repeating the action that produced it.
    """
    subject = " ".join(str(subject).splitlines())[:255]
    return NotificationEmail.objects.bulk_create([
        NotificationEmail(recipient=user, subject=subject, body=body)
        for user in _recipients(recipients) if user.email
    ])


@transaction.atomic
def notify(recipients, kind, text, url="", email_subject=None, email_body=None,
           exclude=()):
    """Create the bell notification immediately and queue any associated email."""
    people = list(_recipients(recipients, exclude))
    rows = Notification.objects.bulk_create([
        Notification(recipient=user, kind=kind, text=text[:200], url=url[:300])
        for user in people
    ])
    if email_subject:
        body = email_body or text
        link = url
        if url.startswith("/") and not url.startswith("//"):
            link = getattr(settings, "SITE_URL", "").rstrip("/") + url
        if link and link not in body:
            body += f"\n\n{link}"
        queue_email(people, email_subject, body)
    return rows


def process_notification_emails(limit=100):
    from .delivery import deliver_emails
    return deliver_emails(limit=limit)
