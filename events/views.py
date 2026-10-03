import calendar as calendar_mod
import csv
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import EventForm, TagPageForm
from .models import RSVP, Category, Event


def _attendees_of(event):
    """Members going to (or waiting for) an event: who to tell of changes."""
    return [
        r.user for r in event.rsvps.filter(
            status__in=[RSVP.Status.GOING, RSVP.Status.WAITING]
        ).select_related("user")
    ]


def tell_attendees_cancelled(event, actor=None):
    from notifications.models import Notification
    from notifications.services import notify

    when = timezone.localtime(event.start).strftime("%a %d %b, %H:%M")
    notify(
        _attendees_of(event), Notification.Kind.EVENT,
        f"{event.title} ({when}) has been cancelled.", event.get_absolute_url(),
        email_subject=f"Cancelled: {event.title}",
        email_body=(
            f"{event.title} on {when} has been cancelled by the organiser. "
            "Sorry for the change of plan."
        ),
        exclude=[actor],
    )


def tell_attendees_moved(event, old_start, actor=None):
    from notifications.models import Notification
    from notifications.services import notify

    new = timezone.localtime(event.start).strftime("%a %d %b, %H:%M")
    old = timezone.localtime(old_start).strftime("%a %d %b, %H:%M")
    notify(
        _attendees_of(event), Notification.Kind.EVENT,
        f"{event.title} has moved from {old} to {new}.", event.get_absolute_url(),
        email_subject=f"New time: {event.title}",
        email_body=f"{event.title} has moved from {old} to {new}. The event page has the details.",
        exclude=[actor],
    )

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

    visible = Event.objects.visible_to(request.user).select_related("category").annotate(
        open_poll_count=Count(
            "polls",
            filter=Q(polls__status="open", polls__closes_at__gt=timezone.now()),
        )
    )
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

    from core.models import SiteConfig
    config = SiteConfig.get()
    grid = [
        {
            "label": config.term_week_label(week[0]),
            "days": [
                {
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "events": by_day.get(day, []),
                }
                for day in week
            ],
        }
        for week in weeks
    ]
    agenda = [
        (day, by_day[day]) for day in sorted(by_day) if day.month == month
    ]
    view = request.GET.get("view", "")
    if view not in ("grid", "list"):
        view = ""

    upcoming = list(
        visible.filter(start__gte=timezone.now())
        .by_promotion()
        .prefetch_related(_going_prefetch(request.user))[:UPCOMING_LIMIT]
    )
    for event in upcoming:
        event.extra_going = max(0, len(event.going_list) - UPCOMING_TOKENS)
        if event.capacity:
            event.spots_left = max(0, event.capacity - len(event.going_list))

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
        "agenda": agenda,
        "view": view,
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
    is_waiting = bool(viewer_rsvp and viewer_rsvp.status == RSVP.Status.WAITING)
    waiting = list(event.waiting) if event.capacity else []
    waiting_position = (
        next((i for i, r in enumerate(waiting, 1) if r.user_id == request.user.pk), None)
        if is_waiting else None
    )
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

    from inbox.policy import can_message
    can_message_host = (
        request.user.is_authenticated
        and request.user != event.effective_host
        and can_message(request.user, event.effective_host)
    )

    from polls.views import card_context
    poll_cards = [
        card_context(poll, request.user)
        for poll in event.polls.select_related("outcome_option").order_by("status", "closes_at")
    ]

    return render(request, "events/detail.html", {
        "poll_cards": poll_cards,
        "can_message_host": can_message_host,
        "poster_scans": event.poster_scans.count() if event.can_edit(request.user) else 0,
        "nav_active": "calendar",
        "event": event,
        "attendees": attendees,
        "going_count": going_count,
        "is_full": is_full,
        "spots_left": spots_left,
        "is_going": is_going,
        "is_waiting": is_waiting,
        "waiting_count": len(waiting),
        "waiting_position": waiting_position,
        "can_rsvp": can_rsvp,
        "can_rate": can_rate,
        "can_edit": event.can_edit(request.user),
        "share_url": share_url,
    })


def _geocode_quietly(event):
    """Look the venue up for the poster map; never let it break or stall a
    save (the poster studio tries again, with more patience, if this fails)."""
    try:
        from posters.geocode import SAVE_TIMEOUT, ensure_geocoded
        ensure_geocoded(event, timeout=SAVE_TIMEOUT)
    except Exception:
        pass


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
        _geocode_quietly(event)
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
    old_start = event.start
    old_location = event.location
    if request.method == "POST" and form.is_valid():
        form.save()  # created_by untouched: admins editing don't take ownership
        if event.location != old_location:
            event.latitude = event.longitude = event.geocoded_at = None
            event.save(update_fields=["latitude", "longitude", "geocoded_at"])
            _geocode_quietly(event)
        if event.start != old_start and not event.is_past:
            tell_attendees_moved(event, old_start, actor=request.user)
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
        tell_attendees_cancelled(event, actor=request.user)
        messages.success(
            request, "Event cancelled. It no longer appears on the calendar."
        )
    return redirect(event)


