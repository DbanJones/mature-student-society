"""The member dashboard: "what am I doing next?" at a glance.

Login required. Mobile numbers are never shown here — only the member's own
activity. Admin extras appear only for ``is_portal_admin`` users.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from accounts.models import WaitlistRequest, WhatsAppAccessRequest
from events.models import RSVP, Event
from guide.models import GuideRevision
from supper.models import Rating


@login_required
def home(request):
    user = request.user
    now = timezone.now()

    # Upcoming events they're going to (soonest first).
    going_events = (
        Event.objects.filter(
            rsvps__user=user,
            rsvps__status=RSVP.Status.GOING,
            start__gte=now,
            is_cancelled=False,
        )
        .select_related("category")
        .order_by("start")
    )
    going_events = list(going_events)
    next_up = going_events[0] if going_events else None

    # Upcoming events they created ("You're running").
    running_events = (
        user.events_created.filter(start__gte=now, is_cancelled=False)
        .select_related("category")
        .annotate(
            num_going=Count("rsvps", filter=Q(rsvps__status=RSVP.Status.GOING))
        )
        .order_by("start")
    )

    # Past supper-club visits they attended but haven't rated yet.
    unrated_visits = (
        Event.objects.filter(
            rsvps__user=user,
            rsvps__status=RSVP.Status.GOING,
            start__lt=now,
            is_cancelled=False,
            restaurant__isnull=False,
        )
        .exclude(restaurant_ratings__user=user)
        .select_related("restaurant")
        .order_by("-start")
    )

    guide_edit_count = GuideRevision.objects.filter(editor=user).count()

    stats = {
        "attended": RSVP.objects.filter(
            user=user,
            status=RSVP.Status.GOING,
            event__start__lt=now,
            event__is_cancelled=False,
        ).count(),
        "hosted": user.events_created.count(),
        "guide_edits": guide_edit_count,
        "ratings": Rating.objects.filter(user=user).count(),
    }

    context = {
        "nav_active": "dashboard",
        "next_up": next_up,
        "going_events": going_events,
        "running_events": running_events,
        "unrated_visits": unrated_visits,
        "show_guide_invite": guide_edit_count == 0,
        "stats": stats,
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

    return render(request, "dashboard/home.html", context)
