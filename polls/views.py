"""Polls on events (venue, date, volunteers) and general polls from the
committee. Voting is for logged-in members; managing a poll follows the
event's edit permission, or admin status for a general poll."""

import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from events.models import Event

from .forms import PollForm
from .models import Poll, PollOption, PollVote

EVENT_KINDS = [Poll.Kind.VENUE, Poll.Kind.DATE, Poll.Kind.VOLUNTEERS, Poll.Kind.GENERAL]
GENERAL_KINDS = [Poll.Kind.GENERAL, Poll.Kind.VOLUNTEERS]

KIND_BLURBS = {
    Poll.Kind.VENUE: "Members pick between places. When the poll closes, the winner becomes the event's location.",
    Poll.Kind.DATE: "Members pick between dates and times. The winner becomes the event's start.",
    Poll.Kind.VOLUNTEERS: "Slots with a number of people needed. Members sign up; you get a roster with contact details.",
    Poll.Kind.GENERAL: "Any other question, with the options you choose.",
}


def _poll(pk):
    poll = get_object_or_404(
        Poll.objects.select_related("event", "event__category", "created_by"), pk=pk
    )
    poll.resolve_if_due()
    return poll


def _back(poll):
    return redirect(poll.event) if poll.event_id else redirect(poll)


def card_context(poll, user):
    """Everything the poll card include needs for one viewer."""
    poll.resolve_if_due()
    options = poll.results()
    return {
        "poll": poll,
        "options": options,
        "choices": poll.choices_of(user),
        "can_manage": poll.can_manage(user),
        "show_results": poll.can_see_results(user),
        "has_voted": any(o.pk in poll.choices_of(user) for o in options),
    }


def _event_for_new_poll(request, slug):
    if slug is None:
        if not request.user.is_portal_admin:
            raise PermissionDenied("Only admins can start a general poll.")
        return None
    event = get_object_or_404(Event, slug=slug)
    if not event.can_edit(request.user):
        raise PermissionDenied("Only the event's organiser or an admin can add a poll.")
    return event


@login_required
def choose(request, slug=None):
    """Pick what kind of poll to start."""
    event = _event_for_new_poll(request, slug)
    kinds = EVENT_KINDS if event else GENERAL_KINDS
    return render(request, "polls/choose.html", {
        "nav_active": "calendar" if event else "panel",
        "event": event,
        "kinds": [(k, Poll.Kind(k).label, KIND_BLURBS[k]) for k in kinds],
    })


@login_required
def create(request, kind, slug=None):
    event = _event_for_new_poll(request, slug)
    if kind not in (EVENT_KINDS if event else GENERAL_KINDS):
        raise Http404("No such kind of poll.")
    form = PollForm(request.POST or None, kind=kind, event=event)
    if request.method == "POST" and form.is_valid():
        form.instance.created_by = request.user
        poll = form.save()
        if event is not None:
            from events.models import RSVP
            from notifications.models import Notification
            from notifications.services import notify

            going = [
                r.user for r in event.rsvps.filter(
                    status=RSVP.Status.GOING
                ).select_related("user")
            ]
            notify(
                going, Notification.Kind.POLL,
                f"New poll on {event.title}: {poll.question}",
                f"{event.get_absolute_url()}#poll-{poll.pk}",
                exclude=[request.user],
            )
        messages.success(request, "Poll started — members can vote until it closes.")
        return _back(poll)
    return render(request, "polls/form.html", {
        "nav_active": "calendar" if event else "panel",
        "event": event,
        "kind": kind,
        "kind_label": Poll.Kind(kind).label,
        "blurb": KIND_BLURBS[kind],
        "form": form,
    })


def detail(request, pk):
    poll = _poll(pk)
    if poll.event_id:
        if poll.event.members_only and not request.user.is_authenticated:
            raise Http404("No poll found.")
    elif not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return render(request, "polls/detail.html", {
        "nav_active": "calendar" if poll.event_id else "dashboard",
        "card": card_context(poll, request.user),
    })


@login_required
@require_POST
def vote(request, pk):
    poll = _poll(pk)
    if not poll.is_open:
        messages.error(request, "This poll has closed.")
        return _back(poll)
    option_ids = request.POST.getlist("option")
    chosen = list(poll.options.filter(pk__in=option_ids))
    if not chosen:
        messages.error(request, "Pick an option first.")
        return _back(poll)
    if not poll.allow_multiple and len(chosen) > 1:
        messages.error(request, "Pick just one option.")
        return _back(poll)
    if any(o.is_opt_out for o in chosen) and len(chosen) > 1:
        messages.error(request, "“None of these” can't be combined with a date.")
        return _back(poll)
    already = poll.choices_of(request.user)
    if poll.is_rota:
        for option in chosen:
            if option.pk not in already and option.is_full:
                messages.error(request, f"“{option.label}” is already full.")
                return _back(poll)
    poll.votes.filter(user=request.user).delete()
    PollVote.objects.bulk_create([
        PollVote(poll=poll, option=option, user=request.user) for option in chosen
    ])
    if poll.is_rota:
        messages.success(request, "Thank you — you're signed up.")
    elif already:
        messages.success(request, "Vote updated.")
    else:
        messages.success(request, "Thanks for voting.")
    return _back(poll)


