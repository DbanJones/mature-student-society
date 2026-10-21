"""The one place that decides who may message whom.

Rules, in order:

1. Muted members can't send at all (admins are never muted).
2. Nobody can message a suspended account.
3. A block in either direction stops both directions.
4. In restricted mode (the default) an ordinary member can only write to a
   committee admin, or to the host of an upcoming event they are going to
   (and the host can reply), or another confirmed member at the same upcoming
   formal. Admins, and members an admin has enabled, can
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
    "message a committee admin, your event host, or confirmed members at your "
    "formal before it starts, or "
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
    return (
        _hosts_event_attended_by(a, b) or _hosts_event_attended_by(b, a)
        or _share_formal(a, b)
    )


def _share_formal(a, b):
    """A host and guest can coordinate a request or an outstanding return."""
    from formals.models import FormalBooking, FormalExchange

    participants = (
        Q(listing__event__host=a, guest=b)
        | Q(listing__event__host=b, guest=a)
        | Q(listing__event__host__isnull=True, listing__event__created_by=a, guest=b)
        | Q(listing__event__host__isnull=True, listing__event__created_by=b, guest=a)
    )
    if FormalBooking.objects.filter(
        participants, listing__event__is_cancelled=False,
        listing__event__start__gt=timezone.now(),
    ).filter(
        Q(status__in=["requested", "reserved"])
        | Q(status="held", return_proposal__status="pending",
            return_proposal__expires_at__gt=timezone.now())
    ).exists():
        return True
    # A booking request or an unaccepted return hold is not confirmed attendance.
    # This permission is rechecked on every send and ends at the formal's start.
    if (
        a.is_active and b.is_active and not a.is_banned and not b.is_banned
        and not a.is_shadow_banned and not b.is_shadow_banned
        and FormalBooking.objects.filter(
            guest=a, status="reserved", listing__event__is_cancelled=False,
            listing__event__start__gt=timezone.now(),
            listing__bookings__guest=b, listing__bookings__status="reserved",
        ).exists()
    ):
        return True
    return FormalExchange.objects.filter(
        Q(creditor=a, debtor=b) | Q(creditor=b, debtor=a),
        status__in=["conditional", "owed", "disputed"],
    ).exists()


def messaging_denied_reason(sender, recipient):
    """Why ``sender`` may not message ``recipient``, or None if they may."""
    if sender.is_muted:
        return MUTED
    if recipient.is_banned or not recipient.is_active:
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
