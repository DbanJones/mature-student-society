import calendar as calendar_mod
import csv
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import EventForm, TagPageForm
from .models import RSVP, Category, Event

UPCOMING_LIMIT = 10
UPCOMING_TOKENS = 6  # initials tokens shown per row before "+N"


def _going_prefetch(viewer=None):
    """RSVPs for display. Shadow-banned attendees are hidden from everyone
    except themselves and admins."""
    qs = RSVP.objects.filter(status=RSVP.Status.GOING).select_related("user")
    if viewer is None or not viewer.is_authenticated:
        qs = qs.filter(user__is_shadow_banned=False)
    elif not viewer.is_portal_admin:
        qs = qs.filter(Q(user__is_shadow_banned=False) | Q(user=viewer))
    return Prefetch("rsvps", queryset=qs, to_attr="going_list")


def _visible_attendees(event, viewer):
    """The GOING list, minus shadow-banned members (unless the viewer is an
    admin or is that member)."""
    rsvps = event.going
    if viewer.is_authenticated and viewer.is_portal_admin:
        return list(rsvps)
    return [
        r for r in rsvps
        if not r.user.is_shadow_banned
        or (viewer.is_authenticated and r.user == viewer)
    ]


def calendar_view(request):
    """Public month-grid calendar plus a 'coming up' list.

    Supports free-text search (?q=) across title, description, location and
    tag, and tag filtering (?cat=). Anonymous visitors never see members_only
    events (Event.objects.visible_to) and only see attendance counts, never
    names/initials.
    """
    today = timezone.localdate()
    try:
        year = int(request.GET.get("y", today.year))
        month = int(request.GET.get("m", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    if not (1 <= month <= 12 and 1970 <= year <= 2100):
        year, month = today.year, today.month

    q = request.GET.get("q", "").strip()
    categories = list(Category.objects.all())
    # Additive tag filter: ?cat= may repeat (and old comma links still work).
    requested = []
    for value in request.GET.getlist("cat"):
        requested += [s for s in value.split(",") if s]
    active_slugs = [c.slug for c in categories if c.slug in set(requested)]
    active_categories = [c for c in categories if c.slug in active_slugs]

    weeks = calendar_mod.Calendar(firstweekday=0).monthdatescalendar(year, month)
    grid_start, grid_end = weeks[0][0], weeks[-1][-1]

    visible = Event.objects.visible_to(request.user).select_related("category")
    if active_slugs:
        visible = visible.filter(category__slug__in=active_slugs)
    if q:
        visible = visible.search(q)

    # Deliberately a plain datetime range, not start__date__gte/lte: the
    # __date lookup asks the database to convert start (stored in UTC) into
    # Europe/London before comparing, which on MySQL compiles to
    # DATE(CONVERT_TZ(start, 'UTC', 'Europe/London')). CONVERT_TZ silently
    # returns NULL — matching nothing — unless the server's mysql.time_zone*
    # tables have been loaded, which shared hosts (e.g. SRCF) typically don't
    # grant permission for. Comparing against aware datetime bounds instead
    # needs no timezone conversion in the database at all.
    grid_start_dt = timezone.make_aware(
        datetime.datetime.combine(grid_start, datetime.time.min)
    )
    grid_end_dt = timezone.make_aware(
        datetime.datetime.combine(
            grid_end + datetime.timedelta(days=1), datetime.time.min
        )
    )

    # Official events lead each day's cell, then the rest chronologically.
    month_events = visible.filter(
        start__gte=grid_start_dt, start__lt=grid_end_dt
    ).by_promotion()
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
        .by_promotion()
        .prefetch_related(_going_prefetch(request.user))[:UPCOMING_LIMIT]
    )
    for event in upcoming:
        event.extra_going = max(0, len(event.going_list) - UPCOMING_TOKENS)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    # Each pill toggles its own tag in/out of the active set.
    pills = []
    for cat in categories:
        toggled = [s for s in active_slugs if s != cat.slug]
        if cat.slug not in active_slugs:
            toggled = active_slugs + [cat.slug]
        pills.append({
            "cat": cat,
            "active": cat.slug in active_slugs,
            "query": _month_query(year, month, toggled, q),
        })

    return render(request, "events/calendar.html", {
        "nav_active": "calendar",
        "grid": grid,
        "month_date": datetime.date(year, month, 1),
        "pills": pills,
        "active_categories": active_categories,
        "active_slugs": active_slugs,
        "upcoming": upcoming,
        "q": q,
        "all_query": _month_query(year, month, [], q),
        "prev_query": _month_query(prev_year, prev_month, active_slugs, q),
        "next_query": _month_query(next_year, next_month, active_slugs, q),
        "today_query": _month_query(today.year, today.month, active_slugs, q),
        "is_current_month": (year, month) == (today.year, today.month),
    })


def _month_query(year, month, cat_slugs, q=""):
    from urllib.parse import urlencode
    params = [("y", year), ("m", month)]
    params += [("cat", slug) for slug in cat_slugs]
    if q:
        params.append(("q", q))
    return "?" + urlencode(params)


def detail_by_pk(request, pk):
    """Permanent redirect from the old /events/<id>/ URLs to the slug form."""
    event = get_object_or_404(Event, pk=pk)
    return redirect(event, permanent=True)


def detail(request, slug):
    event = get_object_or_404(
        Event.objects.select_related(
            "category", "created_by", "host", "restaurant"
        ),
        slug=slug,
    )
    if event.members_only and not request.user.is_authenticated:
        raise Http404("No event found.")
    if (
        event.created_by.is_shadow_banned
        and request.user != event.created_by
        and not (request.user.is_authenticated and request.user.is_portal_admin)
    ):
        raise Http404("No event found.")

    attendees = _visible_attendees(event, request.user)
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
        and event.has_started
        and not event.is_cancelled
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
    form = EventForm(
        request.POST or None, request.FILES or None,
        user=request.user, is_create=True,
    )
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        if not event.host:
            event.host = request.user
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
def edit(request, slug):
    event = get_object_or_404(Event.objects.select_related("category"), slug=slug)
    if not event.can_edit(request.user):
        raise PermissionDenied("Only the event's creator or an admin can edit it.")
    form = EventForm(
        request.POST or None, request.FILES or None,
        instance=event, user=request.user, is_create=False,
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
def cancel(request, slug):
    event = get_object_or_404(Event, slug=slug)
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
def rsvp(request, slug):
    """Toggle the viewer's RSVP, flipping GOING<->CANCELLED (never deleting)."""
    event = get_object_or_404(Event, slug=slug)
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
def export(request, slug):
    """Attendee names + mobiles for the organiser to build a WhatsApp group.

    Mobile numbers are private: only the event's creator/host and portal
    admins may see this page, the CSV, or the contacts file. The ``vcf``
    format bundles every attendee into one contacts file, so they can be
    imported (and then added to a WhatsApp group) in one go.
    """
    event = get_object_or_404(Event.objects.select_related("category"), slug=slug)
    if not event.can_edit(request.user):
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

    if request.GET.get("format") == "vcf":
        # One vCard per attendee with a number. Import the file into your
        # phone's contacts, then add everyone to the WhatsApp group at once.
        cards = []
        for row in rows:
            if not row["mobile"]:
                continue
            last, _, first = row["name"].rpartition(" ")
            cards += [
                "BEGIN:VCARD",
                "VERSION:3.0",
                f"N:{first};{last};;;" if last else f"N:{row['name']};;;;",
                f"FN:{row['name']} (MSS)",
                f"TEL;TYPE=CELL:{row['mobile']}",
                "END:VCARD",
            ]
        response = HttpResponse(
            "\r\n".join(cards) + "\r\n", content_type="text/vcard; charset=utf-8"
        )
        filename = f"{slugify(event.title) or 'event'}-attendees.vcf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    paste_lines = "\n".join(
        f"{row['name']} — {row['mobile'] or '—'}" for row in rows
    )
    numbers = ", ".join(row["mobile"] for row in rows if row["mobile"])
    return render(request, "events/export.html", {
        "nav_active": "calendar",
        "event": event,
        "rows": rows,
        "paste_lines": paste_lines,
        "numbers": numbers,
        "with_mobile_count": sum(1 for row in rows if row["mobile"]),
    })


# --- tag subpages -----------------------------------------------------------------


def tag_page(request, slug):
    """PUBLIC. A tag's own page: blurb, owners, and its upcoming events."""
    tag = get_object_or_404(Category, slug=slug)
    upcoming = list(
        Event.objects.visible_to(request.user)
        .filter(category=tag, start__gte=timezone.now())
        .select_related("category")
        .prefetch_related(_going_prefetch(request.user))
        .by_promotion()[:UPCOMING_LIMIT]
    )
    for event in upcoming:
        event.extra_going = max(0, len(event.going_list) - UPCOMING_TOKENS)
    past = (
        Event.objects.visible_to(request.user)
        .filter(category=tag, start__lt=timezone.now())
        .order_by("-start")[:5]
    )
    return render(request, "events/tag_page.html", {
        "nav_active": "calendar",
        "tag": tag,
        "owners": tag.owners.filter(is_banned=False),
        "upcoming": upcoming,
        "past": past,
        "can_edit": tag.can_edit_page(request.user),
    })


@login_required
def tag_edit(request, slug):
    """Tag owners (who need not be site admins) and society admins can edit
    the tag's page content and blurb."""
    tag = get_object_or_404(Category, slug=slug)
    if not tag.can_edit_page(request.user):
        raise PermissionDenied("Only this tag's owners or an admin can edit its page.")
    form = TagPageForm(request.POST or None, instance=tag)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"The {tag.name} page has been updated.")
        return redirect(tag)
    return render(request, "events/tag_form.html", {
        "nav_active": "calendar",
        "tag": tag,
        "form": form,
    })
