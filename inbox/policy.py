"""The one place that decides who may message whom.

Rules, in order:

1. Muted members can't send at all (admins are never muted).
2. Nobody can message a suspended account.
3. A block in either direction stops both directions.
4. In restricted mode (the default) an ordinary member can only write to a
   committee admin, or to the host of an upcoming event they are going to
   (and the host can reply). Admins, and members an admin has enabled, can
   message anyone. In open mode every member can message every other member.

The daily cap is checked separately by the view: it is a rate limit, not a
permission, so it must not hide the Message button on a profile.
"""

from django.db.models import Q
from django.utils import timezone

from core.models import SiteConfig

from .models import MessageBlock

MUTED = (
    "Your messaging has been restricted by the committee. Contact them if "
    "you think this is a mistake."
)
SUSPENDED = "That member's account is suspended."
BLOCKED = "You can't exchange messages with this member."
RESTRICTED = (
    "Direct messages between members are switched off. You can still "
    "message a committee admin or the host of an event you're going to, or "
    "ask the committee to switch messaging on for you."
)


def _hosts_event_attended_by(host, attendee):
    from events.models import RSVP, Event

    return (
        Event.objects.filter(is_cancelled=False, start__gte=timezone.now())
        .filter(Q(host=host) | Q(host__isnull=True, created_by=host))
        .filter(rsvps__user=attendee, rsvps__status=RSVP.Status.GOING)
        .exists()
    )


def share_an_upcoming_event(a, b):
    """Is one of them hosting an upcoming event the other is going to?
    Logistics between host and attendee must not be blocked."""
    return _hosts_event_attended_by(a, b) or _hosts_event_attended_by(b, a)


def messaging_denied_reason(sender, recipient):
    """Why ``sender`` may not message ``recipient``, or None if they may."""
    if sender.is_muted:
        return MUTED
    if recipient.is_banned:
        return SUSPENDED
    if MessageBlock.exists_between(sender, recipient):
        return BLOCKED
    if (
        SiteConfig.get().messaging_mode == SiteConfig.MessagingMode.RESTRICTED
        and not sender.messaging_enabled
        and not recipient.is_portal_admin
        and not share_an_upcoming_event(sender, recipient)
    ):
        return RESTRICTED
    return None


def can_message(sender, recipient):
    """Should ``sender`` be offered a Message button for ``recipient``?"""
    return (
        sender.is_authenticated
        and sender.pk != recipient.pk
        and messaging_denied_reason(sender, recipient) is None
    )