def _tell_promoted(request, event, promoted):
    """Email people who have just come off the waitlist."""
    from django.core.mail import send_mail

    from notifications.models import Notification
    from notifications.services import notify

    url = request.build_absolute_uri(event.get_absolute_url())
    when = timezone.localtime(event.start).strftime("%A %d %B at %H:%M")
    notify(
        [r.user for r in promoted], Notification.Kind.WAITLIST,
        f"A place opened up: you're now going to {event.title}.",
        event.get_absolute_url(),
    )
    for rsvp in promoted:
        if not rsvp.user.email:
            continue
        body = f"""Hi {rsvp.user.first_name or 'there'},

Good news: a place has come free at "{event.title}" on {when}, and you've
moved off the waitlist. You're now going.

If you can't make it after all, please cancel so the next person can have
the place: {url}

— The MSS portal
"""
        send_mail(
            subject=f"A place has opened up: {event.title}",
            message=body,
            from_email=None,
            recipient_list=[rsvp.user.email],
            fail_silently=True,
        )


@require_POST
@login_required
def rsvp(request, slug):
    """Toggle the viewer's RSVP (never deleting). A full event puts new
    arrivals on the waitlist; a cancellation promotes the first in line."""
    event = get_object_or_404(Event, slug=slug)
    if event.is_cancelled:
        messages.error(request, "This event has been cancelled — RSVPs are closed.")
        return redirect(event)
    if event.is_past:
        messages.error(request, "This event has already happened.")
        return redirect(event)

    existing = event.rsvps.filter(user=request.user).first()
    status = existing.status if existing else RSVP.Status.CANCELLED

    if status == RSVP.Status.GOING:
        existing.status = RSVP.Status.CANCELLED
        existing.save(update_fields=["status", "updated_at"])
        _tell_promoted(request, event, event.promote_waitlist())
        messages.info(request, "No problem — you're no longer marked as going.")
    elif status == RSVP.Status.WAITING:
        existing.status = RSVP.Status.CANCELLED
        existing.save(update_fields=["status", "updated_at"])
        messages.info(request, "You've left the waitlist.")
    else:
        new_status = RSVP.Status.WAITING if event.is_full else RSVP.Status.GOING
        if existing is None:
            RSVP.objects.create(event=event, user=request.user, status=new_status)
        else:
            existing.status = new_status
            existing.save(update_fields=["status", "updated_at"])
        if new_status == RSVP.Status.GOING:
            messages.success(request, f"You're going to “{event.title}”. See you there!")
        else:
            position = event.waiting_count
            messages.success(
                request,
                f"The event is full, so you're on the waitlist (number {position}). "
                "If a place frees up you'll be moved on automatically and emailed.",
            )
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


# --- add to calendar ------------------------------------------------------------


def ics(request, slug):
    """One event as an .ics file, for the viewer's calendar app."""
    from .ics import build_calendar

    event = get_object_or_404(Event.objects.select_related("category"), slug=slug)
    if event.members_only and not request.user.is_authenticated:
        raise Http404("No event found.")
    body = build_calendar(
        [event], "MSS",
        lambda e: request.build_absolute_uri(e.get_absolute_url()),
    )
    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}.ics"'
    return response


# --- duplicate and repeat --------------------------------------------------------

REPEAT_MAX_WEEKS = 12
COPIED_FIELDS = [
    "title", "category", "description", "location", "host", "capacity",
    "members_only", "group_chat_link", "attendee_info", "restaurant",
]


@login_required
def duplicate(request, slug):
    """A create form pre-filled from an existing event, a week later."""
    source = get_object_or_404(Event, slug=slug)
    if not source.can_edit(request.user):
        raise PermissionDenied("Only the event's creator or an admin can duplicate it.")
    shift = datetime.timedelta(days=7)
    initial = {field: getattr(source, field) for field in COPIED_FIELDS}
    initial["start"] = timezone.localtime(source.start + shift)
    initial["end"] = timezone.localtime(source.end + shift) if source.end else None
    initial["is_official"] = source.is_official
    form = EventForm(initial=initial, user=request.user, is_create=True)
    messages.info(request, f"Starting from a copy of “{source.title}”. Nothing is saved until you press Create.")
    return render(request, "events/form.html", {
        "nav_active": "calendar",
        "form": form,
        "event": None,
        "cat_ratings": _ratings_map(),
    })


@require_POST
@login_required
def repeat(request, slug):
    """Create weekly copies of an event, up to REPEAT_MAX_WEEKS."""
    source = get_object_or_404(Event, slug=slug)
    if not source.can_edit(request.user):
        raise PermissionDenied("Only the event's creator or an admin can repeat it.")
    try:
        weeks = int(request.POST.get("weeks", "0"))
    except ValueError:
        weeks = 0
    if not 1 <= weeks <= REPEAT_MAX_WEEKS:
        messages.error(request, f"Pick between 1 and {REPEAT_MAX_WEEKS} weeks.")
        return redirect(source)
    for n in range(1, weeks + 1):
        shift = datetime.timedelta(days=7 * n)
        copy = Event(created_by=request.user, is_official=source.is_official,
                     start=source.start + shift,
                     end=source.end + shift if source.end else None,
                     image=source.image)
        for field in COPIED_FIELDS:
            setattr(copy, field, getattr(source, field))
        copy.save()
    messages.success(
        request,
        f"Created {weeks} more {'copy' if weeks == 1 else 'copies'} of “{source.title}”, one a week.",
    )
    return redirect(source)
