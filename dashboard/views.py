"""The member dashboard: "what am I doing next?" at a glance.

Login required. Mobile numbers are never shown here — only the member's own
activity. Admin extras appear only for ``is_portal_admin`` users.
"""

import json

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import User

from accounts.models import WaitlistRequest, WhatsAppAccessRequest
from events.models import RSVP, Event
from formals.models import FormalBooking, FormalExchange
from guide.models import GuideRevision
from polls.models import Poll, PollVote
from supper.models import Rating

from .models import KeepyUppyScore


def _hosted_by(user, prefix=""):
    return Q(**{prefix + "host": user}) | Q(**{prefix + "host__isnull": True, prefix + "created_by": user})


@login_required
def home(request):
    user = request.user
    now = timezone.now()

    # Upcoming events they're going to (soonest first).
    going_events = (
        Event.objects.filter(
            start__gte=now,
            is_cancelled=False,
        )
        .filter(
            Q(rsvps__user=user, rsvps__status=RSVP.Status.GOING)
            | Q(formal_listing__bookings__guest=user,
                formal_listing__bookings__status__in=["reserved", "attended"])
        )
        .distinct().select_related("category", "formal_listing")
        .order_by("start")
    )
    going_events = list(going_events)
    next_up = going_events[0] if going_events else None
    waiting_events = list(
        Event.objects.filter(
            rsvps__user=user, rsvps__status=RSVP.Status.WAITING,
            start__gte=now, is_cancelled=False,
        ).select_related("category", "formal_listing").order_by("start")
    )

    # The organiser may differ from the member who originally created an event.
    running_events = list(
        Event.objects.filter(_hosted_by(user), start__gte=now, is_cancelled=False)
        .select_related("category", "formal_listing")
        .annotate(
            num_going=Count("rsvps", filter=Q(rsvps__status=RSVP.Status.GOING))
        )
        .order_by("start")
    )
    for event in running_events:
        if event.is_formal:
            event.num_going = event.going_count

    diary_by_id = {event.pk: event for event in going_events + running_events}
    diary_events = sorted(diary_by_id.values(), key=lambda event: (event.start, event.pk))
    next_up = diary_events[0] if diary_events else None
    if next_up:
        next_up.viewer_is_host = any(event.pk == next_up.pk for event in running_events)

    formal_requests = FormalBooking.objects.filter(
        _hosted_by(user, "listing__event__"), listing__event__is_cancelled=False,
        listing__event__start__gt=now, listing__booking_closes_at__gt=now, status="requested",
    ).count()
    formal_attendance = FormalBooking.objects.filter(
        Q(guest=user, guest_confirmed=False) | (_hosted_by(user, "listing__event__") & Q(host_confirmed=False))
        | (Q(status="no_show") & (Q(guest=user) | _hosted_by(user, "listing__event__"))),
        status__in=["reserved", "no_show"], listing__event__is_cancelled=False,
    ).filter(
        Q(listing__event__end__lte=now)
        | Q(listing__event__end__isnull=True, listing__event__start__lte=now)
    ).count()
    formal_promises = FormalExchange.objects.filter(
        Q(creditor=user) | Q(debtor=user),
        status__in=["conditional", "owed", "disputed"],
    ).count()
    formal_plans = list(FormalBooking.objects.filter(
        guest=user, listing__event__is_cancelled=False, listing__event__start__gt=now,
    ).filter(
        Q(status="reserved")
        | Q(status="requested", listing__booking_closes_at__gt=now)
        | Q(status="held", return_proposal__status="pending", return_proposal__expires_at__gt=now)
    ).select_related("listing__event", "exchange", "return_proposal__exchange").order_by("listing__event__start", "pk")[:5])

    # Past supper-club visits they attended but haven't rated yet.
    unrated_visits = list(
        Event.objects.filter(
            rsvps__user=user,
            rsvps__status=RSVP.Status.GOING,
            start__lt=now,
            is_cancelled=False,
            restaurant__isnull=False,
        )
        .exclude(restaurant_ratings__user=user)
        .select_related("restaurant", "formal_listing")
        .order_by("-start")
    )

    # Super events: society headliners, pinned to everyone's dashboard
    # whether or not they've RSVP'd yet.
    super_events = list(
        Event.objects.visible_to(user)
        .filter(is_super=True, start__gte=now)
        .select_related("category", "formal_listing")
        .order_by("start")[:5]
    )
    going_ids = {e.pk for e in going_events}
    for event in super_events:
        event.already_going = event.pk in going_ids

    guide_edit_count = GuideRevision.objects.filter(editor=user).count()

    # New-member checklist: what makes a profile useful to other members.
    has_rsvp = (
        RSVP.objects.filter(user=user, status=RSVP.Status.GOING).exists()
        or FormalBooking.objects.filter(
            guest=user, status__in=["reserved", "attended"],
            listing__event__is_cancelled=False,
        ).exists()
    )
    checklist = [
        {"done": bool(user.photo), "label": "Add a profile photo", "url": "/accounts/profile/"},
        {"done": bool(user.talk_to_me_about), "label": "Fill in “talk to me about…”", "url": "/accounts/profile/"},
        {"done": not user.can_view_whatsapp_link, "label": "Join the WhatsApp community", "url": "/accounts/whatsapp/"},
        {"done": has_rsvp, "label": "RSVP to your first event", "url": "/events/"},
    ]
    checklist_done = sum(1 for item in checklist if item["done"])
    show_checklist = checklist_done < len(checklist)

    # Polls this member can still vote in, and rota slots they've taken.
    polls_waiting = list(Poll.objects.for_dashboard(user)[:5])
    from django.db.models import Count as _Count

    from surveys.models import Survey

    for due in Survey.objects.due():
        due.resolve_if_due()
    surveys_waiting = list(Survey.objects.for_dashboard(user)[:5])
    surveys_run = list(
        user.surveys_run.exclude(status=Survey.Status.CLOSED)
        .annotate(answer_count=_Count("participations", distinct=True))
        .order_by("-created_at")[:5]
    )
    volunteering = list(
        PollVote.objects.filter(user=user, poll__kind=Poll.Kind.VOLUNTEERS)
        .filter(Q(poll__event__isnull=True) | Q(poll__event__start__gte=now))
        .select_related("option", "poll", "poll__event", "poll__event__formal_listing")
        .order_by("poll__event__start", "poll__closes_at")
    )

    stats = {
        "attended": RSVP.objects.filter(
            user=user,
            status=RSVP.Status.GOING,
            event__start__lt=now,
            event__is_cancelled=False,
        ).count() + sum(booking.lead_attended for booking in FormalBooking.objects.filter(
            guest=user, status="attended", listing__event__is_cancelled=False,
        ).only("status", "places", "host_attendance", "guest_attendance").iterator(chunk_size=200)),
        "hosted": Event.objects.filter(_hosted_by(user), is_cancelled=False).filter(
            Q(end__lte=now) | Q(end__isnull=True, start__lte=now)
        ).count(),
        "guide_edits": guide_edit_count,
        "ratings": Rating.objects.filter(user=user).count(),
    }

    from core.models import SiteConfig, visible_to

    testimonials_tab = SiteConfig.get().tab_visibility_for("testimonials")
    testimonials_visible = visible_to(testimonials_tab, user) or (testimonials_tab != "hidden" and user.is_portal_admin)
    show_testimonial_invite = testimonials_visible and not user.testimonials.exists()
    context = {
        "nav_active": "dashboard",
        "formal_requests": formal_requests,
        "formal_attendance": formal_attendance,
        "formal_promises": formal_promises,
        "formal_plans": formal_plans,
        "formal_hosting": [event for event in running_events if event.is_formal][:3],
        "next_up": next_up,
        "super_events": super_events,
        "owned_tags": user.tags_owned.all(),
        "editable_pages": user.site_pages_editable.all(),
        "show_testimonial_invite": show_testimonial_invite,
        "polls_waiting": polls_waiting,
        "volunteering": volunteering,
        "surveys_waiting": surveys_waiting,
        "surveys_run": surveys_run,
        "waiting_events": waiting_events,
        "checklist": checklist,
        "checklist_done": checklist_done,
        "show_checklist": show_checklist,
        "feed_url": request.build_absolute_uri(
            f"/me/calendar.ics?token={user.get_calendar_token()}"
        ),
        "going_events": going_events,
        "diary_events": diary_events,
        "running_events": running_events,
        "ordinary_running_events": [event for event in running_events if not event.is_formal],
        "unrated_visits": unrated_visits,
        "show_guide_invite": guide_edit_count == 0,
        "stats": stats,
        "attention_count": (
            sum(1 for item in checklist if not item["done"]) + len(surveys_waiting) + len(polls_waiting)
            + len(unrated_visits) + int(guide_edit_count == 0) + int(show_testimonial_invite)
            + int(bool(formal_requests)) + int(bool(formal_attendance))
        ),
    }

    if user.is_portal_admin:
        context["pending_approvals"] = (
            WaitlistRequest.objects.filter(
                status=WaitlistRequest.Status.PENDING
            ).count()
            + WhatsAppAccessRequest.objects.filter(
                status=WhatsAppAccessRequest.Status.OPEN
            ).count()
        )
        context["attention_count"] += int(bool(context["pending_approvals"]))

    return render(request, "dashboard/home.html", context)


