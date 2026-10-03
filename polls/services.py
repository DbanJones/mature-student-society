"""What happens when a poll decides something."""

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.formats import date_format


def attendee_emails(event):
    """Everyone going, plus the host: the people who need to hear a result."""
    from events.models import RSVP

    emails = set(
        event.rsvps.filter(status=RSVP.Status.GOING, user__is_banned=False)
        .exclude(user__email="")
        .values_list("user__email", flat=True)
    )
    if event.effective_host.email:
        emails.add(event.effective_host.email)
    return sorted(emails)


def apply_outcome(poll, by=None):
    """Write a venue or date poll's winner onto its event, log it, and tell
    everyone going. Idempotent: a poll is applied at most once."""
    from panel.models import AuditLog

    event, option = poll.event, poll.outcome_option
    if event is None or option is None or poll.applied_at is not None:
        return
    if poll.kind == poll.Kind.VENUE:
        event.location = option.label
        event.save(update_fields=["location", "updated_at"])
        what = f"Venue: {option.label}"
    elif poll.kind == poll.Kind.DATE and option.start:
        duration = (event.end - event.start) if event.end else None
        event.start = option.start
        event.end = option.start + duration if duration else None
        event.save(update_fields=["start", "end", "updated_at"])
        what = "Date: " + date_format(timezone.localtime(option.start), "D j M Y, H:i")
    else:
        return
    poll.applied_at = timezone.now()
    poll.save(update_fields=["applied_at"])
    AuditLog.record(by, "apply_poll", target=event.title, detail=what[:300])

    from notifications.models import Notification
    from notifications.services import notify
    from events.models import RSVP

    notify(
        [r.user for r in event.rsvps.filter(status=RSVP.Status.GOING).select_related("user")]
        + [event.effective_host],
        Notification.Kind.POLL, f"{event.title}: {what}", event.get_absolute_url(),
    )

    recipients = attendee_emails(event)
    if recipients:
        EmailMessage(
            subject=f"{event.title}: {what}",
            body=(
                f"The poll “{poll.question}” has closed.\n\n"
                f"{what}\n\n"
                f"The event page has the details: {event.get_absolute_url()}\n\n"
                "— The MSS portal"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            bcc=recipients,
        ).send(fail_silently=True)
