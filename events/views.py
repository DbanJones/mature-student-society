import calendar as calendar_mod
import csv
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import EventForm
from .models import RSVP, Category, Event

UPCOMING_LIMIT = 10
UPCOMING_TOKENS = 6  # initials tokens shown per row before "+N"


def _going_prefetch():
    return Prefetch(
        "rsvps",
        queryset=RSVP.objects.filter(status=RSVP.Status.GOING).select_related("user"),
        to_attr="going_list",
    )


def calendar_view(request):
    """Public month-grid calendar plus a 'coming up' list.

    Anonymous visitors never see members_only events (Event.objects.visible_to)
    and only see attendance counts, never names/initials.
    """
    today = timezone.localdate()
    try:
        year = int(request.GET.get("y", today.year))
        month = int(request.GET.get("m", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    if not (1 <= month <= 12 and 1970 <= year <= 2100):
        year, month = today.year, today.month

    categories = list(Category.objects.all())
    cat_slug = request.GET.get("cat", "")
    active_category = next((c for c in categories if c.slug == cat_slug), None)

    weeks = calendar_mod.Calendar(firstweekday=0).monthdatescalendar(year, month)
    grid_start, grid_end = weeks[0][0], weeks[-1][-1]

    visible = Event.objects.visible_to(request.user).select_related("category")
    if active_category:
        visible = visible.filter(category=active_category)

    month_events = visible.filter(
        start__date__gte=grid_start, start__date__lte=grid_end
    ).order_by("start")
    by_day = {}
    for event in month_events:
        by_day.setdefault(timezone.localtime(event.start).date(), []).append(event)

    grid = [
        [
            {
                "date": day,
                "in_month": day.month == month,
                "is_today": day == today,
                "events": by_day.get(day, []),
            }
            for day in week
        ]
        for week in weeks
    ]

    upcoming = list(
        visible.filter(start__gte=timezone.now())
        .order_by("-is_official", "start")
        .prefetch_related(_going_prefetch())[:UPCOMING_LIMIT]
    )
    for event in upcoming:
        event.extra_going = max(0, len(event.going_list) - UPCOMING_TOKENS)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return render(request, "events/calendar.html", {
        "nav_active": "calendar",
        "grid": grid,
        "month_date": datetime.date(year, month, 1),
        "categories": categories,
        "active_category": active_category,
        "upcoming": upcoming,
        "prev_query": _month_query(prev_year, prev_month, cat_slug),
        "next_query": _month_query(next_year, next_month, cat_slug),
        "today_query": f"?cat={cat_slug}" if active_category else "",
        "is_current_month": (year, month) == (today.year, today.month),
    })


def _month_query(year, month, cat_slug):
    query = f"?y={year}&m={month}"
    return f"{query}&cat={cat_slug}" if cat_slug else query


def detail(request, pk):
    event = get_object_or_404(
        Event.objects.select_related("category", "created_by", "restaurant"), pk=pk
    )
    if event.members_only and not request.user.is_authenticated:
        raise Http404("No event found.")

    attendees = list(event.going)  # select_related("user") in the property
    going_count = len(attendees)
    is_full = event.capacity is not None and going_count >= event.capacity
    spots_left = (
        max(0, event.capacity - going_count) if event.capacity is not None else None
    )

    viewer_rsvp = event.user_rsvp(request.user)
    is_going = bool(viewer_rsvp and viewer_rsvp.status == RSVP.Status.GOING)
    can_rsvp = (
        request.user.is_authenticated and not event.is_cancelled and not event.is_past
    )
    can_rate = (
        is_going
        and event.is_past
        and event.restaurant_id is not None
        and event.category.has_restaurant_ratings
    )

    share_url = ""
    if request.user.is_authenticated:
        share_url = event.whatsapp_share_url(
            request.build_absolute_uri(event.get_absolute_url())
        )

    return render(request, "events/detail.html", {
        "nav_active": "calendar",
        "event": event,
        "attendees": attendees,
        "going_count": going_count,
        "is_full": is_full,
        "spots_left": spots_left,
        "is_going": is_going,
        "can_rsvp": can_rsvp,
        "can_rate": can_rate,
        "can_edit": event.can_edit(request.user),
        "share_url": share_url,
    })


def _ratings_map():
    """Category-id → has_restaurant_ratings, for the form's show/hide JS."""
    return {str(c.pk): c.has_restaurant_ratings for c in Category.objects.all()}


@login_required
def create(request):
    form = EventForm(request.POST or None, user=request.user, is_create=True)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.save()
        messages.success(
            request,
            "Event created — it's on the calendar. Use “Share to WhatsApp” "
            "below to spread the word.",
        )
        return redirect(event)
    return render(request, "events/form.html", {
        "nav_active": "calendar",
        "form": form,
        "event": None,
        "cat_ratings": _ratings_map(),
    })


@login_required
def edit(request, pk):
    event = get_object_or_404(Event.objects.select_related("category"), pk=pk)
    if not event.can_edit(request.user):
        raise PermissionDenied("Only the event's creator or an admin can edit it.")
    form = EventForm(
        request.POST or None, instance=event, user=request.user, is_create=False
    )
    if request.method == "POST" and form.is_valid():
        form.save()  # created_by untouched: admins editing don't take ownership
        messages.success(request, "Event updated.")
        return redirect(event)
    return render(request, "events/form.html", {
        "nav_active": "calendar",
        "form": form,
        "event": event,
        "cat_ratings": _ratings_map(),
    })


@require_POST
@login_required
def cancel(request, pk):
    event = get_object_or_404(Event, pk=pk)
    if not event.can_edit(request.user):
        raise PermissionDenied("Only the event's creator or an admin can cancel it.")
    if not event.is_cancelled:
        event.is_cancelled = True
        event.save(update_fields=["is_cancelled", "updated_at"])
        messages.success(
            request, "Event cancelled. It no longer appears on the calendar."
        )
    return redirect(event)


@require_POST
@login_required
def rsvp(request, pk):
    """Toggle the viewer's RSVP, flipping GOING<->CANCELLED (never deleting)."""
    event = get_object_or_404(Event, pk=pk)
    if event.is_cancelled:
        messages.error(request, "This event has been cancelled — RSVPs are closed.")
        return redirect(event)
    if event.is_past:
        messages.error(request, "This event has already happened.")
        return redirect(event)

    existing = event.rsvps.filter(user=request.user).first()
    turning_on = existing is None or existing.status == RSVP.Status.CANCELLED
    if turning_on and event.is_full:
        messages.error(request, "Sorry — this event is full.")
        return redirect(event)

    if existing is None:
        RSVP.objects.create(event=event, user=request.user, status=RSVP.Status.GOING)
    else:
        existing.status = (
            RSVP.Status.GOING if turning_on else RSVP.Status.CANCELLED
        )
        existing.save(update_fields=["status", "updated_at"])

    if turning_on:
        messages.success(request, f"You're going to “{event.title}”. See you there!")
    else:
        messages.info(request, "No problem — you're no longer marked as going.")
    return redirect(event)


@login_required
def export(request, pk):
    """Attendee names + mobiles for the organiser to build a WhatsApp group.

    Mobile numbers are private: only the event's creator and portal admins may
    see this page or the CSV.
    """
    event = get_object_or_404(Event.objects.select_related("category"), pk=pk)
    if not (request.user == event.created_by or request.user.is_portal_admin):
        raise PermissionDenied("Only the event's creator or an admin can export attendees.")

    attendees = list(event.going.order_by("user__first_name", "user__last_name"))
    rows = [
        {
            "name": rsvp.user.get_full_name() or rsvp.user.username,
            "college": rsvp.user.get_college_display() if rsvp.user.college else "",
            "mobile": rsvp.user.mobile,
        }
        for rsvp in attendees
    ]

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        filename = f"{slugify(event.title) or 'event'}-attendees.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(["Name", "College", "Mobile"])
        for row in rows:
            writer.writerow([row["name"], row["college"], row["mobile"]])
        return response

    paste_lines = "\n".join(
        f"{row['name']} — {row['mobile'] or '—'}" for row in rows
    )
    return render(request, "events/export.html", {
        "nav_active": "calendar",
        "event": event,
        "rows": rows,
        "paste_lines": paste_lines,
    })
