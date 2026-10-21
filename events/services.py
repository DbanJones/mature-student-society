"""Serialise event places and waitlist promotion on the event's database row."""
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Event, RSVP


class EventBookingError(ValidationError):
    pass


@dataclass
class RSVPResult:
    event: Event
    rsvp: RSVP | None
    changed: bool
    previous: str
    waiting_position: int | None = None


def _member(user):
    if not user or not user.is_authenticated or not user.is_active or user.is_banned:
        raise PermissionDenied("An active member account is required.")


def _result(event, rsvp, changed, previous):
    position = None
    if rsvp is not None and rsvp.status == RSVP.Status.WAITING:
        position = list(event.waiting.values_list("pk", flat=True)).index(rsvp.pk) + 1
    return RSVPResult(event, rsvp, changed, previous, position)


def _promote_locked(event):
    """Caller holds the event row lock; only eligible members take places."""
    if event.is_formal or event.is_cancelled or event.is_past:
        return []
    waiters = event.waiting.filter(user__is_active=True, user__is_banned=False)
    if event.capacity is not None:
        room = max(0, event.capacity - event.going_count)
        waiters = waiters[:room]
    promoted = list(waiters)
    for rsvp in promoted:
        rsvp.status = RSVP.Status.GOING
        rsvp.save(update_fields=["status", "updated_at"])
    return promoted


def tell_promoted(event, promoted):
    if not promoted:
        return
    from notifications.models import Notification
    from notifications.services import notify

    when = timezone.localtime(event.start).strftime("%A %d %B at %H:%M")
    url = getattr(settings, "SITE_URL", "").rstrip("/") + event.get_absolute_url()
    notify(
        [rsvp.user for rsvp in promoted], Notification.Kind.WAITLIST,
        f"A place opened up: you're now going to {event.title}.", event.get_absolute_url(),
        email_subject=f"A place has opened up: {event.title}",
        email_body=(f'A place has come free at "{event.title}" on {when}, and you have moved off the waitlist. '
                    f"You are now going. If you cannot make it, cancel from the event page so the next member can join: {url}"),
    )


@transaction.atomic
def promote_waitlist(event_id):
    event = Event.objects.select_for_update().get(pk=event_id)
    return _promote_locked(event)


@transaction.atomic
def set_rsvp(user, event_id, action="toggle"):
    _member(user)
    if action not in ("join", "cancel", "toggle"):
        raise EventBookingError("Choose join or cancel for this event.")
    event = Event.objects.select_for_update().get(pk=event_id)
    if event.is_formal:
        raise EventBookingError("Manage formal places in Formal Exchange.")
    if event.is_cancelled:
        raise EventBookingError("This event has been cancelled — RSVPs are closed.")
    if event.is_past:
        raise EventBookingError("This event has already happened.")
    if not Event.objects.visible_to(user).filter(pk=event.pk).exists():
        raise PermissionDenied("This event is not available.")

    existing = event.rsvps.filter(user=user).first()
    previous = existing.status if existing else RSVP.Status.CANCELLED
    if action == "toggle":
        action = "cancel" if previous in (RSVP.Status.GOING, RSVP.Status.WAITING) else "join"
    if action == "join" and previous in (RSVP.Status.GOING, RSVP.Status.WAITING):
        return _result(event, existing, False, previous)
    if action == "cancel" and previous == RSVP.Status.CANCELLED:
        return _result(event, existing, False, previous)

    if action == "cancel":
        existing.status = RSVP.Status.CANCELLED
        existing.save(update_fields=["status", "updated_at"])
        tell_promoted(event, _promote_locked(event))
        return _result(event, existing, True, previous)

    # A new arrival never jumps ahead of members already waiting for a place.
    tell_promoted(event, _promote_locked(event))
    status = RSVP.Status.WAITING if event.is_full else RSVP.Status.GOING
    if existing is None:
        existing = RSVP.objects.create(event=event, user=user, status=status)
    else:
        existing.status = status
        existing.save(update_fields=["status", "updated_at"])
    return _result(event, existing, True, previous)


@transaction.atomic
def save_event_edit(user, event_id, data, files=None):
    """Revalidate an edit against the locked event and current reservations."""
    from .forms import EventForm

    _member(user)
    event = Event.objects.select_for_update().get(pk=event_id)
    if not event.can_edit(user):
        raise PermissionDenied("Only the event's organiser or an admin can edit it.")
    if event.is_formal:
        raise EventBookingError("Manage formal dates and places in Formal Exchange.")
    old_start, old_location = event.start, event.location
    form = EventForm(data, files, instance=event, user=user, is_create=False)
    if not form.is_valid():
        return form
    capacity = form.cleaned_data.get("capacity")
    if capacity is not None and capacity < event.going_count:
        form.add_error("capacity", "Capacity cannot be lower than the number of members already going.")
        return form
    form.save()
    if event.location != old_location:
        event.latitude = event.longitude = event.geocoded_at = None
        event.save(update_fields=["latitude", "longitude", "geocoded_at"])
    tell_promoted(event, _promote_locked(event))
    if event.start != old_start and not event.is_past:
        from .views import tell_attendees_moved
        tell_attendees_moved(event, old_start, actor=user)
    return form


@transaction.atomic
def cancel_event(user, event_id):
    _member(user)
    event = Event.objects.select_for_update().get(pk=event_id)
    if not event.can_edit(user):
        raise PermissionDenied("Only the event's organiser or an admin can cancel it.")
    if event.is_formal:
        raise EventBookingError("Manage formal cancellations in Formal Exchange.")
    if not event.is_cancelled:
        event.is_cancelled = True
        event.save(update_fields=["is_cancelled", "updated_at"])
        from .views import tell_attendees_cancelled
        tell_attendees_cancelled(event, actor=user)
    return event
