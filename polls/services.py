"""Atomic voting, volunteer places and one-time poll outcomes."""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
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


def _apply_outcome_locked(poll, by=None):
    """Write a venue or date poll's winner onto its event, log it, and tell
    everyone going. Idempotent: a poll is applied at most once."""
    from panel.models import AuditLog

    event, option = poll.event, poll.outcome_option
    if event is None or option is None or poll.applied_at is not None:
        return
    if event.is_formal or event.is_cancelled:
        return
    if option.poll_id != poll.pk or option.is_opt_out:
        raise PollError("Choose a valid outcome from this poll.")
    if poll.kind == poll.Kind.VENUE:
        event.location = option.label
        event.latitude = event.longitude = event.geocoded_at = None
        event.save(update_fields=["location", "latitude", "longitude", "geocoded_at", "updated_at"])
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

    url = getattr(settings, "SITE_URL", "").rstrip("/") + event.get_absolute_url()
    notify(
        [r.user for r in event.rsvps.filter(status=RSVP.Status.GOING).select_related("user")]
        + [event.effective_host],
        Notification.Kind.POLL, f"{event.title}: {what}", event.get_absolute_url(),
        email_subject=f"{event.title}: {what}",
        email_body=f'The poll “{poll.question}” has closed.\n\n{what}\n\nThe event page has the details: {url}',
    )


class PollError(ValidationError):
    pass


def _member(user):
    if not user or not user.is_authenticated or not user.is_active or user.is_banned:
        raise PermissionDenied("An active member account is required.")


def _locked_poll(poll_id):
    """Event before poll: outcome edits share the event RSVP/edit lock."""
    from events.models import Event
    from .models import Poll

    event_id = Poll.objects.values_list("event_id", flat=True).get(pk=poll_id)
    event = Event.objects.select_for_update().get(pk=event_id) if event_id else None
    poll = Poll.objects.select_for_update().get(pk=poll_id)
    if event is not None:
        poll.event = event
    return poll


@transaction.atomic
def cast_vote(user, poll_id, option_ids):
    from events.models import Event
    from .models import PollVote

    _member(user)
    poll = _locked_poll(poll_id)
    if poll.event_id and (poll.event.is_formal or poll.event.is_cancelled or not Event.objects.visible_to(user).filter(pk=poll.event_id).exists()):
        raise PollError("This poll is not accepting votes.")
    if not poll.is_open:
        raise PollError("This poll has closed.")
    try:
        if not isinstance(option_ids, (list, tuple)) or any(isinstance(value, bool) or not isinstance(value, (str, int)) for value in option_ids):
            raise ValueError
        ids = {int(value) for value in option_ids}
        if any(not 1 <= value <= 9223372036854775807 for value in ids):
            raise ValueError
    except (ValueError, TypeError):
        raise PollError("Choose valid options from this poll.")
    chosen = list(poll.options.filter(pk__in=ids))
    if not chosen:
        raise PollError("Pick an option first.")
    if len(chosen) != len(ids):
        raise PollError("Choose options from this poll only.")
    if not poll.allow_multiple and len(chosen) > 1:
        raise PollError("Pick just one option.")
    if any(option.is_opt_out for option in chosen) and len(chosen) > 1:
        raise PollError("‘None of these’ cannot be combined with another option.")
    previous = poll.choices_of(user)
    if previous == ids:
        return poll, bool(previous)
    if poll.is_rota:
        for option in chosen:
            if option.pk not in previous and option.is_full:
                raise PollError(f"‘{option.label}’ is already full.")
    poll.votes.filter(user=user).delete()
    PollVote.objects.bulk_create([PollVote(poll=poll, option=option, user=user) for option in chosen])
    return poll, bool(previous)


def _close_locked(poll, by=None, option_id=None):
    from .models import PollOption

    if by is not None:
        _member(by)
        if not poll.can_manage(by):
            raise PermissionDenied("Only the organiser or an admin can close this poll.")
    if poll.event_id and poll.event.is_formal:
        raise PollError("Formal dates and places are managed in Formal Exchange.")
    if poll.status == poll.Status.CLOSED and not poll.needs_decision:
        if option_id is not None and option_id != poll.outcome_option_id:
            raise PollError("This result is already final. Start a new poll to make a different decision.")
        return poll.outcome_option
    if poll.status == poll.Status.CLOSED and option_id is None:
        return poll.outcome_option
    winner = None
    if option_id is not None:
        winner = PollOption.objects.filter(pk=option_id, poll=poll).first()
        if winner is None or winner.is_opt_out:
            raise PollError("Choose a valid outcome from this poll.")
    poll.status = poll.Status.CLOSED
    poll.needs_decision = False
    if winner is None:
        leaders = poll.leaders()
        if len(leaders) == 1:
            winner = leaders[0]
        elif len(leaders) > 1 and poll.sets_event:
            poll.needs_decision = True
    poll.outcome_option = winner
    poll.save(update_fields=["status", "outcome_option", "needs_decision"])
    if winner is not None and poll.sets_event:
        _apply_outcome_locked(poll, by)
    return winner


@transaction.atomic
def close_poll(poll_id, by=None, option_id=None):
    poll = _locked_poll(poll_id)
    winner = _close_locked(poll, by, option_id)
    return poll, winner


@transaction.atomic
def apply_outcome(poll, by=None):
    """Always reload the authoritative outcome under the same locks."""
    locked = _locked_poll(poll.pk)
    _apply_outcome_locked(locked, by)
    poll.refresh_from_db()


@transaction.atomic
def delete_poll(user, poll_id):
    _member(user)
    poll = _locked_poll(poll_id)
    if not poll.can_manage(user):
        raise PermissionDenied("Only the organiser or an admin can delete this poll.")
    if poll.event_id and poll.event.is_formal:
        raise PollError("Formal dates and places are managed in Formal Exchange.")
    event = poll.event
    poll.delete()
    return event