# --- keepy-uppy: the hidden football (type b-a-l-l on any page) -------------------

MAX_PLAUSIBLE_SCORE = 10000


@login_required
@require_http_methods(["GET", "POST"])
def game_scores(request):
    """GET: leaderboard JSON. POST {"score": n}: record a personal best."""
    if request.method == "POST":
        try:
            score = int(json.loads(request.body or b"{}").get("score", 0))
        except (ValueError, TypeError, json.JSONDecodeError):
            return JsonResponse({"error": "bad score"}, status=400)
        score = max(0, min(score, MAX_PLAUSIBLE_SCORE))
        row, _ = KeepyUppyScore.objects.get_or_create(user=request.user)
        if score > row.best:
            row.best = score
            row.save(update_fields=["best", "updated_at"])

    top = list(
        KeepyUppyScore.objects.filter(
            best__gt=0, user__is_banned=False, user__is_shadow_banned=False
        )
        .select_related("user")
        .order_by("-best", "updated_at")[:10]
    )
    mine = KeepyUppyScore.objects.filter(user=request.user).first()
    return JsonResponse({
        "best": mine.best if mine else 0,
        "leaderboard": [
            {
                "name": (
                    f"{row.user.first_name} {row.user.last_name[:1]}."
                    if row.user.first_name else row.user.username
                ),
                "score": row.best,
                "me": row.user_id == request.user.pk,
            }
            for row in top
        ],
    })