@login_required
@require_POST
def close(request, pk):
    """Close now, optionally naming the winner (needed on a tie)."""
    poll = _poll(pk)
    if not poll.can_manage(request.user):
        raise PermissionDenied("Only the organiser or an admin can close this poll.")
    option = None
    if request.POST.get("option"):
        option = get_object_or_404(PollOption, pk=request.POST["option"], poll=poll)
    if poll.status == Poll.Status.CLOSED and option is None:
        messages.info(request, "This poll is already closed.")
        return _back(poll)
    winner = poll.close(by=request.user, option=option)
    if poll.needs_decision:
        messages.warning(
            request, "The poll closed on a tie — pick the winner below to set the event."
        )
    elif winner is not None and poll.sets_event:
        messages.success(request, f"Poll closed: “{winner.label}” wins and the event is updated.")
    elif winner is not None:
        messages.success(request, f"Poll closed: “{winner.label}” wins.")
    else:
        messages.info(request, "Poll closed with no votes.")
    return _back(poll)


@login_required
@require_POST
def delete(request, pk):
    poll = _poll(pk)
    if not poll.can_manage(request.user):
        raise PermissionDenied("Only the organiser or an admin can delete this poll.")
    target = poll.event if poll.event_id else None
    poll.delete()
    messages.success(request, "Poll deleted.")
    return redirect(target) if target else redirect("dashboard:home")


@login_required
def roster(request, pk):
    """Who has signed up for which slot, with contact details for the host.
    Same privacy rule as an event's attendee export."""
    poll = _poll(pk)
    if not poll.is_rota:
        raise Http404("Not a volunteer rota.")
    if not poll.can_manage(request.user):
        raise PermissionDenied("Only the organiser or an admin can see the roster.")
    slots = []
    for option in poll.options.prefetch_related("votes__user"):
        people = [v.user for v in option.votes.all()]
        slots.append({"option": option, "people": people, "short": (
            max(0, option.capacity - len(people)) if option.capacity else None
        )})
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="{slugify(poll.question) or "rota"}-volunteers.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(["Slot", "Name", "College", "Mobile", "Email"])
        for slot in slots:
            for person in slot["people"]:
                writer.writerow([
                    slot["option"].label, person.get_full_name() or person.username,
                    person.get_college_display() if person.college else "",
                    person.mobile, person.email,
                ])
        return response
    paste = "\n".join(
        f"{slot['option'].label}: " + (
            ", ".join(p.get_full_name() or p.username for p in slot["people"]) or "nobody yet"
        )
        for slot in slots
    )
    return render(request, "polls/roster.html", {
        "nav_active": "calendar" if poll.event_id else "panel",
        "poll": poll,
        "slots": slots,
        "paste": paste,
    })


def index(request):
    """Every poll a visitor may see: open ones to vote in, then closed ones
    with their results. Logged-out visitors see polls on public events."""
    from django.db.models import Count, Q

    from events.models import Event

    user = request.user
    for poll in Poll.objects.due().select_related("event"):
        poll.close()
    polls = Poll.objects.select_related("event", "outcome_option").annotate(vote_count=Count("votes", distinct=True))
    visible_events = Event.objects.visible_to(user if user.is_authenticated else None)
    if user.is_authenticated:
        polls = polls.filter(Q(event__isnull=True) | Q(event__in=visible_events))
    else:
        polls = polls.filter(event__in=visible_events)  # no general polls, no hidden events
    polls = list(polls.order_by("-closes_at")[:60])
    voted = set(PollVote.objects.filter(user=user).values_list("poll_id", flat=True)) if user.is_authenticated else set()
    for poll in polls:
        poll.voted = poll.pk in voted
        poll.results_ok = poll.can_see_results(user)
    return render(request, "polls/index.html", {
        "nav_active": "polls",
        "open_polls": sorted((p for p in polls if p.status == Poll.Status.OPEN), key=lambda p: p.closes_at),
        "closed_polls": [p for p in polls if p.status != Poll.Status.OPEN],
    })
