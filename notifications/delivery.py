"""Leased, bounded email delivery. SMTP runs outside database transactions."""

from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F
from django.utils import timezone

from .models import NotificationEmail

MAX_ATTEMPTS = 5
LEASE = timedelta(minutes=10)


def _claim():
    now = timezone.now()
    NotificationEmail.objects.filter(
        status=NotificationEmail.Status.SENDING, available_at__lte=now,
        attempts__gte=MAX_ATTEMPTS,
    ).update(status=NotificationEmail.Status.FAILED, claim_token=None,
             last_error="DeliveryInterrupted")
    eligible = NotificationEmail.objects.filter(
        status__in=[NotificationEmail.Status.PENDING, NotificationEmail.Status.SENDING],
        available_at__lte=now, attempts__lt=MAX_ATTEMPTS,
    )
    # A conditional claim protects even databases without SELECT FOR UPDATE:
    # another worker can only claim a row while it is still eligible.
    for pk in eligible.order_by("pk").values_list("pk", flat=True)[:20]:
        token = uuid4()
        if eligible.filter(pk=pk).update(
            status=NotificationEmail.Status.SENDING, claim_token=token,
            claimed_at=now, available_at=now + LEASE, attempts=F("attempts") + 1,
        ):
            return NotificationEmail.objects.select_related("recipient").get(pk=pk, claim_token=token)
    return None


def deliver_emails(limit=100):
    """Return messages accepted by the mail backend, not just attempted.

    Delivery is at least once: a crash after SMTP accepts but before saving
    the acknowledgement can duplicate an email, never its underlying action.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    sent = 0
    for _ in range(limit):
        job = _claim()
        if job is None:
            break
        owned = NotificationEmail.objects.filter(pk=job.pk, claim_token=job.claim_token,
                                                status=NotificationEmail.Status.SENDING)
        member = job.recipient
        if not member.is_active or member.is_banned or not member.email:
            owned.update(status=NotificationEmail.Status.SKIPPED, claim_token=None, last_error="")
            continue
        try:
            accepted = send_mail(job.subject, job.body, settings.DEFAULT_FROM_EMAIL,
                                 [member.email], fail_silently=False)
            if not accepted:
                raise RuntimeError("Mail backend did not accept the message")
        except Exception as error:
            owned.update(
                status=(NotificationEmail.Status.FAILED if job.attempts >= MAX_ATTEMPTS
                        else NotificationEmail.Status.PENDING),
                available_at=timezone.now() + timedelta(minutes=min(60, 2 ** job.attempts)),
                claim_token=None, last_error=type(error).__name__[:80],
            )
        else:
            if owned.update(status=NotificationEmail.Status.SENT, sent_at=timezone.now(),
                            claim_token=None, last_error=""):
                sent += 1
    return sent


def retry_failed(queryset=None):
    """Queue failed messages after the mail fault is fixed."""
    rows = queryset if queryset is not None else NotificationEmail.objects.all()
    return rows.filter(status=NotificationEmail.Status.FAILED).update(
        status=NotificationEmail.Status.PENDING, attempts=0, available_at=timezone.now(),
        claim_token=None, claimed_at=None, last_error="",
    )