# --- personal calendar feed ------------------------------------------------------


def calendar_feed(request):
    """The member's events as an iCalendar feed their calendar app can
    subscribe to. The token in the URL is the only authentication, so it
    can be reset from the dashboard if it ever leaks."""
    from events.ics import build_calendar

    token = request.GET.get("token", "")
    user = User.objects.filter(calendar_token=token, is_banned=False, is_active=True).first() if token else None
    if user is None:
        raise Http404("No calendar found.")
    since = timezone.now() - datetime.timedelta(days=60)
    cancelled_formal_ids = list(FormalBooking.objects.filter(
        guest=user, status="cancelled", reserved_at__isnull=False,
        listing__event__start__gte=since,
    ).values_list("listing__event_id", flat=True))
    events = (
        Event.objects.filter(start__gte=since)
        .filter(
            Q(rsvps__user=user, rsvps__status=RSVP.Status.GOING)
            | Q(formal_listing__bookings__guest=user,
                formal_listing__bookings__status__in=["reserved", "attended"])
            | Q(pk__in=cancelled_formal_ids)
            | Q(host=user) | Q(created_by=user)
        )
        .distinct().select_related("category", "formal_listing").order_by("start")
    )
    # Keep the same UID with a cancellation for a released guest reservation.
    # Mutate only these in-memory feed objects, never the shared Event row.
    for event in events:
        if event.pk in cancelled_formal_ids and event.effective_host != user:
            event.is_cancelled = True
    body = build_calendar(
        events, "MSS: my events",
        lambda e: request.build_absolute_uri(e.get_absolute_url()),
    )
    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@login_required
@require_POST
def calendar_token_reset(request):
    request.user.reset_calendar_token()
    messages.success(request, "Your calendar link has been reset. Re-subscribe with the new one.")
    return redirect("dashboard:home")
