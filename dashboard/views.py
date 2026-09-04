"""The member dashboard: "what am I doing next?" at a glance.

Login required. Mobile numbers are never shown here — only the member's own
activity. Admin extras appear only for ``is_portal_admin`` users.
"""

import json

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import WaitlistRequest, WhatsAppAccessRequest
from events.models import RSVP, Event
from guide.models import GuideRevision
from supper.models import Rating

from .models import KeepyUppyScore


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

    # Super events: society headliners, pinned to everyone's dashboard
    # whether or not they've RSVP'd yet.
    super_events = list(
        Event.objects.visible_to(user)
        .filter(is_super=True, start__gte=now)
        .select_related("category")
        .order_by("start")[:5]
    )
    going_ids = {e.pk for e in going_events}
    for event in super_events:
        event.already_going = event.pk in going_ids

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
        "super_events": super_events,
        "owned_tags": user.tags_owned.all(),
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
